import { useEffect, useState } from "react";

import {
  cancelJob,
  createLadaJob,
  getDefaults,
  getJob,
  subscribeJobEvents,
  TERMINAL_STATUSES,
  uploadFile,
} from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";

const initialForm = {
  input_file: "",
  encoding_preset: "hevc-nvidia-gpu-hq",
  device: "cuda:0",
  fp16_mode: "true",
  max_clip_length: 180,
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
  };
}

function optionalNumber(value) {
  if (value === "" || value == null) {
    return null;
  }
  return Number(value);
}

export function LadaPage() {
  const [inputMode, setInputMode] = useState("path");
  const [form, setForm] = useState(initialForm);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [activeJob, setActiveJob] = useState(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [streamMode, setStreamMode] = useState("idle");

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

  const submitJob = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      let inputFile = form.input_file.trim();
      if (inputMode === "upload") {
        if (!selectedFile) {
          throw new Error("请选择要上传的视频文件。");
        }
        setUploadProgress(0);
        const upload = await uploadFile(selectedFile, setUploadProgress);
        setUploadedFile(upload);
        inputFile = upload.path;
      }
      if (!inputFile) {
        throw new Error("请输入本机视频路径或先上传文件。");
      }

      const request = {
        input_file: inputFile,
        encoding_preset: form.encoding_preset.trim() || null,
        device: form.device.trim() || null,
        fp16: form.fp16_mode === "default" ? null : form.fp16_mode === "true",
        max_clip_length: optionalNumber(form.max_clip_length),
      };
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
      <Panel title="LADA 去码" description="调用本机 lada-cli.exe，输出视频会登记为任务产物。">
        <form className="task-form" onSubmit={submitJob}>
          <div className="segmented-control" aria-label="输入来源">
            <button className={inputMode === "path" ? "active" : ""} onClick={() => setInputMode("path")} type="button">
              路径
            </button>
            <button className={inputMode === "upload" ? "active" : ""} onClick={() => setInputMode("upload")} type="button">
              上传
            </button>
          </div>

          {inputMode === "path" ? (
            <label>
              输入视频
              <input
                onChange={(event) => updateField("input_file", event.target.value)}
                placeholder="D:\\media\\video.mp4"
                value={form.input_file}
              />
            </label>
          ) : (
            <div className="upload-box">
              <input accept="video/*" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} type="file" />
              <div className="upload-progress"><span style={{ width: `${uploadProgress}%` }} /></div>
              {uploadedFile ? (
                <div className="upload-result">
                  <p>{uploadedFile.filename} · {Math.round(uploadedFile.size_bytes / 1024)} KB</p>
                  <code>{uploadedFile.path}</code>
                </div>
              ) : null}
            </div>
          )}

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

          {error ? <div className="error-box">{error}</div> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "处理中" : "启动 LADA"}
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