from dataclasses import dataclass


DEFAULT_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
DEFAULT_FORCED_ALIGNER_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
DEFAULT_LADA_CLI_PATH = r"D:\lada\lada-cli.exe"
DEFAULT_DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_TRANSLATION_PROMPT_TEMPLATE = """你是专业字幕翻译助手。请将下面文本翻译为{target_language}。

要求：
1. 保持原有段落顺序和换行。
2. 不要添加解释、标题或注释。
3. 如果原文包含时间戳、编号或说话人标签，请原样保留。

原文：
{text}"""


@dataclass
class Config:
    # 输入输出
    input_file: str = ""
    output_dir: str = "./output"
    save_srt: bool = True

    # 模型
    model: str = DEFAULT_ASR_MODEL
    forced_aligner_model: str | None = DEFAULT_FORCED_ALIGNER_MODEL
    return_time_stamps: bool = True
    language: str | None = None  # None = 自动检测
    backend: str = "auto"  # auto / vllm / transformers
    device_map: str = "cuda:0"
    dtype: str = "bfloat16"

    # 推理引擎
    gpu_memory_utilization: float = 0.5
    max_inference_batch_size: int = 1
    max_new_tokens: int = 2048

    # VAD 切片
    segment_duration: int = 60  # 目标切片长度（秒）
    max_segment_duration: int = 90  # 切片上限（秒）

    # 预处理缓存
    use_cache: bool = True
    cache_dir: str = "./cache"
    refresh_cache: bool = False

    # SRT 聚合
    srt_max_chars: int = 42
    srt_max_duration: float = 6.0


@dataclass
class WebSettings:
    # Web 服务
    host: str = "127.0.0.1"
    port: int = 7860

    # 运行时目录
    upload_dir: str = "./uploads"
    job_dir: str = "./jobs"
    artifact_dir: str = "./output/web"
    max_upload_size_mb: int = 10240
    recent_log_lines: int = 500

    # Web ASR 本机策略
    asr_cache_dir: str = "./cache"
    asr_device_map: str = "cuda:0"
    asr_dtype: str = "bfloat16"

    # LADA 去码
    lada_cli_path: str = DEFAULT_LADA_CLI_PATH
    lada_output_dir: str = "./output/lada"
    lada_encoding_preset: str | None = None
    lada_device: str | None = None
    lada_fp16: bool | None = None
    lada_max_clip_length: int | None = None

    # DeepSeek 翻译
    deepseek_api_base: str = DEFAULT_DEEPSEEK_API_BASE
    deepseek_chat_completion_path: str = "/chat/completions"
    deepseek_api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    deepseek_temperature: float = 0.2
    deepseek_max_tokens: int = 4096
    deepseek_target_language: str = "简体中文"
    deepseek_chunk_chars: int = 6000
    deepseek_prompt_template: str = DEFAULT_TRANSLATION_PROMPT_TEMPLATE
