import { useRef, useState } from "react";
import {
  Activity,
  Check,
  Download,
  Expand,
  Film,
  MonitorPlay,
  Pause,
  Play,
  RotateCcw,
  Square,
  Terminal,
} from "lucide-react";
import { elapsed, Job, modeName, statusName } from "./types";
import { Signal } from "./Signal";
export const Stamp = ({ code }: { code: string }) => (
  <span className="stamp">{code}</span>
);
export function Monitor({
  job,
  backend,
  action,
  busy,
}: {
  job: Job | null;
  backend: string;
  action: (path: string) => void;
  busy: boolean;
}) {
  const screen = useRef<HTMLDivElement>(null),
    [playing, play] = useState(false),
    [fullError, setFullError] = useState("");
  const completed = job?.status === "completed",
    active = job?.status === "queued" || job?.status === "running";
  const enterFull = () => {
    screen.current
      ?.requestFullscreen?.()
      .catch(() => setFullError("当前浏览器无法进入全屏"));
  };
  return (
    <div className="middle">
      <section className="panel monitor">
        <div className="paneltitle">
          <MonitorPlay size={18} />
          <h2>输出监看</h2>
          <Stamp code="MONITOR" />
        </div>
        <div className="viewer" ref={screen}>
          {completed ? (
            <video
              key={job.job_id}
              src={"/api/jobs/" + job.job_id + "/result"}
              controls
              playsInline
              preload="metadata"
            />
          ) : (
            <>
              <Signal playing={playing} />
              <div className="frame">
                <i />
                <i />
                <i />
                <i />
              </div>
              <span className="signal-tag">
                <b /> SIGNAL TEST
              </span>
              <span className="signal-caption">监看测试画面</span>
            </>
          )}
        </div>
        <div className="transport">
          {completed ? (
            <Film size={15} />
          ) : (
            <button
              className="icon"
              aria-label={playing ? "暂停测试画面" : "播放测试画面"}
              onClick={() => play(!playing)}
            >
              {playing ? <Pause size={14} /> : <Play size={14} />}
            </button>
          )}
          <span className="mono">
            {completed ? job.parameters.duration + " SEC" : "TEST SIGNAL"}
          </span>
          <span className="transportline" />
          <span className="mono">
            {completed ? job.parameters.resolution : "16:9"}
          </span>
          <button className="icon" aria-label="全屏监看" onClick={enterFull}>
            <Expand size={15} />
          </button>
        </div>
        {fullError && <p className="error">{fullError}</p>}
      </section>
      <section className="panel inspector">
        <div className="paneltitle">
          <Activity size={17} />
          <h2>任务详情</h2>
          <span className={"pill " + (job?.status ?? "idle")}>
            {job ? statusName[job.status] : "待机"}
          </span>
        </div>
        {job ? (
          <>
            <div className="jobprompt">{job.prompt}</div>
            <div className="metrics">
              <span>
                <small>执行阶段</small>
                {job.stage}
              </span>
              <span>
                <small>运行时间</small>
                <b className="mono">{elapsed(job)}</b>
              </span>
              <span>
                <small>生成模式</small>
                {modeName[job.mode]}
              </span>
            </div>
            <div className="jobmeta">
              {job.backend === "mock" ? "Mock 测试片 · " : ""}
              {job.parameters.preset} · {job.parameters.steps} 步 · Seed{" "}
              {job.parameters.seed} · {job.input_files.length} 个素材
              <div>
                Profile {job.parameters.model_profile_id ?? "原有工作流"} · LoRA{" "}
                {job.parameters.lora_id ?? "None"} · 强度{" "}
                {job.parameters.lora_strength ?? 0}
              </div>
              <div>
                Sampler {job.parameters.sampler ?? "随工作流"} · Scheduler{" "}
                {job.parameters.scheduler ?? "随工作流"} · Shift{" "}
                {job.parameters.shift ?? "随工作流"} · Turbo{" "}
                {job.parameters.turbo_enabled == null
                  ? "随工作流"
                  : job.parameters.turbo_enabled
                    ? "开"
                    : "关"}
              </div>
            </div>
            {job.error_message && (
              <p className="error" role="alert">
                {job.error_message}
              </p>
            )}
            <div className="actions">
              {completed && (
                <a
                  href={"/api/jobs/" + job.job_id + "/result?download=true"}
                  download
                >
                  <Download size={14} />
                  下载视频
                </a>
              )}
              {active ? (
                <button
                  disabled={busy || job.cancel_requested}
                  onClick={() => action("cancel")}
                >
                  <Square size={13} />
                  {job.cancel_requested ? "取消中" : "取消任务"}
                </button>
              ) : (
                <button disabled={busy} onClick={() => action("regenerate")}>
                  <RotateCcw size={14} />
                  重新生成
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="standby">
            <span className="standby-icon">
              <Terminal size={21} />
            </span>
            <div>
              <strong>等待生成任务</strong>
              <p>提交任务后，在这里查看执行阶段和结果。</p>
            </div>
          </div>
        )}
      </section>
      <div className="localnote">
        <Check size={14} />
        {backend === "mock"
          ? "Mock 模式生成本地测试片，用于验证工作流程。"
          : "素材与生成结果保存在本机。"}
      </div>
    </div>
  );
}
