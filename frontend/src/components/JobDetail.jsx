import { useLayoutEffect, useRef } from "react";

import { artifactUrl, TERMINAL_STATUSES } from "../api/client.js";

function subtitleArtifact(job) {
  const artifacts = job?.artifacts ?? [];
  return (
    artifacts.find((artifact) => artifact.kind === "srt" && artifact.name === "subtitle")
    ?? artifacts.find((artifact) => artifact.kind === "srt")
    ?? null
  );
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0))}%`;
}

function formatSeconds(value) {
  if (value == null) {
    return "--:--";
  }
  const total = Math.max(0, Math.round(Number(value)));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function JobDetail({ job, onCancel, onTranslateArtifact, onResumeTranslate, busy = false }) {
  const logBoxRef = useRef(null);
  const shouldStickToBottomRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const lastJobIdRef = useRef(null);
  const latestLog = job?.logs?.length ? job.logs[job.logs.length - 1] : "";

  useLayoutEffect(() => {
    const logBox = logBoxRef.current;
    if (!logBox || !job) {
      return;
    }
    if (lastJobIdRef.current !== job.job_id) {
      shouldStickToBottomRef.current = true;
      lastJobIdRef.current = job.job_id;
    }
    if (shouldStickToBottomRef.current) {
      logBox.scrollTop = logBox.scrollHeight;
      lastScrollTopRef.current = logBox.scrollTop;
      requestAnimationFrame(() => {
        logBox.scrollTop = logBox.scrollHeight;
        lastScrollTopRef.current = logBox.scrollTop;
      });
    }
  }, [job?.job_id, job?.logs?.length, latestLog, job?.stage, job?.status]);

  if (!job) {
    return <div className="empty-state">没有正在查看的任务。</div>;
  }

  const progress = job.progress ?? {};
  const canCancel = Boolean(onCancel) && !TERMINAL_STATUSES.has(job.status);
  const translateArtifact = subtitleArtifact(job);
  const canTranslate = Boolean(onTranslateArtifact) && job.type === "asr" && job.status === "succeeded" && Boolean(translateArtifact);
  const canResumeTranslate = Boolean(onResumeTranslate)
    && job.type === "translate"
    && TERMINAL_STATUSES.has(job.status)
    && job.status !== "succeeded";
  const onLogScroll = () => {
    const logBox = logBoxRef.current;
    if (!logBox) {
      return;
    }
    const distanceToBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight;
    const scrolledUp = logBox.scrollTop < lastScrollTopRef.current;
    if (distanceToBottom <= 48) {
      shouldStickToBottomRef.current = true;
    } else if (scrolledUp) {
      shouldStickToBottomRef.current = false;
    }
    lastScrollTopRef.current = logBox.scrollTop;
  };

  return (
    <div className="job-detail">
      <div className="job-summary">
        <div>
          <span className={`status-dot ${job.status}`} />
          <strong>{job.status}</strong>
          <span className="job-stage">{job.stage}</span>
        </div>
        {canTranslate || canResumeTranslate || canCancel ? (
          <div className="job-actions">
            {canTranslate ? (
              <button className="ghost-button accent" disabled={busy} onClick={() => onTranslateArtifact(job, translateArtifact)} type="button">
                转交翻译
              </button>
            ) : null}
            {canResumeTranslate ? (
              <button className="ghost-button accent" disabled={busy} onClick={() => onResumeTranslate(job)} type="button">
                继续翻译
              </button>
            ) : null}
            {canCancel ? (
              <button className="ghost-button danger" disabled={busy} onClick={onCancel} type="button">
                取消
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="progress-block" aria-label="任务进度">
        <div className="progress-track">
          <div className="progress-fill" style={{ width: formatPercent(progress.percent) }} />
        </div>
        <div className="progress-meta">
          <span>{formatPercent(progress.percent)}</span>
          <span>{progress.done ?? 0}/{progress.total ?? 0}</span>
          <span>elapsed {formatSeconds(progress.elapsed_seconds)}</span>
          <span>eta {formatSeconds(progress.eta_seconds)}</span>
        </div>
      </div>

      {job.error ? <div className="error-box">{job.error}</div> : null}

      <div className="artifact-list">
        {job.artifacts.length ? (
          job.artifacts.map((artifact) => (
            <a href={artifactUrl(job.job_id, artifact.name)} key={artifact.name} target="_blank" rel="noreferrer">
              {artifact.name}.{artifact.kind}
            </a>
          ))
        ) : (
          <span className="muted-text">暂无产物</span>
        )}
      </div>

      <div className="log-box" aria-label="任务日志" onScroll={onLogScroll} ref={logBoxRef}>
        {job.logs.length ? job.logs.map((line) => <div key={line}>{line}</div>) : <div>暂无日志。</div>}
      </div>
    </div>
  );
}
