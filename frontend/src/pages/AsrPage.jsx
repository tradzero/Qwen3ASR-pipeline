import { Panel } from "../components/Panel.jsx";

export function AsrPage() {
  return (
    <div className="page-grid">
      <Panel title="ASR 任务" description="阶段 0 空壳：后续接入上传、路径输入、参数和进度事件。">
        <div className="placeholder-form">
          <label>
            输入路径
            <input placeholder="D:\\media\\video.mp4 或 \\NAS\\media\\video.mp4" />
          </label>
          <label>
            后端
            <select defaultValue="auto">
              <option value="auto">auto</option>
              <option value="transformers">transformers</option>
              <option value="vllm">vllm</option>
            </select>
          </label>
        </div>
      </Panel>
      <Panel title="任务进度" description="阶段 1 后由 SSE 实时更新。">
        <div className="timeline-preview">
          <span>cache</span>
          <span>vad</span>
          <span>model</span>
          <span>asr</span>
          <span>output</span>
        </div>
      </Panel>
    </div>
  );
}