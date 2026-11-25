import { Catalog, Mode, Lora } from "./types";

type Props = {
  catalog: Catalog;
  mode: Mode;
  profile?: string;
  lora?: Lora;
  strength: number;
  route?: Catalog["routes"][number];
  onProfile: (id: string) => void;
  onLora: (lora: Lora | undefined) => void;
  onStrength: (strength: number) => void;
};
export function ProfilePicker({
  catalog,
  mode,
  profile,
  lora,
  strength,
  route,
  onProfile,
  onLora,
  onStrength,
}: Props) {
  const fixed = catalog.profiles.find(p => p.id === profile)?.bundled_lora;
  return (
    <div className="profile-select">
      <label>
        Checkpoint profile
        <select
          value={profile}
          onChange={(e) => {
            onProfile(e.target.value);
          }}
        >
          {catalog.profiles.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.modes.includes(mode)}>
              {p.id === catalog.default_profile_id && mode === "reference" ? "MiniMax H3 Ref2VA · 基座" : p.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        LoRA
        <select disabled={!!fixed}
          value={lora?.id ?? ""}
          onChange={(e) => {
            const selected = catalog.loras.find((l) => l.id === e.target.value);
            onLora(selected);
          }}
        >
          <option value="">{fixed ?? (profile === catalog.default_profile_id ? "由工作流指定" : "None（关闭）")}</option>
          {catalog.loras.map((l) => (
            <option
              key={l.id}
              value={l.id}
              disabled={l.preferred_model_profile !== profile}
            >
              {l.title}
            </option>
          ))}
        </select>
      </label>
      {!fixed && !catalog.loras.some((l) => l.preferred_model_profile === profile) && (
        <small>当前 profile 没有已注册的可选 LoRA。</small>
      )}
      {lora && (
        <label>
          LoRA 强度 · {strength}
          <input
            type="range"
            min={lora.strength_min}
            max={lora.strength_max}
            step="0.01"
            value={strength}
            aria-label="LoRA 强度"
            onChange={(e) => onStrength(Number(e.target.value))}
          />
          <small>
            参考区间 {lora.author_reference_strength.join("–")}（
            {lora.strength_source}）。0 使用独立基座图。
          </small>
        </label>
      )}
      {route && (
        <small>
          {fixed ? "工作流配置" : route.status === "ready" ? "真实推理已验证" : "真实推理待验证"} ·{" "}
          {route.status} · {route.reason}
        </small>
      )}
    </div>
  );
}
