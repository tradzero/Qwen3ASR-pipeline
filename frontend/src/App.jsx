import { useEffect, useState } from "react";

import { API_BASE_URL, getHealth, getJob, getWarmupStatus, subscribeJobEvents, TERMINAL_STATUSES } from "./api/client.js";
import { Panel } from "./components/Panel.jsx";
import { AsrPage } from "./pages/AsrPage.jsx";
import { HistoryPage } from "./pages/HistoryPage.jsx";
import { LadaPage } from "./pages/LadaPage.jsx";
import { TranslatePage } from "./pages/TranslatePage.jsx";

const navItems = [
  { id: "asr", label: "ASR", component: AsrPage },
  { id: "lada", label: "LADA", component: LadaPage },
  { id: "translate", label: "翻译", component: TranslatePage },
  { id: "history", label: "历史", component: HistoryPage },
];

export function App() {
  const [activeTab, setActiveTab] = useState(navItems[0].id);
  const [serviceStatus, setServiceStatus] = useState("checking");
  const [warmup, setWarmup] = useState({ status: "checking", stage: "backend", message: "等待后端响应..." });
  const [activeJob, setActiveJob] = useState(null);
  const [streamMode, setStreamMode] = useState("idle");
  const [jobError, setJobError] = useState("");
  const ActivePage = navItems.find((item) => item.id === activeTab)?.component ?? AsrPage;
  const warmupBlocking = warmup.status === "checking" || warmup.status === "pending" || warmup.status === "running";

  useEffect(() => {
    let alive = true;
    const check = () => {
      getHealth()
        .then(() => {
          if (alive) {
            setServiceStatus("online");
          }
        })
        .catch(() => {
          if (alive) {
            setServiceStatus("offline");
          }
        });
    };
    check();
    const intervalId = window.setInterval(check, 10000);
    return () => {
      alive = false;
      window.clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    let intervalId = null;
    const loadWarmup = () => {
      getWarmupStatus()
        .then((status) => {
          if (alive) {
            setWarmup(status);
            if (["ready", "failed", "disabled"].includes(status.status) && intervalId) {
              window.clearInterval(intervalId);
            }
          }
        })
        .catch((error) => {
          if (alive) {
            setWarmup({ status: "checking", stage: "backend", message: "等待后端启动或恢复...", error: error.message });
          }
        });
    };
    loadWarmup();
    intervalId = window.setInterval(loadWarmup, 2000);
    return () => {
      alive = false;
      window.clearInterval(intervalId);
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
      } catch (error) {
        if (alive) {
          setStreamMode("polling");
          setJobError(error.message);
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

  const pageProps = {
    activeJob,
    setActiveJob,
    streamMode,
    jobError,
    setJobError,
  };

  return (
    <main className="console-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Local task console</p>
          <h1>Qwen3-ASR 控制台</h1>
        </div>
        <div className={`service-pill ${serviceStatus}`}>{API_BASE_URL.replace(/^https?:\/\//, "")} · {serviceStatus} · {warmup.status}</div>
      </header>

      <nav className="tabbar" aria-label="任务类型">
        {navItems.map((item) => (
          <button
            aria-pressed={item.id === activeTab}
            className={item.id === activeTab ? "tab active" : "tab"}
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>

      {warmupBlocking ? (
        <div className="warmup-shell">
          <Panel title="模型预热" description={warmup.message}>
            <div className="warmup-status">
              <div className="progress-track">
                <div className="progress-fill indeterminate" />
              </div>
              <strong>{warmup.stage}</strong>
            </div>
          </Panel>
        </div>
      ) : (
        <>
          {warmup.status === "failed" ? <div className="error-box warmup-error">{warmup.error || warmup.message}</div> : null}
          <ActivePage {...pageProps} />
        </>
      )}
    </main>
  );
}