import argparse
import multiprocessing as mp
import os
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")

from config import Config


ProgressCallback = Callable[[dict[str, Any]], None]


class AsrJobCanceled(Exception):
    pass


def configure_runtime() -> None:
    """在导入 vLLM 前固定 multiprocessing 启动方式。"""
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass


def parse_args() -> Config:
    defaults = Config()
    parser = argparse.ArgumentParser(description="Qwen3-ASR 长视频转录工具")
    parser.add_argument("-i", "--input", required=True, help="输入视频/音频文件路径")
    parser.add_argument("-o", "--output-dir", default=defaults.output_dir, help="输出目录 (默认: ./output)")
    parser.add_argument("--srt", dest="save_srt", action="store_true", default=defaults.save_srt, help="输出 SRT 字幕文件 (默认开启)")
    parser.add_argument("--no-srt", dest="save_srt", action="store_false", help="不输出 SRT 字幕文件")
    parser.add_argument("--model", default=defaults.model, help="ASR 主模型名称或本地路径")
    parser.add_argument("--aligner-model", default=defaults.forced_aligner_model, help="ForcedAligner 模型名称或本地路径；设为空字符串可关闭")
    parser.add_argument("--timestamps", dest="return_time_stamps", action="store_true", default=defaults.return_time_stamps, help="启用 ForcedAligner 时间戳 (默认开启)")
    parser.add_argument("--no-timestamps", dest="return_time_stamps", action="store_false", help="关闭 ForcedAligner 时间戳，SRT 回退到 VAD 段落级时间")
    parser.add_argument("--language", default=defaults.language, help=f"语言 (默认: {defaults.language or '自动检测'}；auto=自动检测)")
    parser.add_argument("--backend", choices=("auto", "vllm", "transformers"), default=defaults.backend, help="推理后端 (默认: auto；Windows 自动使用 transformers)")
    parser.add_argument("--device-map", default=defaults.device_map, help="transformers 后端设备映射 (默认: cuda:0)")
    parser.add_argument("--dtype", choices=("bfloat16", "float16", "float32"), default=defaults.dtype, help="transformers/ForcedAligner dtype (默认: bfloat16)")
    parser.add_argument("--gpu-mem", type=float, default=defaults.gpu_memory_utilization, help="vLLM GPU 显存利用率 (默认: 0.5；transformers 后端不使用)")
    parser.add_argument("--batch-size", type=int, default=defaults.max_inference_batch_size, help="最大推理批大小 (默认: 2，显存不足时可降为 1)")
    parser.add_argument("--max-tokens", type=int, default=defaults.max_new_tokens, help="最大生成 token 数 (默认: 2048)")
    parser.add_argument("--segment-duration", type=int, default=defaults.segment_duration, help="VAD 目标切片长度/秒 (默认: 60)")
    parser.add_argument("--max-segment", type=int, default=defaults.max_segment_duration, help="VAD 切片上限/秒 (默认: 120)")
    parser.add_argument("--cache-dir", default=defaults.cache_dir, help="音频/VAD 预处理缓存目录 (默认: ./cache)")
    parser.add_argument("--no-cache", dest="use_cache", action="store_false", default=defaults.use_cache, help="关闭音频/VAD 预处理缓存")
    parser.add_argument("--refresh-cache", action="store_true", default=defaults.refresh_cache, help="忽略并重建当前输入的音频/VAD 缓存")
    parser.add_argument("--srt-max-chars", type=int, default=defaults.srt_max_chars, help="单条 SRT 字幕最大字符数 (默认: 42)")
    parser.add_argument("--srt-max-duration", type=float, default=defaults.srt_max_duration, help="单条 SRT 字幕最长秒数 (默认: 6.0)")

    args = parser.parse_args()
    return Config(
        input_file=args.input,
        output_dir=args.output_dir,
        save_srt=args.save_srt,
        model=args.model,
        forced_aligner_model=args.aligner_model or None,
        return_time_stamps=args.return_time_stamps,
        language=None if str(args.language or "").lower() == "auto" else args.language,
        backend=args.backend,
        device_map=args.device_map,
        dtype=args.dtype,
        gpu_memory_utilization=args.gpu_mem,
        max_inference_batch_size=args.batch_size,
        max_new_tokens=args.max_tokens,
        segment_duration=args.segment_duration,
        max_segment_duration=args.max_segment,
        use_cache=args.use_cache,
        cache_dir=args.cache_dir,
        refresh_cache=args.refresh_cache,
        srt_max_chars=args.srt_max_chars,
        srt_max_duration=args.srt_max_duration,
    )


def get_input_stem(input_file: str) -> str:
    parsed = urlparse(input_file)
    if len(parsed.scheme) > 1 and parsed.scheme.lower() not in {"", "file"}:
        path = unquote(parsed.path.rstrip("/"))
        stem = os.path.splitext(os.path.basename(path))[0]
        return stem or "transcription"
    return os.path.splitext(os.path.basename(input_file))[0]


def _is_cancel_requested(cancel_token: object | None) -> bool:
    return bool(cancel_token is not None and getattr(cancel_token, "is_canceled", False))


def _raise_if_canceled(cancel_token: object | None) -> None:
    if _is_cancel_requested(cancel_token):
        raise AsrJobCanceled("ASR 任务已取消")


def run_asr_job(
    config: Config,
    progress_callback: ProgressCallback | None = None,
    cancel_token: object | None = None,
    parallel_model_load: bool = True,
) -> dict[str, str]:
    from audio import load_audio
    from cache import clear_preprocess_cache, get_cache_dir, load_preprocess_cache, save_preprocess_cache
    from output import save_srt, save_txt
    from transcribe import init_model, transcribe_segments
    from vad import process_vad

    input_name = get_input_stem(config.input_file)
    use_model_time_stamps = config.return_time_stamps and bool(config.forced_aligner_model)
    t_start = time.time()
    artifacts: dict[str, str] = {}

    def notify(
        event: str,
        stage: str,
        message: str | None = None,
        progress: dict[str, Any] | None = None,
        artifact: dict[str, str] | None = None,
    ) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "event": event,
                "stage": stage,
                "message": message,
                "progress": progress,
                "artifact": artifact,
            }
        )

    def emit_log(stage: str, message: str) -> None:
        print(message)
        notify("log", stage, message)

    # --- 流水线并行：CPU 端 (音频+VAD) 与 GPU 端 (模型加载) 同时进行 ---
    segments_result = [None]
    model_result = [None]
    pipeline_errors: list[BaseException] = []
    cpu_done = threading.Event()
    gpu_done = threading.Event()

    def raise_first_pipeline_error() -> None:
        if not pipeline_errors:
            return
        if isinstance(pipeline_errors[0], AsrJobCanceled):
            raise pipeline_errors[0]
        raise RuntimeError(f"流水线执行失败: {pipeline_errors[0]}") from pipeline_errors[0]

    def cpu_pipeline():
        try:
            _raise_if_canceled(cancel_token)
            if config.use_cache and config.refresh_cache:
                clear_preprocess_cache(config)
                emit_log("cache_refresh", f"[CPU] 已清理当前输入缓存: {get_cache_dir(config)}")

            if config.use_cache:
                notify("status", "cache_check", "[CPU] 检查预处理缓存...")
                cached_segments = load_preprocess_cache(config)
                if cached_segments is not None:
                    emit_log("cache_hit", f"[CPU] 命中预处理缓存: {get_cache_dir(config)}")
                    emit_log("cpu_done", f"[CPU] 切片完成: {len(cached_segments)} 段 (cache)")
                    segments_result[0] = cached_segments
                    return
                emit_log("cache_miss", f"[CPU] 未命中预处理缓存，将生成: {get_cache_dir(config)}")

            _raise_if_canceled(cancel_token)
            emit_log("audio_loading", f"[CPU] 加载音频: {config.input_file}")
            wav = load_audio(config.input_file)
            duration = len(wav) / 16000
            emit_log("audio_loaded", f"[CPU] 音频时长: {duration:.1f}s")
            _raise_if_canceled(cancel_token)
            emit_log("vad_running", f"[CPU] VAD 切片中 (目标 {config.segment_duration}s, 上限 {config.max_segment_duration}s)...")
            segs = process_vad(wav, config.segment_duration, config.max_segment_duration)
            emit_log("cpu_done", f"[CPU] 切片完成: {len(segs)} 段")
            if config.use_cache:
                cache_dir = save_preprocess_cache(config, wav, segs)
                emit_log("cache_save", f"[CPU] 预处理缓存已保存: {cache_dir}")
            segments_result[0] = segs
        except BaseException as exc:
            pipeline_errors.append(exc)
        finally:
            cpu_done.set()

    def gpu_pipeline():
        try:
            _raise_if_canceled(cancel_token)
            emit_log("model_loading", f"[GPU] 请求后端: {config.backend}")
            if use_model_time_stamps:
                emit_log("model_loading", f"[GPU] 加载 ASR 模型: {config.model}")
                emit_log("model_loading", f"[GPU] 加载对齐模型: {config.forced_aligner_model}")
            else:
                emit_log("model_loading", f"[GPU] 加载 ASR 模型: {config.model}")
            model = init_model(config)
            emit_log("model_ready", "[GPU] 模型加载完成")
            model_result[0] = model
        except BaseException as exc:
            pipeline_errors.append(exc)
        finally:
            gpu_done.set()

    if parallel_model_load:
        t_cpu = threading.Thread(target=cpu_pipeline)
        t_gpu = threading.Thread(target=gpu_pipeline)
        t_cpu.start()
        t_gpu.start()

        # 等待两条流水线完成，打印等待状态
        while t_cpu.is_alive() or t_gpu.is_alive():
            both_alive = t_cpu.is_alive() and t_gpu.is_alive()
            if not both_alive:
                if t_cpu.is_alive() and gpu_done.is_set():
                    emit_log("waiting", "[等待] GPU 就绪，等待 CPU 端 (音频加载/VAD) 完成...")
                elif t_gpu.is_alive() and cpu_done.is_set():
                    emit_log("waiting", "[等待] CPU 就绪，等待 GPU 端 (模型加载) 完成...")
                # 等剩余线程结束
                t_cpu.join()
                t_gpu.join()
                break
            time.sleep(0.5)

        t_cpu.join()
        t_gpu.join()
    else:
        emit_log("cpu_preflight", "[CPU] Web 模式先完成音频/VAD 预处理，再加载模型。")
        cpu_pipeline()
        raise_first_pipeline_error()
        _raise_if_canceled(cancel_token)
        gpu_pipeline()

    raise_first_pipeline_error()

    _raise_if_canceled(cancel_token)

    segments = segments_result[0]
    model = model_result[0]

    # --- 批量推理 ---
    emit_log("transcribing", f"[ASR] 开始推理 ({len(segments)} 段)...")
    t_infer = time.time()
    texts, time_stamps = transcribe_segments(
        model, segments, config.language,
        batch_size=config.max_inference_batch_size,
        return_time_stamps=use_model_time_stamps,
        progress_callback=progress_callback,
        cancel_token=cancel_token,
    )
    emit_log("transcribed", f"[ASR] 推理完成, 耗时 {time.time() - t_infer:.1f}s")

    _raise_if_canceled(cancel_token)

    # --- 输出 ---
    os.makedirs(config.output_dir, exist_ok=True)
    txt_path = os.path.join(config.output_dir, f"{input_name}.txt")
    save_txt(texts, txt_path)
    artifacts["transcript"] = txt_path
    emit_log("output", f"[输出] 文本已保存: {txt_path}")
    notify("artifact", "output", f"[输出] 文本已保存: {txt_path}", artifact={"name": "transcript", "kind": "txt", "path": txt_path})

    if config.save_srt:
        _raise_if_canceled(cancel_token)
        srt_path = os.path.join(config.output_dir, f"{input_name}.srt")
        seg_ranges = [(s, e) for s, e, _ in segments]
        save_srt(
            texts,
            seg_ranges,
            srt_path,
            time_stamps=time_stamps,
            max_caption_chars=config.srt_max_chars,
            max_caption_duration=config.srt_max_duration,
        )
        artifacts["subtitle"] = srt_path
        emit_log("output", f"[输出] 字幕已保存: {srt_path}")
        notify("artifact", "output", f"[输出] 字幕已保存: {srt_path}", artifact={"name": "subtitle", "kind": "srt", "path": srt_path})

    emit_log("finished", f"[完成] 总耗时 {time.time() - t_start:.1f}s")
    return artifacts


def run(config: Config) -> None:
    run_asr_job(config)


if __name__ == "__main__":
    configure_runtime()
    config = parse_args()
    run(config)
