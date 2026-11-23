import { useState } from "react";
import {
  Clapperboard,
  Dices,
  Image,
  Layers3,
  SlidersHorizontal,
  Sparkles,
  Type,
} from "lucide-react";
import {
  api,
  Asset,
  Capability,
  Caps,
  Job,
  Mode,
  modeName,
  roleName,
  Role,
  validAssets,
} from "./types";
import { Uploads } from "./Uploads";
import { ProfilePicker } from "./ProfilePicker";

function Form({
  cap: legacy,
  caps,
  onJob,
  online,
}: {
  cap: Capability;
  caps: Caps;
  onJob: (j: Job) => void;
  online: boolean;
}) {
  const catalog = caps.catalog;
  const [profileChoice, setProfile] = useState<string>();
  const profile = profileChoice ?? catalog?.default_profile_id;
  const [loraId, setLora] = useState<string | null>(null);
  const [strength, setStrength] = useState(0);
  const [sample, setSample] = useState(""),
    [schedule, setSchedule] = useState("");
  const [shift, setShift] = useState<number>(),
    [turbo, setTurbo] = useState(false);
  const lora = catalog?.loras.find((l) => l.id === loraId);
  const advanced = !!catalog && profile !== catalog.default_profile_id;
  const route = catalog?.routes.find(
    (r) =>
      r.model_profile_id === profile &&
      r.mode === legacy.mode &&
      r.lora_id === (strength > 0 ? loraId : null),
  );
  const cap = advanced
    ? (route?.capability ?? {
        ...legacy,
        ready: false,
        reason: "当前模式没有此模型组合",
        options: null,
      })
    : legacy;
  const opt = cap.options;
  const [prompt, setPrompt] = useState(""),
    [seed, setSeed] = useState("-1"),
    [assets, setAssets] = useState<Asset[]>([]);
  const [durationChoice, setDuration] = useState(opt?.durations[0]),
    [resChoice, setRes] = useState(opt?.resolutions[0]);
  const [stepChoice, setSteps] = useState(opt?.steps[0]),
    [presetChoice, setPreset] = useState(opt?.presets[0]);
  const choose = <T,>(choices: T[] | undefined, value: T | undefined) =>
    value !== undefined && choices?.includes(value) ? value : choices?.[0];
  const duration = choose(opt?.durations, durationChoice),
    res = choose(opt?.resolutions, resChoice);
  const steps = choose(opt?.steps, stepChoice),
    preset = choose(opt?.presets, presetChoice);
  const sampler = choose(opt?.samplers, sample),
    scheduler = choose(opt?.schedulers, schedule);
  const actualShift = choose(opt?.shifts, shift),
    actualTurbo = choose(opt?.turbo_options, turbo);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const selectors = catalog && (
    <ProfilePicker
      catalog={catalog}
      mode={legacy.mode}
      profile={profile}
      lora={lora}
      strength={strength}
      route={route}
      onStrength={setStrength}
      onProfile={(id) => {
        setProfile(id);
        setLora(null);
        setStrength(0);
        setError("");
      }}
      onLora={(selected) => {
        setLora(selected?.id ?? null);
        setStrength(selected?.default_strength ?? 0);
        setError("");
      }}
    />
  );
  if (!cap.ready || !opt)
    return (
      <div className="unready">
        {selectors}
        <Clapperboard />
        <strong>模式尚未就绪</strong>
        <p>{cap.reason}</p>
      </div>
    );
  const valid = validAssets(opt, assets, cap.mode);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy || !valid || !prompt.trim() || !online) return;
        setBusy(true);
        setError("");
        try {
          if (
            assets.reduce((n, a) => n + a.file.size, 0) >
            caps.max_request_mb * 1024 ** 2
          )
            throw Error("素材总量超过上传上限");
          const data = new FormData();
          data.set(
            "payload",
            JSON.stringify({
              mode: cap.mode,
              prompt,
              parameters: {
                duration,
                resolution: res,
                steps,
                preset,
                seed: Number(seed),
                ...(catalog
                  ? {
                      model_profile_id: profile,
                      lora_id: loraId,
                      lora_strength: strength,
                    }
                  : {}),
                ...(advanced
                  ? {
                      sampler,
                      scheduler,
                      shift: actualShift,
                      turbo_enabled: actualTurbo,
                    }
                  : {}),
              },
              roles: assets.map((a) => a.role),
            }),
          );
          assets.forEach((a) => data.append("files", a.file));
          onJob(await api<Job>("jobs", { method: "POST", body: data }));
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="formbody">
        <Uploads
          {...{ options: opt, assets, setAssets, caps, onError: setError }}
        />
        {opt.combinations?.length ? (
          <small>
            支持的素材组合：
            {opt.combinations
              .map((c) =>
                Object.entries(c)
                  .map(([r, n]) => roleName[r as Role] + " ×" + n)
                  .join(" + "),
              )
              .filter(Boolean)
              .join(" / ")}
          </small>
        ) : null}
        <div className="fieldhead">
          <label htmlFor="prompt">场景提示词</label>
          <small className="mono">{prompt.length} / 4000</small>
        </div>
        <textarea
          id="prompt"
          maxLength={4000}
          required
          placeholder="描述画面主体、运动方式与镜头语言…"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <div className="sectionline">
          <span>视频参数</span>
          <i />
        </div>
        <div className="fieldgrid">
          <label>
            视频时长
            <select
              value={duration}
              onChange={(e) => setDuration(+e.target.value)}
            >
              {opt.durations.map((n) => (
                <option key={n} value={n}>
                  {n} 秒
                </option>
              ))}
            </select>
          </label>
          <label>
            分辨率
            <select value={res} onChange={(e) => setRes(e.target.value)}>
              {opt.resolutions.map((n) => (
                <option key={n} value={n}>
                  {n.replace("x", " × ")}
                </option>
              ))}
            </select>
          </label>
          <label>
            推理步数
            <select value={steps} onChange={(e) => setSteps(+e.target.value)}>
              {opt.steps.map((n) => (
                <option value={n} key={n}>
                  {n} 步
                </option>
              ))}
            </select>
          </label>
          <label>
            随机种子
            <div className="seed">
              <input
                aria-label="随机种子"
                required
                type="number"
                min={-1}
                max={2147483647}
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
              />
              <button
                type="button"
                title="生成随机种子"
                aria-label="生成随机种子"
                onClick={() =>
                  setSeed(
                    String(crypto.getRandomValues(new Uint32Array(1))[0] >>> 1),
                  )
                }
              >
                <Dices size={15} />
              </button>
            </div>
          </label>
        </div>
        <label>
          模型预设
          <select value={preset} onChange={(e) => setPreset(e.target.value)}>
            {opt.presets.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        {selectors}
        {advanced && (
          <div className="profile-select">
            <div className="fieldgrid">
              <label>
                Sampler
                <select
                  value={sampler}
                  onChange={(e) => setSample(e.target.value)}
                >
                  {opt.samplers?.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                Scheduler
                <select
                  value={scheduler}
                  onChange={(e) => setSchedule(e.target.value)}
                >
                  {opt.schedulers?.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                Shift
                <select
                  value={actualShift}
                  onChange={(e) => setShift(+e.target.value)}
                >
                  {opt.shifts?.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label>
                Turbo
                <select
                  value={String(actualTurbo)}
                  onChange={(e) => setTurbo(e.target.value === "true")}
                >
                  {opt.turbo_options?.map((s) => (
                    <option value={String(s)} key={String(s)}>
                      {s ? "开启" : "关闭"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {lora && (
              <>
                <small>
                  {lora.sampling_source} · {lora.recommended.sampler} /{" "}
                  {lora.recommended.scheduler} · {lora.recommended.steps} 步 ·
                  Shift {lora.recommended.shift} · Turbo{" "}
                  {lora.recommended.turbo_enabled ? "开" : "关"}
                </small>
                <button
                  type="button"
                  className="loadmore"
                  onClick={() => {
                    const r = lora.recommended;
                    if (
                      !opt.samplers?.includes(r.sampler) ||
                      !opt.schedulers?.includes(r.scheduler) ||
                      !opt.steps.includes(r.steps) ||
                      !opt.shifts?.includes(r.shift) ||
                      !opt.turbo_options?.includes(r.turbo_enabled)
                    ) {
                      setError("当前工作流不支持完整推荐参数");
                      return;
                    }
                    setSample(r.sampler);
                    setSchedule(r.scheduler);
                    setSteps(r.steps);
                    setShift(r.shift);
                    setTurbo(r.turbo_enabled);
                    setError("");
                  }}
                >
                  应用推荐参数
                </button>
              </>
            )}
          </div>
        )}
      </div>
      <div className="submitarea">
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {!online && <small>等待 Worker 连接</small>}
        <button
          className="generate"
          disabled={busy || !online || !valid || !prompt.trim()}
        >
          <Sparkles size={17} />
          {busy ? "正在提交…" : "生成视频"}
        </button>
      </div>
    </form>
  );
}
export function Controls({
  caps,
  online,
  onJob,
}: {
  caps: Caps;
  online: boolean;
  onJob: (j: Job) => void;
}) {
  const [mode, setMode] = useState<Mode>("text"),
    cap = caps.modes.find((c) => c.mode === mode)!;
  const icons = { text: Type, image: Image, reference: Layers3 };
  return (
    <section className="panel controls">
      <div className="paneltitle">
        <SlidersHorizontal size={17} />
        <h2>生成设置</h2>
      </div>
      <div className="modebar">
        {caps.modes.map((c) => {
          const Icon = icons[c.mode];
          return (
            <button
              key={c.mode}
              aria-pressed={mode === c.mode}
              onClick={() => setMode(c.mode)}
            >
              <Icon size={16} />
              {modeName[c.mode]}
            </button>
          );
        })}
      </div>
      <Form
        key={mode + JSON.stringify(cap)}
        {...{ cap, caps, online, onJob }}
      />
    </section>
  );
}
