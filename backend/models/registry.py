"""Local allowlist. Only registry IDs cross the job API boundary."""

from pydantic import BaseModel, ConfigDict

DEFAULT_PROFILE = "workflow_default"
H3 = "h3_fl2va_int8_convrot"
HM = "hmnsfw_extreme_realism"


class Profile(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    title: str
    modes: tuple[str, ...]
    family: str
    checkpoint: str | None = None
    bundled_lora: str | None = None


class Recommendation(BaseModel):
    sampler: str = "euler"
    scheduler: str = "beta"
    steps: int = 18
    shift: float = 12
    turbo_enabled: bool = False


class Lora(BaseModel):
    id: str = HM
    title: str = "HMNSFW Extreme Realism"
    filename: str = "HMNSFW-Extreme-Realism.safetensors"
    preferred_model_profile: str = H3
    base_family: str = "FL2VA"
    default_strength: float = 0.8
    strength_min: float = 0
    strength_max: float = 1.5
    author_reference_strength: tuple[float, float] = (0.6, 1.0)
    recommended: Recommendation = Recommendation()
    sampling_source: str = "作者 README"
    strength_source: str = "第三方转录，尚待本机验证"
    version_id: str = "3446189"


PROFILES = {
    DEFAULT_PROFILE: Profile(
        id=DEFAULT_PROFILE,
        title="原有工作流预设",
        family="workflow",
        modes=("text", "image", "reference"),
    ),
    "ref2va_turbo": Profile(
        id="ref2va_turbo", title="MiniMax H3 Ref2VA · Turbo 4-step", modes=("reference",),
        family="Ref2VA", checkpoint="minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        bundled_lora="minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
    ),
    H3: Profile(
        id=H3,
        title="MiniMax H3 FL2VA INT8 ConvRot (non-pruned)",
        family="FL2VA",
        checkpoint="minimax_h3_fl2va_int8_convrot.safetensors",
        modes=("text", "image"),
    ),
}
LORAS = {HM: Lora()}


def check_ids(profile_id, lora_id, mode=None):
    if profile_id not in PROFILES or (lora_id is not None and lora_id not in LORAS):
        raise ValueError("未知模型或 LoRA ID")
    profile = PROFILES[profile_id]
    if mode and mode not in profile.modes:
        raise ValueError("模型 profile 不支持当前模式")
    if lora_id and LORAS[lora_id].preferred_model_profile != profile_id:
        raise ValueError("LoRA 与 checkpoint profile 不兼容")


def manifest_path(cfg, mode, profile_id, lora_id):
    check_ids(profile_id, lora_id, mode)
    if mode not in ("text", "image", "reference"):
        raise ValueError("未知模式")
    stem = (
        mode
        if profile_id == DEFAULT_PROFILE
        else f"{profile_id}.{mode}.{lora_id or 'base'}"
    )
    return cfg.workflow_dir / "bindings" / f"{stem}.json"
