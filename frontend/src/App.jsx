import { useState } from "react";

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
  const ActivePage = navItems.find((item) => item.id === activeTab)?.component ?? AsrPage;

  return (
    <main className="console-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Local task console</p>
          <h1>Qwen3-ASR 控制台</h1>
        </div>
        <div className="service-pill">127.0.0.1:7860</div>
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