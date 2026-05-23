import { Panel } from "../components/Panel.jsx";

export function TranslatePage() {
  return (
    <Panel title="DeepSeek 翻译" description="阶段 5 接入手动翻译和 prompt 配置。">
      <div className="empty-state">等待翻译任务 API。</div>
    </Panel>
  );
}