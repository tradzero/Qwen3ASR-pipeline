import { useEffect, useState } from "react";

import {
  cancelJob,
  createTranslateJob,
  getDefaults,
  getJob,
  listJobs,
  subscribeJobEvents,
  TERMINAL_STATUSES,
} from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";

const initialForm = {
  input_text: "",
  input_file: "",
  source_job_id: "",
  artifact_name: "subtitle",
  target_language: "简体中文",
  model: "deepseek-v4-pro",
  reasoning_effort: "max",
  temperature: 0.2,
  max_tokens: 4096,
  chunk_chars: 6000,
  prompt_template: "",
};

function buildForm(defaults) {
  const web = defaults?.web ?? {};
  return {
    ...initialForm,
    target_language: web.deepseek_target_language ?? initialForm.target_language,
    model: web.deepseek_model ?? initialForm.model,
    reasoning_effort: web.deepseek_reasoning_effort ?? initialForm.reasoning_effort,
    temperature: web.deepseek_temperature ?? initialForm.temperature,
    max_tokens: web.deepseek_max_tokens ?? initialForm.max_tokens,
    chunk_chars: web.deepseek_chunk_chars ?? initialForm.chunk_chars,
    prompt_template: web.deepseek_prompt_template ?? "",
  };
}

function numberValue(value) {
  return Number(value);
}

function hasSubtitleArtifact(job) {
  return job.artifacts?.some((artifact) => artifact.name === "subtitle" || artifact.kind === "srt");
}

export function TranslatePage() {
  const [sourceMode, setSourceMode] = useState("job");
  const [form, setForm] = useState(initialForm);
  const [jobs, setJobs] = useState([]);
  const [activeJob, setActiveJob] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [streamMode, setStreamMode] = useState("idle");

  useEffect(() => {
    let alive = true;
    Promise.all([getDefaults(), listJobs()])
      .then(([defaults, response]) => {
        if (!alive) {
          return;
        }
        const nextForm = buildForm(defaults);
        const subtitleJobs = response.jobs.filter(hasSubtitleArtifact);
        nextForm.source_job_id = subtitleJobs[0]?.job_id ?? "";
        setForm(nextForm);
        setJobs(response.jobs);
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

  useEffect(() => {
    const jobId = activeJob?.job_id;
    if (!jobId || TERMINAL_STATUSES.has(activeJob.status)) {
      return undefined;
    }

    let alive = true;
    let closeEvents = null;
    let intervalId = null;
    const refreshJob = async () => {
      try {
        const nextJob = await getJob(jobId);
        if (!alive) {
          return;
        }
        setActiveJob(nextJob);
        if (TERMINAL_STATUSES.has(nextJob.status)) {
          closeEvents?.();
          window.clearInterval(intervalId);
          setStreamMode("closed");
        }
      } catch (nextError) {
        if (alive) {
          setStreamMode("polling");
          setError(nextError.message);
        }
      }
    };

    setStreamMode("live");
    closeEvents = subscribeJobEvents(
      jobId,
      () => refreshJob(),
      () => {
        if (alive) {
          setStreamMode("polling");
        }
      },
    );
    intervalId = window.setInterval(refreshJob, 3000);
    refreshJob();

    return () => {
      alive = false;
      closeEvents?.();
      window.clearInterval(intervalId);
    };
  }, [activeJob?.job_id]);

  const updateField = (name, value) => {
    setForm((current) => ({ ...current, [name]: value }));
  };

  const subtitleJobs = jobs.filter(hasSubtitleArtifact);

  const submitJob = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      const payload = {
        target_language: form.target_language.trim(),
        model: form.model.trim(),
        reasoning_effort: form.reasoning_effort,
        temperature: numberValue(form.temperature),
        max_tokens: numberValue(form.max_tokens),
        chunk_chars: numberValue(form.chunk_chars),
        prompt_template: form.prompt_template.trim() || null,
      };

      if (sourceMode === "job") {
        if (!form.source_job_id) {
          throw new Error("请选择包含 SRT 产物的历史任务。");
        }
        payload.source_job_id = form.source_job_id;
        payload.artifact_name = form.artifact_name.trim() || "subtitle";
      } else if (sourceMode === "path") {
        if (!form.input_file.trim()) {
          throw new Error("请输入本机 SRT 文件路径。");
        }
        payload.input_file = form.input_file.trim();
      } else {
        if (!form.input_text.trim()) {
          throw new Error("请粘贴 SRT 字幕内容。");
        }
        payload.input_text = form.input_text;
      }

      const job = await createTranslateJob(payload);
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

  return (
    <div className="page-grid">
      <Panel title="DeepSeek 字幕翻译" description="读取 SRT 字幕并输出 translated.srt。">
        <form className="task-form" onSubmit={submitJob}>
          <div className="segmented-control three" aria-label="字幕来源">
            <button className={sourceMode === "job" ? "active" : ""} onClick={() => setSourceMode("job")} type="button">
              历史
            </button>
            <button className={sourceMode === "path" ? "active" : ""} onClick={() => setSourceMode("path")} type="button">
              路径
            </button>
            <button className={sourceMode === "text" ? "active" : ""} onClick={() => setSourceMode("text")} type="button">
              粘贴
            </button>
          </div>

          {sourceMode === "job" ? (
            <div className="form-grid">
              <label>
                历史字幕任务
                <select onChange={(event) => updateField("source_job_id", event.target.value)} value={form.source_job_id}>
                  <option value="">选择任务</option>
                  {subtitleJobs.map((job) => (
                    <option key={job.job_id} value={job.job_id}>{job.job_id}</option>
                  ))}
                </select>
              </label>
              <label>
                artifact
                <input onChange={(event) => updateField("artifact_name", event.target.value)} value={form.artifact_name} />
              </label>
            </div>
          ) : null}

          {sourceMode === "path" ? (
            <label>
              SRT 路径
              <input onChange={(event) => updateField("input_file", event.target.value)} placeholder="D:\\media\\video.srt" value={form.input_file} />
            </label>
          ) : null}

          {sourceMode === "text" ? (
            <label>
              SRT 内容
              <textarea onChange={(event) => updateField("input_text", event.target.value)} rows="8" value={form.input_text} />
            </label>
          ) : null}

          <div className="form-grid">
            <label>
              目标语言
              <input onChange={(event) => updateField("target_language", event.target.value)} value={form.target_language} />
            </label>
            <label>
              模型
              <input onChange={(event) => updateField("model", event.target.value)} value={form.model} />
            </label>
            <label>
              思考强度
              <select onChange={(event) => updateField("reasoning_effort", event.target.value)} value={form.reasoning_effort}>
                <option value="high">high</option>
                <option value="max">max</option>
              </select>
            </label>
            <label>
              temperature
              <input max="2" min="0" onChange={(event) => updateField("temperature", event.target.value)} step="0.1" type="number" value={form.temperature} />
            </label>
            <label>
              max tokens
              <input min="1" onChange={(event) => updateField("max_tokens", event.target.value)} type="number" value={form.max_tokens} />
            </label>
            <label>
              chunk chars
              <input max="30000" min="500" onChange={(event) => updateField("chunk_chars", event.target.value)} type="number" value={form.chunk_chars} />
            </label>
          </div>

          <label>
            Prompt 模板
            <textarea onChange={(event) => updateField("prompt_template", event.target.value)} rows="9" value={form.prompt_template} />
          </label>

          {error ? <div className="error-box">{error}</div> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "处理中" : "启动翻译"}
          </button>
        </form>
      </Panel>
      <Panel title="任务进度">
        <div className="stream-line">{streamMode === "live" ? "SSE live" : streamMode === "polling" ? "polling" : "idle"}</div>
        <JobDetail job={activeJob} busy={submitting} onCancel={requestCancel} />
      </Panel>
    </div>
  );
}