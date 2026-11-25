import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .registry import DEFAULT_PROFILE, LORAS, check_ids

Mode = Literal["text", "image", "reference"]
Role = Literal["first_frame", "last_frame", "ref_image", "ref_video", "ref_audio"]
Status = Literal["queued", "running", "completed", "failed", "canceled", "interrupted"]
MODES = ("text", "image", "reference")
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
FAMILY = {"text": "FL2VA", "image": "FL2VA", "reference": "Ref2VA"}
KINDS = {
    "first_frame": "image",
    "last_frame": "image",
    "ref_image": "image",
    "ref_video": "video",
    "ref_audio": "audio",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Parameters(Strict):
    duration: float = Field(gt=0, le=600)
    resolution: str = Field(pattern=r"^[1-9]\d{1,3}x[1-9]\d{1,3}$")
    seed: int = Field(ge=-1, le=2147483647)
    steps: int = Field(ge=1, le=200)
    preset: str = Field(min_length=1, max_length=100)
    model_profile_id: str = DEFAULT_PROFILE
    lora_id: str | None = None
    lora_strength: float = Field(0, ge=0, le=1.5, allow_inf_nan=False)
    sampler: str | None = Field(None, max_length=80)
    scheduler: str | None = Field(None, max_length=80)
    shift: float | None = Field(None, ge=0, allow_inf_nan=False)
    turbo_enabled: bool | None = None

    @model_validator(mode="after")
    def selection(self):
        check_ids(self.model_profile_id, self.lora_id)
        if self.lora_id and "lora_strength" not in self.model_fields_set:
            self.lora_strength = LORAS[self.lora_id].default_strength
        if self.lora_id is None and self.lora_strength:
            raise ValueError("未选择 LoRA 时强度应为 0")
        return self

    @property
    def active_lora(self):
        return self.lora_id if self.lora_strength > 0 else None


class Request(Strict):
    mode: Mode
    prompt: str = Field(min_length=1, max_length=4000)
    parameters: Parameters
    roles: list[Role] = Field(default_factory=list, max_length=20)

    @field_validator("prompt")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入提示词")
        return value.strip()


class Slot(Strict):
    min: int = Field(0, ge=0, le=20)
    max: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def ordered(self):
        if self.min > self.max:
            raise ValueError("输入数量范围无效")
        return self


class Options(Strict):
    durations: list[float] = Field(min_length=1)
    resolutions: list[str] = Field(min_length=1)
    steps: list[int] = Field(min_length=1)
    presets: list[str] = Field(min_length=1)
    slots: dict[Role, Slot] = Field(default_factory=dict)
    reference_tags: bool = False
    combinations: list[dict[Role, int]] = Field(default_factory=list)
    samplers: list[str] = Field(default_factory=list)
    schedulers: list[str] = Field(default_factory=list)
    shifts: list[float] = Field(default_factory=list)
    turbo_options: list[bool] = Field(default_factory=list)


class Capability(Strict):
    mode: Mode
    family: str
    ready: bool
    reason: str = ""
    options: Options | None = None


class InputFile(Strict):
    role: Role
    name: str
    size: int
    mime: str


class Job(Strict):
    job_id: str
    mode: Mode
    prompt: str
    parameters: Parameters
    input_files: list[InputFile]
    backend: Literal["mock", "comfyui"]
    status: Status
    stage: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    error_message: str | None
    comfy_prompt_id: str | None
    cancel_requested: bool


def validate_request(req: Request, cap: Capability):
    check_ids(req.parameters.model_profile_id, req.parameters.lora_id, req.mode)
    if not cap.ready or cap.options is None:
        raise RuntimeError(cap.reason or "模型未就绪")
    o, p = cap.options, req.parameters
    for field, choices in (
        ("sampler", o.samplers),
        ("scheduler", o.schedulers),
        ("shift", o.shifts),
        ("turbo_enabled", o.turbo_options),
    ):
        value = getattr(p, field)
        if value is None and choices:
            setattr(p, field, choices[0])
        elif value is not None and value not in choices:
            raise ValueError(f"{field} 不在当前工作流支持范围内")
    if (
        p.duration not in o.durations
        or p.resolution not in o.resolutions
        or p.steps not in o.steps
        or p.preset not in o.presets
    ):
        raise ValueError("参数不在当前工作流支持范围内")
    if any(r not in o.slots for r in req.roles):
        raise ValueError("当前模式不支持此类素材")
    for role, slot in o.slots.items():
        if not slot.min <= req.roles.count(role) <= slot.max:
            raise ValueError(f"{role} 需要 {slot.min}–{slot.max} 个文件")
    if req.mode == "reference" and not req.roles:
        raise ValueError("请至少提供一个参考素材")
    counts = {r: req.roles.count(r) for r in set(req.roles)}
    if o.reference_tags:
        roles = {"Picture": "ref_image", "Video": "ref_video", "Audio": "ref_audio"}
        for kind, index in re.findall(r"<(Picture|Video|Audio)\s+([^>]*)>", req.prompt):
            if not re.fullmatch(r"[1-9]\d{0,2}", index) or int(index) > counts.get(roles[kind], 0):
                raise ValueError(f"参考索引 <{kind} {index}> 超出已上传素材范围")
    if o.combinations and counts not in o.combinations:
        raise ValueError("当前工作流未配置这种素材组合")
