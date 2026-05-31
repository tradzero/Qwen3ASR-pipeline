from dataclasses import dataclass


DEFAULT_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
DEFAULT_FORCED_ALIGNER_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"
DEFAULT_ASR_LANGUAGE = "Japanese"
DEFAULT_LADA_CLI_PATH = r"D:\lada\lada-cli.exe"
DEFAULT_DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_DEEPSEEK_REASONING_EFFORT = "high"
DEEPSEEK_V4_CONTEXT_TOKENS = 1_000_000
DEEPSEEK_V4_MAX_OUTPUT_TOKENS = 384_000
DEFAULT_DEEPSEEK_MAX_TOKENS = DEEPSEEK_V4_MAX_OUTPUT_TOKENS
DEFAULT_DEEPSEEK_CHUNK_CHARS = 200_000
DEFAULT_DEEPSEEK_CONTEXT_CHARS = 12_000
DEFAULT_TRANSLATION_PROMPT_TEMPLATE = """你是专业字幕翻译助手。请将下面 SRT 字幕正文翻译为{target_language}。

要求：
1. 只翻译每个 <SEG n> 标签中的字幕正文。
2. 必须保留 <SEG n> 与 </SEG n> 标签，且不要增删或重排分段。
3. 不要输出解释、标题、注释、Markdown 代码块或 SRT 时间轴。
4. 如果正文包含人名、专有名词、音效标记或 HTML 标签，请尽量保留语义和结构。
5. 尊重原始内容， 不要对内容进行过度审查改写。

字幕正文：
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
    language: str | None = DEFAULT_ASR_LANGUAGE  # None = 自动检测
    backend: str = "auto"  # auto / vllm / transformers
    device_map: str = "cuda:0"
    dtype: str = "bfloat16"

    # 推理引擎
    gpu_memory_utilization: float = 0.5
    max_inference_batch_size: int = 2
    max_new_tokens: int = 2048

    # VAD 切片
    segment_duration: int = 60  # 目标切片长度（秒）
    max_segment_duration: int = 120  # 切片上限（秒）

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
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    process_priority: str = "AboveNormal"

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
    warmup_on_startup: bool = True
    warmup_vad: bool = True
    warmup_asr: bool = True

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
    deepseek_reasoning_effort: str = DEFAULT_DEEPSEEK_REASONING_EFFORT
    deepseek_context_tokens: int = DEEPSEEK_V4_CONTEXT_TOKENS
    deepseek_max_output_tokens: int = DEEPSEEK_V4_MAX_OUTPUT_TOKENS
    deepseek_max_tokens: int = DEFAULT_DEEPSEEK_MAX_TOKENS
    deepseek_target_language: str = "简体中文"
    deepseek_chunk_chars: int = DEFAULT_DEEPSEEK_CHUNK_CHARS
    deepseek_context_chars: int = DEFAULT_DEEPSEEK_CONTEXT_CHARS
    deepseek_max_srt_size_mb: int = 20
    deepseek_prompt_template: str = DEFAULT_TRANSLATION_PROMPT_TEMPLATE
