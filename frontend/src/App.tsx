import { useEffect, useRef, useState } from "react";
import { Cpu, HardDrive, LockKeyhole, Radio } from "lucide-react";
import { api, Caps, Job, System } from "./types";
import { Controls } from "./Controls";
import { History } from "./History";
import { Monitor } from "./Monitor";
import "./style.css";

export function App() {
  const [caps, setCaps] = useState<Caps | null>(null),
    [sys, setSys] = useState<System | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]),
    [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Job | null>(null);
  const [limit, setLimit] = useState(50),
    [hasMore, setMore] = useState(false),
    [err, setErr] = useState(""),
    [offline, setOffline] = useState("");
  const [busy, setBusy] = useState(false),
    [revision, bump] = useState(0);
  const selection = useRef(selected);
  selection.current = selected;
  useEffect(() => {
    let alive = true,
      timer: ReturnType<typeof setTimeout>,
      iteration = 0;
    async function poll() {
      try {
        const readPages = async () => {
          const rows: Job[] = [];
          while (rows.length <= limit) {
            const size = Math.min(limit + 1 - rows.length, 200);
            const page = await api<Job[]>(
              "jobs?limit=" + size + "&offset=" + rows.length,
            );
            rows.push(...page);
            if (page.length < size) break;
          }
          return rows;
        };
        const [rows, s, c] = await Promise.all([
          readPages(),
          api<System>("system"),
          iteration % 8 === 0
            ? api<Caps>("capabilities")
            : Promise.resolve(null),
        ]);
        if (!alive) return;
        setJobs(rows.slice(0, limit));
        setMore(rows.length > limit);
        setSys(s);
        if (c) setCaps(c);
        if (!selection.current && rows.length) setSelected(rows[0].job_id);
        if (
          selection.current &&
          !rows.some((j) => j.job_id === selection.current)
        ) {
          const chosen = await api<Job>("jobs/" + selection.current).catch(
            () => null,
          );
          if (!alive) return;
          setDetail(chosen);
          if (!chosen) setSelected(rows[0]?.job_id || "");
        }
        setOffline("");
        iteration++;
      } catch {
        if (alive) setOffline("无法连接本地服务，请检查 API 和 Worker。");
      }
      if (alive) timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [revision, limit]);
  const job =
    jobs.find((j) => j.job_id === selected) ??
    (detail?.job_id === selected ? detail : null);
  const added = (j: Job) => {
    setDetail(j);
    setSelected(j.job_id);
    setErr("");
    bump((x) => x + 1);
  };
  const action = async (path: string) => {
    if (!job) return;
    setBusy(true);
    try {
      added(
        await api<Job>("jobs/" + job.job_id + "/" + path, { method: "POST" }),
      );
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (j: Job) => {
    try {
      await api("jobs/" + j.job_id, { method: "DELETE" });
      if (selected === j.job_id) {
        setSelected("");
        setDetail(null);
      }
      setErr("");
      bump((x) => x + 1);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const online =
    !!sys?.worker.online && sys.worker.backend === caps?.backend && !offline;
  return (
    <>
      <header className="top">
        <div className="brand">
          <span className="mark" aria-hidden="true">
            H3
          </span>
          <b>H3 Video Studio</b>
        </div>
        <span className="topdiv" />
        <span className="worklabel">创作空间</span>
        <div className="topright">
          <LockKeyhole size={14} /> 私有工作区
        </div>
      </header>
      <main>
        <div className="pageheading">
          <div>
            <div className="eyebrow">
              <span /> GENERATION STUDIO
            </div>
            <h1>视频生成工作台</h1>
          </div>
          <div className={"engine " + (online ? "on" : "")}>
            <i />
            {online ? "推理引擎在线" : "等待引擎"}
            <em />
            {caps?.backend === "comfyui"
              ? "ComfyUI"
              : caps?.backend === "mock"
                ? "Mock"
                : "—"}
          </div>
        </div>
        {(err || offline) && (
          <div role="alert" className="banner">
            {err || offline}
            <button
              onClick={() => {
                setErr("");
                bump((x) => x + 1);
              }}
            >
              刷新
            </button>
          </div>
        )}
        <div className="layout">
          {caps ? (
            <Controls caps={caps} online={online} onJob={added} />
          ) : (
            <section className="panel loading">正在读取生成能力…</section>
          )}
          <Monitor
            job={job}
            backend={caps?.backend ?? ""}
            action={action}
            busy={busy}
          />
          <History
            jobs={jobs}
            selected={selected}
            select={(j) => {
              setDetail(j);
              setSelected(j.job_id);
            }}
            remove={remove}
            more={() => setLimit((x) => x + 50)}
            hasMore={hasMore}
          />
        </div>
        <footer>
          <span>
            <Radio size={14} /> 本地连接
          </span>
          <span>
            <Cpu size={14} /> Worker {online ? "在线" : "离线"}
          </span>
          <span>
            <HardDrive size={14} /> 可用空间 {sys?.disk_free_gib ?? "—"} GiB
          </span>
          <b>H3 VIDEO STUDIO</b>
        </footer>
      </main>
    </>
  );
}
