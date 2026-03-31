import argparse
import os
import threading
import time

from config import Config


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Qwen3-ASR 长视频转录工具")
    parser.add_argument("-i", "--input", required=True, help="输入视频/音频文件路径")
    parser.add_argument("-o", "--output-dir", default="./output", help="输出目录 (默认: ./output)")
    parser.add_argument("--srt", action="store_true", help="同时输出 SRT 字幕文件")
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B", help="ASR 模型名称或本地路径")
    parser.add_argument("--language", default=None, help="语言 (默认: 自动检测)")
    parser.add_argument("--gpu-mem", type=float, default=0.7, help="GPU 显存利用率 (默认: 0.7)")
    parser.add_argument("--batch-size", type=int, default=32, help="最大推理批大小 (默认: 32)")
    parser.add_argument("--max-tokens", type=int, default=4096, help="最大生成 token 数 (默认: 4096)")
    parser.add_argument("--segment-duration", type=int, default=120, help="VAD 目标切片长度/秒 (默认: 120)")
    parser.add_argument("--max-segment", type=int, default=180, help="VAD 切片上限/秒 (默认: 180)")

    args = parser.parse_args()
    return Config(
        input_file=args.input,
        output_dir=args.output_dir,
        save_srt=args.srt,
        model=args.model,
        language=args.language,
        gpu_memory_utilization=args.gpu_mem,
        max_inference_batch_size=args.batch_size,
        max_new_tokens=args.max_tokens,
        segment_duration=args.segment_duration,
        max_segment_duration=args.max_segment,
    )


def run(config: Config) -> None:
    from audio import load_audio
    from output import save_srt, save_txt
    from transcribe import init_model, transcribe_segments
    from vad import process_vad

    input_name = os.path.splitext(os.path.basename(config.input_file))[0]
    t_start = time.time()

    # --- 流水线并行：CPU 端 (音频+VAD) 与 GPU 端 (模型加载) 同时进行 ---
    segments_result = [None]
    model_result = [None]

    def cpu_pipeline():
        print(f"[CPU] 加载音频: {config.input_file}")
        wav = load_audio(config.input_file)
        duration = len(wav) / 16000
        print(f"[CPU] 音频时长: {duration:.1f}s")
        print(f"[CPU] VAD 切片中 (目标 {config.segment_duration}s, 上限 {config.max_segment_duration}s)...")
        segs = process_vad(wav, config.segment_duration, config.max_segment_duration)
        print(f"[CPU] 切片完成: {len(segs)} 段")
        segments_result[0] = segs

    def gpu_pipeline():
        print(f"[GPU] 加载模型: {config.model}")
        model = init_model(config)
        print("[GPU] 模型加载完成")
        model_result[0] = model

    t_cpu = threading.Thread(target=cpu_pipeline)
    t_gpu = threading.Thread(target=gpu_pipeline)
    t_cpu.start()
    t_gpu.start()
    t_cpu.join()
    t_gpu.join()

    segments = segments_result[0]
    model = model_result[0]

    # --- 批量推理 ---
    print(f"[ASR] 开始推理 ({len(segments)} 段)...")
    t_infer = time.time()
    texts = transcribe_segments(model, segments, config.language)
    print(f"[ASR] 推理完成, 耗时 {time.time() - t_infer:.1f}s")

    # --- 输出 ---
    os.makedirs(config.output_dir, exist_ok=True)
    txt_path = os.path.join(config.output_dir, f"{input_name}.txt")
    save_txt(texts, txt_path)
    print(f"[输出] 文本已保存: {txt_path}")

    if config.save_srt:
        srt_path = os.path.join(config.output_dir, f"{input_name}.srt")
        seg_ranges = [(s, e) for s, e, _ in segments]
        save_srt(texts, seg_ranges, srt_path)
        print(f"[输出] 字幕已保存: {srt_path}")

    print(f"[完成] 总耗时 {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    config = parse_args()
    run(config)
