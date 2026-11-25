import { useState } from "react";
import {
  ChevronRight,
  History as HistoryIcon,
  ListVideo,
  Trash2,
  X,
} from "lucide-react";
import { Job, modeName, statusName, Status } from "./types";
export function History({
  jobs,
  selected,
  select,
  remove,
  more,
  hasMore,
}: {
  jobs: Job[];
  selected: string;
  select: (j: Job) => void;
  remove: (j: Job) => Promise<void>;
  more: () => void;
  hasMore: boolean;
}) {
  const [filter, setFilter] = useState("all"),
    [deleting, setDeleting] = useState(""),
    [busy, setBusy] = useState(false);
  const visible = jobs.filter((j) => filter === "all" || j.status === filter);
  return (
    <aside className="panel history">
      <div className="paneltitle">
        <HistoryIcon size={17} />
        <h2>任务历史</h2>
        <span className="count">
          {jobs.length}
          {hasMore ? "+" : ""}
        </span>
      </div>
      <select
        aria-label="筛选任务状态"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      >
        <option value="all">全部状态</option>
        {Object.entries(statusName).map(([s, n]) => (
          <option value={s} key={s}>
            {n}
          </option>
        ))}
      </select>
      <div className="historylist">
        {visible.length === 0 && (
          <div className="empty">
            <div className="empty-icon">
              <ListVideo size={26} />
            </div>
            <strong>{jobs.length ? "没有匹配的任务" : "暂无任务记录"}</strong>
            <p>
              {jobs.length ? "可切换状态筛选。" : "生成任务将按时间排列于此。"}
            </p>
          </div>
        )}
        {visible.map((j) => (
          <article
            className={selected === j.job_id ? "selected" : ""}
            key={j.job_id}
          >
            <button className="historyitem" onClick={() => select(j)}>
              <span>
                <span className={"pill " + j.status}>
                  {statusName[j.status]}
                </span>
                <small>{modeName[j.mode]}</small>
              </span>
              <strong>{j.privacy_mode ? "保密任务" : j.prompt}</strong>
              <small>
                {new Date(j.created_at).toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </small>
            </button>
            {!(["queued", "running"] as Status[]).includes(j.status) && (
              <button
                disabled={busy}
                className="del icon"
                aria-label={j.privacy_mode ? "清理保密任务" : "删除任务"}
                onClick={() => setDeleting(j.job_id)}
              >
                <Trash2 size={14} />
              </button>
            )}
            {deleting === j.job_id && (
              <div className="confirm">
                <span>{j.privacy_mode ? "确认清理临时成片与任务记录？" : "删除此任务及文件？"}</span>
                <button
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await remove(j);
                      setDeleting("");
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  删除
                </button>
                <button aria-label="保留任务" onClick={() => setDeleting("")}>
                  <X size={14} />
                </button>
              </div>
            )}
          </article>
        ))}
        {hasMore && (
          <button className="loadmore" onClick={more}>
            加载更多 <ChevronRight size={14} />
          </button>
        )}
      </div>
    </aside>
  );
}
