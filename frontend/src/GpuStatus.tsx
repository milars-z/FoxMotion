import { useEffect, useState } from "react";
import { api } from "./types";

type Data = {
  device: string; used: number; total: number; busy: boolean;
  releasing: boolean; queued_or_running: boolean;
  models: { name: string; bytes: number; device: string }[];
  cgroup: { used: number; limit: number | null; events: {max: number; oom: number; oom_kill: number} } | null;
};
const gib = (bytes: number) => (bytes / 2 ** 30).toFixed(1);

export function GpuStatus() {
  const [data, setData] = useState<Data>();
  const [error, setError] = useState("");
  const [releasing, setReleasing] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Data>("gpu");
        if (active) setData(next);
      } catch { if (active) setData(undefined); }
      if (active) timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  const busy = !data || data.busy || data.queued_or_running || data.releasing || releasing;
  return <section className="panel gpu-status" aria-label="GPU 状态">
    <div className="fieldhead"><strong>{data?.device ?? "GPU 状态不可用"}</strong>
      <button type="button" disabled={busy} onClick={async () => {
        setError(""); setReleasing(true);
        try { await api("gpu/release", {method:"POST",body:"{}"}); setData(await api<Data>("gpu")); }
        catch (e) { setError((e as Error).message); }
        finally { setReleasing(false); }
      }}>释放显存</button></div>
    {data && <><p>{data.releasing || releasing ? "正在释放" : data.busy ? "推理中" : data.queued_or_running ? "任务处理中" : "空闲"} · 显存 {gib(data.used)} / {gib(data.total)} GiB</p>
    {data.cgroup && <p>容器 RAM {gib(data.cgroup.used)} / {data.cgroup.limit ? gib(data.cgroup.limit)+" GiB" : "无限制"} · 触限 {data.cgroup.events.max} · OOM {data.cgroup.events.oom} / kill {data.cgroup.events.oom_kill}</p>}
    <details><summary>驻留模型（{data.models.length}）</summary>
      {data.models.map((m,i) => <div key={i}>{m.name} · {gib(m.bytes)} GiB</div>)}
    </details></>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
