import { useEffect, useState } from "react";

import { API_BASE_URL, getHealth } from "./api/client.js";
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
  const ActivePage = navItems.find((item) => item.id === activeTab)?.component ?? AsrPage;

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

  return (
    <main className="console-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Local task console</p>
          <h1>Qwen3-ASR 控制台</h1>
        </div>
        <div className={`service-pill ${serviceStatus}`}>{API_BASE_URL.replace(/^https?:\/\//, "")} · {serviceStatus}</div>
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

      <ActivePage />
    </main>
  );
}