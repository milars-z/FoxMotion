export type Mode = "text" | "image" | "reference";
export type Role =
  "first_frame" | "last_frame" | "ref_image" | "ref_video" | "ref_audio";
export type Status =
  "queued" | "running" | "completed" | "failed" | "canceled" | "interrupted";
export type Slots = Partial<Record<Role, { min: number; max: number }>>;
export type Options = {
  durations: number[];
  resolutions: string[];
  steps: number[];
  presets: string[];
  slots: Slots;
  combinations?: Partial<Record<Role, number>>[];
  samplers?: string[];
  schedulers?: string[];
  shifts?: number[];
  turbo_options?: boolean[];
};
export type Capability = {
  mode: Mode;
  family: string;
  ready: boolean;
  reason: string;
  options: Options | null;
};
export type Caps = {
  catalog?: Catalog;
  backend: string;
  modes: Capability[];
  max_file_mb: number;
  max_request_mb: number;
  media: Record<string, Record<string, string>>;
  mime_aliases?: Record<string, string>;
};
export type Params = {
  duration: number;
  resolution: string;
  seed: number;
  steps: number;
  preset: string;
  model_profile_id?: string;
  lora_id?: string | null;
  lora_strength?: number;
  sampler?: string | null;
  scheduler?: string | null;
  shift?: number | null;
  turbo_enabled?: boolean | null;
};
export type ModelProfile = { id: string; title: string; modes: Mode[] };
export type Lora = {
  id: string;
  title: string;
  preferred_model_profile: string;
  default_strength: number;
  strength_min: number;
  strength_max: number;
  author_reference_strength: number[];
  strength_source: string;
  sampling_source: string;
  recommended: {
    sampler: string;
    scheduler: string;
    steps: number;
    shift: number;
    turbo_enabled: boolean;
  };
};
export type Catalog = {
  profiles: ModelProfile[];
  loras: Lora[];
  default_profile_id: string;
  routes: {
    model_profile_id: string;
    lora_id: string | null;
    mode: Mode;
    capability: Capability;
    status: string;
    reason: string;
  }[];
};
export type Asset = { role: Role; file: File };
export type Job = {
  job_id: string;
  mode: Mode;
  prompt: string;
  status: Status;
  stage: string;
  parameters: Params;
  backend: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  input_files: { name: string; role: Role; size: number }[];
};
export type System = {
  backend: string;
  worker: { online: boolean; backend: string | null };
  python: string;
  disk_free_gib: number;
};
export const modeName = {
  text: "文生视频",
  image: "图生视频",
  reference: "参考生成",
};
export const statusName = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
  interrupted: "已中断",
};
export const roleName = {
  first_frame: "首帧图片",
  last_frame: "尾帧图片",
  ref_image: "参考图片",
  ref_video: "参考视频",
  ref_audio: "参考音频",
};
export function elapsed(job: Job) {
  if (!job.started_at) return "00:00";
  const seconds = Math.max(
    0,
    Math.floor(
      ((job.finished_at ? Date.parse(job.finished_at) : Date.now()) -
        Date.parse(job.started_at)) /
        1000,
    ),
  );
  return [Math.floor(seconds / 60), seconds % 60]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch("/api/" + path, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : "请求失败（HTTP " + res.status + "）",
    );
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
export function validAssets(options: Options, assets: Asset[], mode: Mode) {
  const count = (role: string) => assets.filter((a) => a.role === role).length;
  if (mode === "reference" && !assets.length) return false;
  if (assets.some((a) => !options.slots[a.role])) return false;
  if (
    !Object.entries(options.slots).every(
      ([r, s]) => count(r) >= s.min && count(r) <= s.max,
    )
  )
    return false;
  return (
    !options.combinations?.length ||
    options.combinations.some((c) =>
      Object.keys(options.slots).every((r) => count(r) === (c[r as Role] ?? 0)),
    )
  );
}
