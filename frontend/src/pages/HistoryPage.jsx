import { useEffect, useState } from "react";

import { cancelJob, getJob, listJobs, TERMINAL_STATUSES } from "../api/client.js";
import { JobDetail } from "../components/JobDetail.jsx";
import { Panel } from "../components/Panel.jsx";

export function HistoryPage({ onTranslateArtifact }) {
  const [jobs, setJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadJobs = async () => {
    try {
      const response = await listJobs();
      setJobs(response.jobs);
      setSelectedJob((current) => {
        if (!current) {
          return response.jobs[0] ?? null;
        }
        return response.jobs.find((job) => job.job_id === current.job_id) ?? current;
      });
    } catch (nextError) {
      setError(nextError.message);
    }
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const response = await listJobs();
        if (alive) {
          setJobs(response.jobs);
          setSelectedJob((current) => {
            if (!current) {
              return response.jobs[0] ?? null;
            }
            return response.jobs.find((job) => job.job_id === current.job_id) ?? current;
          });
        }
      } catch (nextError) {
        if (alive) {
          setError(nextError.message);
        }
      }
    };
    load();
    const intervalId = window.setInterval(load, 5000);
    return () => {
      alive = false;
      window.clearInterval(intervalId);
    };
  }, []);

  const requestCancel = async () => {
    if (!selectedJob) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const response = await cancelJob(selectedJob.job_id);
      setSelectedJob(response.job);
      await loadJobs();
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  const requestTranslate = async (job, artifact) => {
    if (!onTranslateArtifact) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onTranslateArtifact(job, artifact);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-grid history-grid">
      <Panel title="任务历史">
        <div className="panel-actions">
          <button className="ghost-button" onClick={loadJobs} type="button">刷新</button>
        </div>
        {error ? <div className="error-box">{error}</div> : null}
        <div className="job-list">
          {jobs.length ? (
            jobs.map((job) => (
              <button
                className={selectedJob?.job_id === job.job_id ? "job-row active" : "job-row"}
                key={job.job_id}
                onClick={() => setSelectedJob(job)}
                type="button"
              >
                <span className={`status-dot ${job.status}`} />
                <span>{job.type}</span>
                <strong>{job.stage}</strong>
                <small>{TERMINAL_STATUSES.has(job.status) ? job.status : `${Math.round(job.progress.percent)}%`}</small>
              </button>
            ))
          ) : (
            <div className="empty-state">暂无任务。</div>
          )}
        </div>
      </Panel>
      <Panel title="任务详情">
        <JobDetail job={selectedJob} busy={busy} onCancel={requestCancel} onTranslateArtifact={requestTranslate} />
      </Panel>
    </div>
  );
}