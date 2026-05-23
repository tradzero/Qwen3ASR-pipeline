import { useEffect, useState } from "react";

import { API_BASE_URL, getHealth, getJob, getWarmupStatus, listJobs, subscribeJobEvents, TERMINAL_STATUSES } from "./api/client.js";
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
const taskTabs = new Set(["asr", "lada", "translate"]);

function initialActiveTab() {
  try {
    const storedTab = window.localStorage.getItem("qwen3-asr-active-tab");
    return navItems.some((item) => item.id === storedTab) ? storedTab : navItems[0].id;
  } catch {
    return navItems[0].id;
  }
}

function storeActiveTab(activeTab) {
  try {
    window.localStorage.setItem("qwen3-asr-active-tab", activeTab);
  } catch {
    return false;
  }
  return true;
}

export function App() {
  const [activeTab, setActiveTab] = useState(initialActiveTab);
  const [serviceStatus, setServiceStatus] = useState("checking");
  const [warmup, setWarmup] = useState({ status: "checking", stage: "backend", message: "等待后端响应..." });
  const [activeJobsByType, setActiveJobsByType] = useState({});
  const [streamMode, setStreamMode] = useState("idle");
  const [jobError, setJobError] = useState("");
  const [jobsRestored, setJobsRestored] = useState(false);
  const ActivePage = navItems.find((item) => item.id === activeTab)?.component ?? AsrPage;
  const activeJob = activeJobsByType[activeTab] ?? null;
  const warmupBlocking = warmup.status === "checking" || warmup.status === "pending" || warmup.status === "running";

  const setActiveJob = (job) => {
    setActiveJobsByType((current) => {
      const type = job?.type && taskTabs.has(job.type) ? job.type : activeTab;
      if (!taskTabs.has(type)) {
        return current;
      }
      if (!job) {
        const next = { ...current };
        delete next[type];
        return next;
      }
      return { ...current, [type]: job };
    });
  };

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
    storeActiveTab(activeTab);
  }, [activeTab]);

  useEffect(() => {
    if (jobsRestored || serviceStatus !== "online") {
      return undefined;
    }

    let alive = true;
    listJobs()
      .then((response) => {
        if (!alive) {
          return;
        }
        setActiveJobsByType((current) => {
          const next = { ...current };
          for (const job of response.jobs) {
            if (!taskTabs.has(job.type) || TERMINAL_STATUSES.has(job.status)) {
              continue;
            }
            const currentJob = next[job.type];
            if (!currentJob || TERMINAL_STATUSES.has(currentJob.status)) {
              next[job.type] = job;
            }
          }
          return next;
        });
        setJobsRestored(true);
      })
      .catch((error) => {
        if (alive) {
          setJobError(error.message);
        }
      });
    return () => {
      alive = false;
    };
  }, [jobsRestored, serviceStatus]);

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
    if (!jobId) {
      setStreamMode("idle");
      return undefined;
    }
    if (TERMINAL_STATUSES.has(activeJob.status)) {
      setStreamMode("closed");
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
  }, [activeJob?.job_id, activeJob?.status]);

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