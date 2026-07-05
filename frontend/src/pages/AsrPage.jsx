import { useEffect, useState } from "react";

import {
  cancelJob,
  createAsrJob,
  getDefaults,
} from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";
import { PathDropInput } from "../components/PathDropInput.jsx";

const ASR_LANGUAGE_OPTIONS = [
  { value: "Japanese", label: "日语" },
  { value: "Chinese", label: "中文" },
  { value: "English", label: "英语" },
  { value: "Korean", label: "韩语" },
  { value: "", label: "自动检测" },
];

const initialForm = {
  input_file: "",
  model: "Qwen/Qwen3-ASR-1.7B",
  language: "Japanese",
  backend: "auto",
  gpu_memory_utilization: 0.5,
  max_inference_batch_size: 8,
  max_new_tokens: 1024,
  segment_duration: 45,
  max_segment_duration: 75,
  save_srt: true,
  use_cache: true,
  refresh_cache: false,
  return_time_stamps: true,
  forced_aligner_model: "Qwen/Qwen3-ForcedAligner-0.6B",
  srt_max_chars: 42,
  srt_max_duration: 6,
  handoff_lada: false,
  handoff_lada_encoding_preset: "",
  handoff_lada_device: "",
  handoff_lada_fp16_mode: "default",
  handoff_lada_max_clip_length: "",
  handoff_translate: false,
  handoff_translate_target_language: "简体中文",
  handoff_translate_model: "deepseek-v4-flash",
  handoff_translate_reasoning_effort: "high",
  handoff_translate_max_tokens: 384000,
  handoff_translate_chunk_chars: 200000,
  handoff_translate_max_blocks_per_chunk: 80,
  handoff_translate_debug_io: false,
};
const MAX_LADA_CLIP_LENGTH = 1000;

function buildForm(defaults) {
  const asr = defaults?.asr ?? {};
  const web = defaults?.web ?? {};
  return {
    ...initialForm,
    ...asr,
    input_file: "",
    language: asr.language ?? initialForm.language,
    forced_aligner_model: asr.forced_aligner_model ?? "",
    handoff_lada_encoding_preset: web.lada_encoding_preset ?? "",
    handoff_lada_device: web.lada_device ?? "",
    handoff_lada_fp16_mode: web.lada_fp16 == null ? "default" : String(Boolean(web.lada_fp16)),
    handoff_lada_max_clip_length: web.lada_max_clip_length ?? "",
    handoff_translate_target_language: web.deepseek_target_language ?? initialForm.handoff_translate_target_language,
    handoff_translate_model: web.deepseek_model ?? initialForm.handoff_translate_model,
    handoff_translate_reasoning_effort: web.deepseek_reasoning_effort ?? initialForm.handoff_translate_reasoning_effort,
    handoff_translate_max_tokens: web.deepseek_max_tokens ?? initialForm.handoff_translate_max_tokens,
    handoff_translate_chunk_chars: web.deepseek_chunk_chars ?? initialForm.handoff_translate_chunk_chars,
    handoff_translate_max_blocks_per_chunk: web.deepseek_max_blocks_per_chunk ?? initialForm.handoff_translate_max_blocks_per_chunk,
    handoff_translate_debug_io: web.deepseek_debug_io ?? initialForm.handoff_translate_debug_io,
  };
}

function numberValue(value) {
  return Number(value);
}

function optionalNumber(value) {
  if (value === "" || value == null) {
    return null;
  }
  return Number(value);
}

function isRemoteInput(value) {
  return /^https?:\/\//i.test(value.trim());
}

export function AsrPage({ activeJob, setActiveJob, onTranslateArtifact, streamMode, jobError, setJobError }) {
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    getDefaults()
      .then((defaults) => {
        if (alive) {
          setForm(buildForm(defaults));
        }
      })
      .catch((nextError) => {
        if (alive) {
          setError(nextError.message);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  const updateField = (name, value) => {
    setForm((current) => {
      const next = { ...current, [name]: value };
      if (name === "handoff_lada" && !value) {
        next.handoff_translate = false;
      }
      if (name === "save_srt" && !value) {
        next.handoff_translate = false;
      }
      return next;
    });
  };

  const submitJob = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setJobError?.("");

    try {
      const inputFile = form.input_file.trim();
      if (!inputFile) {
        throw new Error("请输入服务端可访问的本机路径、盘符路径或 UNC/NAS 路径。");
      }
      if (form.handoff_lada && isRemoteInput(inputFile)) {
        throw new Error("自动转交 LADA 需要本机或 UNC/NAS 路径，远程 URL 不支持。");
      }
      if (form.handoff_lada && form.handoff_translate && !form.save_srt) {
        throw new Error("LADA 后翻译需要 ASR 输出 SRT，请先开启 SRT。");
      }

      const translateHandoffEnabled = Boolean(form.handoff_lada && form.handoff_translate && form.save_srt);

      const request = {
        input_file: inputFile,
        model: form.model,
        language: form.language.trim() || null,
        backend: form.backend,
        gpu_memory_utilization: numberValue(form.gpu_memory_utilization),
        max_inference_batch_size: numberValue(form.max_inference_batch_size),
        max_new_tokens: numberValue(form.max_new_tokens),
        segment_duration: numberValue(form.segment_duration),
        max_segment_duration: numberValue(form.max_segment_duration),
        save_srt: Boolean(form.save_srt),
        use_cache: Boolean(form.use_cache),
        refresh_cache: Boolean(form.refresh_cache),
        return_time_stamps: Boolean(form.return_time_stamps),
        forced_aligner_model: form.forced_aligner_model.trim() || null,
        srt_max_chars: numberValue(form.srt_max_chars),
        srt_max_duration: numberValue(form.srt_max_duration),
        handoff: {
          lada: {
            enabled: Boolean(form.handoff_lada),
            encoding_preset: form.handoff_lada_encoding_preset.trim() || null,
            device: form.handoff_lada_device.trim() || null,
            fp16: form.handoff_lada_fp16_mode === "default" ? null : form.handoff_lada_fp16_mode === "true",
            max_clip_length: optionalNumber(form.handoff_lada_max_clip_length),
            translate: {
              enabled: translateHandoffEnabled,
              target_language: form.handoff_translate_target_language.trim() || null,
              model: form.handoff_translate_model.trim() || null,
              reasoning_effort: form.handoff_translate_reasoning_effort,
              max_tokens: optionalNumber(form.handoff_translate_max_tokens),
              chunk_chars: optionalNumber(form.handoff_translate_chunk_chars),
              max_blocks_per_chunk: optionalNumber(form.handoff_translate_max_blocks_per_chunk),
              debug_io: Boolean(form.handoff_translate_debug_io),
            },
          },
        },
      };
      const job = await createAsrJob(request);
      setActiveJob(job);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSubmitting(false);
    }
  };

  const requestCancel = async () => {
    if (!activeJob) {
      return;
    }
    setSubmitting(true);
    try {
      const response = await cancelJob(activeJob.job_id);
      setActiveJob(response.job);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSubmitting(false);
    }
  };

  const requestTranslate = async (job, artifact) => {
    if (!onTranslateArtifact) {
      return;
    }
    setSubmitting(true);
    setError("");
    setJobError?.("");
    try {
      await onTranslateArtifact(job, artifact);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page-grid">
      <Panel title="ASR 任务">
        <form className="task-form" onSubmit={submitJob}>
          <PathDropInput
            label="输入路径"
            onChange={(value) => updateField("input_file", value)}
            placeholder="D:\\media\\video.mp4 或 \\\\NAS\\media\\video.mp4"
            value={form.input_file}
          />

          <div className="form-grid">
            <label>
              模型
              <input onChange={(event) => updateField("model", event.target.value)} value={form.model} />
            </label>
            <label>
              语言
              <select onChange={(event) => updateField("language", event.target.value)} value={form.language}>
                {ASR_LANGUAGE_OPTIONS.map((option) => (
                  <option key={option.value || "auto"} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              后端
              <select onChange={(event) => updateField("backend", event.target.value)} value={form.backend}>
                <option value="auto">auto</option>
                <option value="transformers">transformers</option>
                <option value="vllm">vllm</option>
              </select>
            </label>
            <label>
              batch
              <input min="1" onChange={(event) => updateField("max_inference_batch_size", event.target.value)} type="number" value={form.max_inference_batch_size} />
            </label>
            <label>
              segment 秒
              <input min="1" onChange={(event) => updateField("segment_duration", event.target.value)} type="number" value={form.segment_duration} />
            </label>
            <label>
              max segment 秒
              <input min="1" onChange={(event) => updateField("max_segment_duration", event.target.value)} type="number" value={form.max_segment_duration} />
            </label>
          </div>

          <div className="toggle-grid">
            <label><input checked={form.save_srt} onChange={(event) => updateField("save_srt", event.target.checked)} type="checkbox" />SRT</label>
            <label><input checked={form.return_time_stamps} onChange={(event) => updateField("return_time_stamps", event.target.checked)} type="checkbox" />时间戳</label>
            <label><input checked={form.use_cache} onChange={(event) => updateField("use_cache", event.target.checked)} type="checkbox" />缓存</label>
            <label><input checked={form.refresh_cache} onChange={(event) => updateField("refresh_cache", event.target.checked)} type="checkbox" />刷新缓存</label>
          </div>

          <div className="handoff-section">
            <div className="toggle-grid">
              <label><input checked={form.handoff_lada} onChange={(event) => updateField("handoff_lada", event.target.checked)} type="checkbox" />完成后 LADA</label>
              <label><input checked={form.handoff_translate} disabled={!form.handoff_lada || !form.save_srt} onChange={(event) => updateField("handoff_translate", event.target.checked)} type="checkbox" />LADA 后翻译</label>
            </div>

            {form.handoff_lada ? (
              <div className="form-grid">
                <label>
                  LADA 编码预设
                  <input onChange={(event) => updateField("handoff_lada_encoding_preset", event.target.value)} value={form.handoff_lada_encoding_preset} />
                </label>
                <label>
                  LADA 设备
                  <input onChange={(event) => updateField("handoff_lada_device", event.target.value)} value={form.handoff_lada_device} />
                </label>
                <label>
                  LADA fp16
                  <select onChange={(event) => updateField("handoff_lada_fp16_mode", event.target.value)} value={form.handoff_lada_fp16_mode}>
                    <option value="default">默认</option>
                    <option value="true">开启</option>
                    <option value="false">关闭</option>
                  </select>
                </label>
                <label>
                  LADA max clip
                  <input max={MAX_LADA_CLIP_LENGTH} min="1" onChange={(event) => updateField("handoff_lada_max_clip_length", event.target.value)} type="number" value={form.handoff_lada_max_clip_length} />
                </label>
              </div>
            ) : null}

            {form.handoff_lada && form.handoff_translate && form.save_srt ? (
              <div className="form-grid">
                <label>
                  翻译目标语言
                  <input onChange={(event) => updateField("handoff_translate_target_language", event.target.value)} value={form.handoff_translate_target_language} />
                </label>
                <label>
                  翻译模型
                  <input onChange={(event) => updateField("handoff_translate_model", event.target.value)} value={form.handoff_translate_model} />
                </label>
                <label>
                  思考强度
                  <select onChange={(event) => updateField("handoff_translate_reasoning_effort", event.target.value)} value={form.handoff_translate_reasoning_effort}>
                    <option value="high">high</option>
                    <option value="max">max</option>
                  </select>
                </label>
                <label>
                  翻译 max blocks
                  <input max="500" min="1" onChange={(event) => updateField("handoff_translate_max_blocks_per_chunk", event.target.value)} type="number" value={form.handoff_translate_max_blocks_per_chunk} />
                </label>
                <label><input checked={form.handoff_translate_debug_io} onChange={(event) => updateField("handoff_translate_debug_io", event.target.checked)} type="checkbox" />翻译 debug I/O</label>
              </div>
            ) : null}
          </div>

          {error ? <div className="error-box">{error}</div> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "处理中" : "启动 ASR"}
          </button>
        </form>
      </Panel>
      <Panel title="任务进度">
        <div className="stream-line">{streamMode === "live" ? "SSE live" : streamMode === "polling" ? "polling" : "idle"}</div>
        {jobError ? <div className="error-box">{jobError}</div> : null}
        <JobDetail job={activeJob} busy={submitting} onCancel={requestCancel} onTranslateArtifact={requestTranslate} />
      </Panel>
    </div>
  );
}
