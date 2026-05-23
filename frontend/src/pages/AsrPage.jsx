import { useEffect, useState } from "react";

import {
  cancelJob,
  createAsrJob,
  getDefaults,
} from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";

const initialForm = {
  input_file: "",
  model: "Qwen/Qwen3-ASR-1.7B",
  language: "",
  backend: "auto",
  gpu_memory_utilization: 0.5,
  max_inference_batch_size: 2,
  max_new_tokens: 2048,
  segment_duration: 60,
  max_segment_duration: 120,
  save_srt: true,
  use_cache: true,
  refresh_cache: false,
  return_time_stamps: true,
  forced_aligner_model: "Qwen/Qwen3-ForcedAligner-0.6B",
  srt_max_chars: 42,
  srt_max_duration: 6,
};

function buildForm(defaults) {
  const asr = defaults?.asr ?? {};
  return {
    ...initialForm,
    ...asr,
    input_file: "",
    language: asr.language ?? "",
    forced_aligner_model: asr.forced_aligner_model ?? "",
  };
}

function numberValue(value) {
  return Number(value);
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
    setForm((current) => ({ ...current, [name]: value }));
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
          <label>
            输入路径
            <input
              onChange={(event) => updateField("input_file", event.target.value)}
              placeholder="D:\\media\\video.mp4 或 \\\\NAS\\media\\video.mp4"
              value={form.input_file}
            />
          </label>

          <div className="form-grid">
            <label>
              模型
              <input onChange={(event) => updateField("model", event.target.value)} value={form.model} />
            </label>
            <label>
              语言
              <input onChange={(event) => updateField("language", event.target.value)} placeholder="auto" value={form.language} />
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