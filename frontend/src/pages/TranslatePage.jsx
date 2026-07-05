import { useEffect, useState } from "react";

import {
  cancelJob,
  createTranslateJob,
  getDefaults,
  listJobs,
  resumeTranslateJob,
} from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";
import { PathDropInput } from "../components/PathDropInput.jsx";

const DEEPSEEK_V4_CONTEXT_TOKENS = 1000000;
const DEEPSEEK_V4_MAX_OUTPUT_TOKENS = 384000;

const initialForm = {
  input_text: "",
  input_file: "",
  source_job_id: "",
  artifact_name: "subtitle",
  target_language: "简体中文",
  model: "deepseek-v4-pro",
  reasoning_effort: "max",
  max_tokens: DEEPSEEK_V4_MAX_OUTPUT_TOKENS,
  chunk_chars: 200000,
  max_blocks_per_chunk: 80,
  debug_io: false,
  prompt_template: "",
};

function buildForm(defaults) {
  const web = defaults?.web ?? {};
  return {
    ...initialForm,
    target_language: web.deepseek_target_language ?? initialForm.target_language,
    model: web.deepseek_model ?? initialForm.model,
    reasoning_effort: web.deepseek_reasoning_effort ?? initialForm.reasoning_effort,
    max_tokens: web.deepseek_max_tokens ?? initialForm.max_tokens,
    chunk_chars: web.deepseek_chunk_chars ?? initialForm.chunk_chars,
    max_blocks_per_chunk: web.deepseek_max_blocks_per_chunk ?? initialForm.max_blocks_per_chunk,
    debug_io: web.deepseek_debug_io ?? initialForm.debug_io,
    prompt_template: web.deepseek_prompt_template ?? "",
  };
}

function numberValue(value) {
  return Number(value);
}

function subtitleArtifacts(job) {
  return job?.artifacts?.filter((artifact) => artifact.kind === "srt") ?? [];
}

function hasSubtitleArtifact(job) {
  return subtitleArtifacts(job).length > 0;
}

export function TranslatePage({ activeJob, setActiveJob, streamMode, jobError, setJobError }) {
  const [sourceMode, setSourceMode] = useState("job");
  const [form, setForm] = useState(initialForm);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([getDefaults(), listJobs()])
      .then(([defaults, response]) => {
        if (!alive) {
          return;
        }
        const nextForm = buildForm(defaults);
        const subtitleJobs = response.jobs.filter(hasSubtitleArtifact);
        const firstArtifact = subtitleArtifacts(subtitleJobs[0])[0];
        nextForm.source_job_id = subtitleJobs[0]?.job_id ?? "";
        nextForm.artifact_name = firstArtifact?.name ?? "subtitle";
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

  const updateField = (name, value) => {
    setForm((current) => ({ ...current, [name]: value }));
  };

  const subtitleJobs = jobs.filter(hasSubtitleArtifact);
  const selectedSourceJob = jobs.find((job) => job.job_id === form.source_job_id);
  const selectedArtifacts = subtitleArtifacts(selectedSourceJob);

  const selectSourceJob = (jobId) => {
    const job = jobs.find((candidate) => candidate.job_id === jobId);
    const firstArtifact = subtitleArtifacts(job)[0];
    setForm((current) => ({
      ...current,
      source_job_id: jobId,
      artifact_name: firstArtifact?.name ?? "",
    }));
  };

  const submitJob = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setJobError?.("");

    try {
      const payload = {
        target_language: form.target_language.trim(),
        model: form.model.trim(),
        reasoning_effort: form.reasoning_effort,
        max_tokens: numberValue(form.max_tokens),
        chunk_chars: numberValue(form.chunk_chars),
        max_blocks_per_chunk: numberValue(form.max_blocks_per_chunk),
        debug_io: Boolean(form.debug_io),
        prompt_template: form.prompt_template.trim() || null,
      };

      if (sourceMode === "job") {
        if (!form.source_job_id) {
          throw new Error("请选择包含 SRT 产物的历史任务。");
        }
        if (!form.artifact_name.trim()) {
          throw new Error("请选择要翻译的 SRT 产物。");
        }
        payload.source_job_id = form.source_job_id;
        payload.artifact_name = form.artifact_name.trim();
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

  const requestResumeTranslate = async (job) => {
    setSubmitting(true);
    setError("");
    setJobError?.("");
    try {
      const resumedJob = await resumeTranslateJob(job.job_id);
      setActiveJob(resumedJob);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page-grid">
      <Panel title="DeepSeek 字幕翻译" description="读取 SRT 字幕并按源文件名输出 SRT。">
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
                <select onChange={(event) => selectSourceJob(event.target.value)} value={form.source_job_id}>
                  <option value="">选择任务</option>
                  {subtitleJobs.map((job) => (
                    <option key={job.job_id} value={job.job_id}>{job.job_id}</option>
                  ))}
                </select>
              </label>
              <label>
                SRT 产物
                <select disabled={!selectedArtifacts.length} onChange={(event) => updateField("artifact_name", event.target.value)} value={form.artifact_name}>
                  <option value="">选择产物</option>
                  {selectedArtifacts.map((artifact) => (
                    <option key={artifact.name} value={artifact.name}>{artifact.name}.{artifact.kind}</option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}

          {sourceMode === "path" ? (
            <PathDropInput
              label="SRT 路径"
              onChange={(value) => updateField("input_file", value)}
              placeholder="D:\\media\\video.srt 或 \\\\NAS\\media\\video.srt"
              value={form.input_file}
            />
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
              max tokens
              <input max={DEEPSEEK_V4_MAX_OUTPUT_TOKENS} min="1" onChange={(event) => updateField("max_tokens", event.target.value)} type="number" value={form.max_tokens} />
            </label>
            <label>
              chunk chars
              <input max={DEEPSEEK_V4_CONTEXT_TOKENS} min="500" onChange={(event) => updateField("chunk_chars", event.target.value)} type="number" value={form.chunk_chars} />
            </label>
            <label>
              max blocks
              <input max="500" min="1" onChange={(event) => updateField("max_blocks_per_chunk", event.target.value)} type="number" value={form.max_blocks_per_chunk} />
            </label>
          </div>

          <div className="toggle-grid">
            <label><input checked={form.debug_io} onChange={(event) => updateField("debug_io", event.target.checked)} type="checkbox" />debug I/O</label>
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
        {jobError ? <div className="error-box">{jobError}</div> : null}
        <JobDetail job={activeJob} busy={submitting} onCancel={requestCancel} onResumeTranslate={requestResumeTranslate} />
      </Panel>
    </div>
  );
}
