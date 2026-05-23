import { useEffect, useState } from "react";

import {
  cancelJob,
  createLadaJob,
  getDefaults,
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

export function LadaPage({ activeJob, setActiveJob, streamMode, jobError, setJobError }) {
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
        throw new Error("请输入服务端可访问的本机视频路径、盘符路径或 UNC/NAS 路径。");
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
      <Panel title="LADA 去码" description="调用本机 lada-cli.exe，输出优先写入输入视频同目录并登记为任务产物。">
        <form className="task-form" onSubmit={submitJob}>
          <label>
            输入视频
            <input
              onChange={(event) => updateField("input_file", event.target.value)}
              placeholder="D:\\media\\video.mp4 或 \\\\NAS\\media\\video.mp4"
              value={form.input_file}
            />
          </label>

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
        {jobError ? <div className="error-box">{jobError}</div> : null}
        <JobDetail job={activeJob} busy={submitting} onCancel={requestCancel} />
      </Panel>
    </div>
  );
}