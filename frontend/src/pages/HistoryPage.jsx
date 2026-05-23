import { Panel } from "../components/Panel.jsx";

export function HistoryPage() {
  return (
    <Panel title="任务历史" description="阶段 1 接入本地任务历史。">
      <div className="empty-state">暂无任务。</div>
    </Panel>
  );
}