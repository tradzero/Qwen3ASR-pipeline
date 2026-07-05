import { useEffect, useState } from "react";

import {
  cancelJob,
  createLadaJob,
  getDefaults,
  listJobs,
} from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";
import { PathDropInput } from "../components/PathDropInput.jsx";

const initialForm = {
  input_file: "",
  encoding_preset: "hevc-nvidia-gpu-hq",
  device: "cuda:0",
  fp16_mode: "true",
  max_clip_length: 180,
  handoff_translate: false,
  handoff_translate_source_mode: "job",
  handoff_translate_source_job_id: "",
  handoff_translate_artifact_name: "subtitle",
  handoff_translate_input_file: "",
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
  const web = defaults?.web ?? {};
  return {
    ...initialForm,
    input_file: "",
    encoding_preset: web.lada_encoding_preset ?? "",
    device: web.lada_device ?? "",
    fp16_mode: web.lada_fp16 == null ? "default" : String(Boolean(web.lada_fp16)),
    max_clip_length: web.lada_max_clip_length ?? "",
    handoff_translate_target_language: web.deepseek_target_language ?? initialForm.handoff_translate_target_language,
    handoff_translate_model: web.deepseek_model ?? initialForm.handoff_translate_model,
    handoff_translate_reasoning_effort: web.deepseek_reasoning_effort ?? initialForm.handoff_translate_reasoning_effort,
    handoff_translate_max_tokens: web.deepseek_max_tokens ?? initialForm.handoff_translate_max_tokens,
    handoff_translate_chunk_chars: web.deepseek_chunk_chars ?? initialForm.handoff_translate_chunk_chars,
    handoff_translate_max_blocks_per_chunk: web.deepseek_max_blocks_per_chunk ?? initialForm.handoff_translate_max_blocks_per_chunk,
    handoff_translate_debug_io: web.deepseek_debug_io ?? initialForm.handoff_translate_debug_io,
  };
}

function optionalNumber(value) {
  if (value === "" || value == null) {
    return null;
  }
  return Number(value);
}

function subtitleArtifacts(job) {
  return job?.artifacts?.filter((artifact) => artifact.kind === "srt") ?? [];
}

function hasSubtitleArtifact(job) {
  return subtitleArtifacts(job).length > 0;
}

export function LadaPage({ activeJob, setActiveJob, streamMode, jobError, setJobError }) {
  const [form, setForm] = useState(initialForm);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([getDefaults(), listJobs()])
      .then(([defaults, response]) => {
        if (alive) {
          const nextForm = buildForm(defaults);
          const subtitleJobs = response.jobs.filter(hasSubtitleArtifact);
          const firstArtifact = subtitleArtifacts(subtitleJobs[0])[0];
          nextForm.handoff_translate_source_job_id = subtitleJobs[0]?.job_id ?? "";
          nextForm.handoff_translate_artifact_name = firstArtifact?.name ?? "subtitle";
          setForm(nextForm);
          setJobs(response.jobs);
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

  const subtitleJobs = jobs.filter(hasSubtitleArtifact);
  const selectedSourceJob = jobs.find((job) => job.job_id === form.handoff_translate_source_job_id);
  const selectedArtifacts = subtitleArtifacts(selectedSourceJob);

  const selectSourceJob = (jobId) => {
    const job = jobs.find((candidate) => candidate.job_id === jobId);
    const firstArtifact = subtitleArtifacts(job)[0];
    setForm((current) => ({
      ...current,
      handoff_translate_source_job_id: jobId,
      handoff_translate_artifact_name: firstArtifact?.name ?? "",
    }));
  };

  const submitJob = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setJobError?.("");

    try {
      const inputFile = form.input_file.trim();
      if (!inputFile) {
        throw new Error("请输入服务端可访问的本机视频路径、盘符路径或 UNC/NAS 路径。");
      }

      const request = {
        input_file: inputFile,
        encoding_preset: form.encoding_preset.trim() || null,
        device: form.device.trim() || null,
        fp16: form.fp16_mode === "default" ? null : form.fp16_mode === "true",
        max_clip_length: optionalNumber(form.max_clip_length),
        handoff: {
          translate: {
            enabled: Boolean(form.handoff_translate),
            source_job_id: form.handoff_translate && form.handoff_translate_source_mode === "job" ? form.handoff_translate_source_job_id || null : null,
            artifact_name: form.handoff_translate_artifact_name.trim() || "subtitle",
            input_file: form.handoff_translate && form.handoff_translate_source_mode === "path" ? form.handoff_translate_input_file.trim() || null : null,
            target_language: form.handoff_translate_target_language.trim() || null,
            model: form.handoff_translate_model.trim() || null,
            reasoning_effort: form.handoff_translate_reasoning_effort,
            max_tokens: optionalNumber(form.handoff_translate_max_tokens),
            chunk_chars: optionalNumber(form.handoff_translate_chunk_chars),
            max_blocks_per_chunk: optionalNumber(form.handoff_translate_max_blocks_per_chunk),
            debug_io: Boolean(form.handoff_translate_debug_io),
          },
        },
      };
      if (form.handoff_translate && form.handoff_translate_source_mode === "job" && !form.handoff_translate_source_job_id) {
        throw new Error("请选择用于自动翻译的历史 SRT 任务。");
      }
      if (form.handoff_translate && form.handoff_translate_source_mode === "path" && !form.handoff_translate_input_file.trim()) {
        throw new Error("请输入用于自动翻译的 SRT 路径。");
      }
      const job = await createLadaJob(request);
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
      <Panel title="LADA 去码" description="调用本机 lada-cli.exe，输出优先写入输入视频同目录并登记为任务产物。">
        <form className="task-form" onSubmit={submitJob}>
          <PathDropInput
            label="输入视频"
            onChange={(value) => updateField("input_file", value)}
            placeholder="D:\\media\\video.mp4 或 \\\\NAS\\media\\video.mp4"
            value={form.input_file}
          />

          <div className="form-grid">
            <label>
              编码预设
              <input onChange={(event) => updateField("encoding_preset", event.target.value)} value={form.encoding_preset} />
            </label>
            <label>
              设备
              <input onChange={(event) => updateField("device", event.target.value)} value={form.device} />
            </label>
            <label>
              fp16
              <select onChange={(event) => updateField("fp16_mode", event.target.value)} value={form.fp16_mode}>
                <option value="default">默认</option>
                <option value="true">开启</option>
                <option value="false">关闭</option>
              </select>
            </label>
            <label>
              max clip length
              <input max={MAX_LADA_CLIP_LENGTH} min="1" onChange={(event) => updateField("max_clip_length", event.target.value)} type="number" value={form.max_clip_length} />
            </label>
          </div>

          <div className="handoff-section">
            <div className="toggle-grid">
              <label><input checked={form.handoff_translate} onChange={(event) => updateField("handoff_translate", event.target.checked)} type="checkbox" />完成后翻译</label>
            </div>

            {form.handoff_translate ? (
              <>
                <div className="segmented-control" aria-label="自动翻译来源">
                  <button className={form.handoff_translate_source_mode === "job" ? "active" : ""} onClick={() => updateField("handoff_translate_source_mode", "job")} type="button">
                    历史
                  </button>
                  <button className={form.handoff_translate_source_mode === "path" ? "active" : ""} onClick={() => updateField("handoff_translate_source_mode", "path")} type="button">
                    路径
                  </button>
                </div>

                {form.handoff_translate_source_mode === "job" ? (
                  <div className="form-grid">
                    <label>
                      历史字幕任务
                      <select onChange={(event) => selectSourceJob(event.target.value)} value={form.handoff_translate_source_job_id}>
                        <option value="">选择任务</option>
                        {subtitleJobs.map((job) => (
                          <option key={job.job_id} value={job.job_id}>{job.job_id}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      SRT 产物
                      <select disabled={!selectedArtifacts.length} onChange={(event) => updateField("handoff_translate_artifact_name", event.target.value)} value={form.handoff_translate_artifact_name}>
                        <option value="">选择产物</option>
                        {selectedArtifacts.map((artifact) => (
                          <option key={artifact.name} value={artifact.name}>{artifact.name}.{artifact.kind}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                ) : (
                  <PathDropInput
                    label="SRT 路径"
                    onChange={(value) => updateField("handoff_translate_input_file", value)}
                    placeholder="D:\\media\\video.srt 或 \\\\NAS\\media\\video.srt"
                    value={form.handoff_translate_input_file}
                  />
                )}

                <div className="form-grid">
                  <label>
                    目标语言
                    <input onChange={(event) => updateField("handoff_translate_target_language", event.target.value)} value={form.handoff_translate_target_language} />
                  </label>
                  <label>
                    模型
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
                    max blocks
                    <input max="500" min="1" onChange={(event) => updateField("handoff_translate_max_blocks_per_chunk", event.target.value)} type="number" value={form.handoff_translate_max_blocks_per_chunk} />
                  </label>
                  <label><input checked={form.handoff_translate_debug_io} onChange={(event) => updateField("handoff_translate_debug_io", event.target.checked)} type="checkbox" />debug I/O</label>
                </div>
              </>
            ) : null}
          </div>

          {error ? <div className="error-box">{error}</div> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "处理中" : "启动 LADA"}
          </button>
        </form>
      </Panel>
      <Panel title="任务进度">
        <div className="stream-line">{streamMode === "live" ? "SSE live" : streamMode === "polling" ? "polling" : "idle"}</div>
        {jobError ? <div className="error-box">{jobError}</div> : null}
        <JobDetail job={activeJob} busy={submitting} onCancel={requestCancel} />
      </Panel>
    </div>
  );
}
