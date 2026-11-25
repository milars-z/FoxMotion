"""Compile administrator-reviewed exports into mode-specific, local prompt graphs."""

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from backend.models.jobs import (
    FAMILY,
    KINDS,
    Capability,
    Mode,
    Options,
    Parameters,
    Role,
    Slot,
    Strict,
)
from backend.models.registry import DEFAULT_PROFILE, LORAS, PROFILES, manifest_path
from backend.services.files import inside

from .base import InferenceError
from .evidence import require_smoke


class Target(Strict):
    node: str
    input: str


class Binding(Target):
    # Explicitly map a displayed duration to the workflow's frame count if needed.
    values: dict[str, int | float | str] = Field(default_factory=dict)


class Model(Target):
    folder: str
    base: bool = False


class LoaderContract(Strict):
    model_input: str
    model_output: int = Field(ge=0)
    model_type: str = Field(min_length=1)


class Variant(Strict):
    workflow: str
    fields: dict[str, list[Binding]]
    slots: dict[Role, list[Target]]
    models: list[Model] = Field(min_length=1)
    output_node: str
    output_key: str
    loader: LoaderContract | None = None
    reference_node: str | None = None


class Manifest(Strict):
    family: Literal["FL2VA", "Ref2VA"]
    durations: list[float] = Field(min_length=1)
    resolutions: list[str] = Field(min_length=1)
    steps: list[int] = Field(min_length=1)
    preset: str
    selectable_sampling: bool = False
    confirmed_loras: list[str] = Field(default_factory=list)
    samplers: list[str] = Field(default_factory=list)
    schedulers: list[str] = Field(default_factory=list)
    shifts: list[float] = Field(default_factory=list)
    variants: list[Variant] = Field(min_length=1)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def demand(condition, message):
    if not condition:
        raise InferenceError(message)


def safe_relative(name):
    return (
        name
        and not Path(name).is_absolute()
        and ":" not in name
        and "\\" not in name
        and not any(s in {"", ".", ".."} for s in name.split("/"))
    )


def translated(binding, value):
    if not binding.values:
        return value
    key = format(value, "g") if isinstance(value, float) else str(value)
    demand(key in binding.values, "bindings 缺少参数值映射")
    return binding.values[key]


class WorkflowAdapter:
    def __init__(
        self, cfg, mode: Mode, objects, profile_id=DEFAULT_PROFILE, lora_id=None
    ):
        self.mode = mode
        manifest_file = manifest_path(cfg, mode, profile_id, lora_id)
        self.profile = PROFILES[profile_id]
        self.advanced = bool(self.profile.checkpoint and not self.profile.bundled_lora)
        self.guarded_unet = 'FoxMotionUNETLoader' in objects
        self.lora = LORAS[lora_id] if lora_id else None
        demand(
            manifest_file.is_file(),
            "NOT_CONFIGURED / WORKFLOW_NOT_CONFIGURED · 尚未配置该组合的 API 工作流",
        )
        try:
            self.spec = Manifest.model_validate(read(manifest_file))
            spec = self.spec
            demand(spec.family == FAMILY[mode], "工作流模型家族与生成模式不符")
            self.graphs = []
            self.revocation_paths = {}
            combinations = []
            for variant in spec.variants:
                demand(safe_relative(variant.workflow), "工作流路径无效")
                graph = read(inside(cfg.workflow_dir, variant.workflow))
                self.verify(cfg, graph, variant, objects)
                if self.advanced:
                    self.revocation_paths[variant.workflow] = require_smoke(
                        cfg,
                        manifest_file,
                        inside(cfg.workflow_dir, variant.workflow),
                        inside(
                            cfg.comfy_dir("model"),
                            "diffusion_models",
                            self.profile.checkpoint,
                        ),
                        inside(cfg.comfy_dir("model"), "loras", self.lora.filename)
                        if self.lora
                        else None,
                    )
                counts = {k: len(v) for k, v in variant.slots.items()}
                demand(counts not in combinations, "重复的输入组合")
                combinations.append(counts)
                self.graphs.append((counts, variant, graph))
            limits = {
                r: Slot(
                    min=min(c.get(r, 0) for c in combinations),
                    max=max(c.get(r, 0) for c in combinations),
                )
                for r in KINDS
                if any(r in c for c in combinations)
            }
            dynamic = any(v.reference_node for _, v, _ in self.graphs)
            if dynamic:
                demand(len(self.graphs) == 1, "动态参考图只接受一个模板")
                limits = {r: Slot(max=n) for r, n in {"ref_image": 9, "ref_video": 3, "ref_audio": 3}.items()}
                combinations = []
            self.cap = Capability(
                mode=mode,
                family=spec.family,
                ready=True,
                options=Options(
                    durations=spec.durations,
                    resolutions=spec.resolutions,
                    steps=spec.steps,
                    presets=[spec.preset],
                    slots=limits,
                    reference_tags=dynamic,
                    combinations=combinations,
                    samplers=spec.samplers if self.advanced or spec.selectable_sampling else [],
                    schedulers=spec.schedulers if self.advanced or spec.selectable_sampling else [],
                    shifts=spec.shifts if self.advanced else [],
                    turbo_options=[False] if self.advanced else [],
                ),
            )
        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
        ) as err:
            raise InferenceError(
                "工作流配置无效，请检查文件、节点和 bindings 格式"
            ) from err

    def verify(self, cfg, graph, v, objects):
        demand(
            isinstance(graph, dict)
            and graph
            and "nodes" not in graph
            and "links" not in graph,
            "需要导出 API 格式的工作流 JSON",
        )
        if self.lora:
            declared_loaders = [
                graph.get(m.node, {}) for m in v.models if m.folder == "loras"
            ]
            demand(
                len(declared_loaders) == 1
                and declared_loaders[0].get("class_type") in objects,
                "LORA_NODE_MISSING · 本机未注册导出图所需的 loader",
            )
        for n in graph.values():
            demand(
                isinstance(n, dict)
                and isinstance(n.get("inputs"), dict)
                and n.get("class_type") in objects,
                "工作流节点未安装或不是 API 格式",
            )
            info = objects[n["class_type"]]
            demand(
                not info.get("api_node")
                and not str(info.get("category", "")).startswith("api node"),
                "工作流包含外部推理节点",
            )
            for linked in n["inputs"].values():
                if isinstance(linked, list):
                    demand(
                        len(linked) == 2
                        and isinstance(linked[0], str)
                        and linked[0] in graph
                        and isinstance(linked[1], int)
                        and linked[1] >= 0,
                        "工作流包含无效的节点连线",
                    )

        def input_metadata(target):
            demand(
                target.node in graph and target.input in graph[target.node]["inputs"],
                "bindings 指向不存在的节点输入",
            )
            meta = objects[graph[target.node]["class_type"]]["input"]
            defs = {**meta.get("required", {}), **meta.get("optional", {})}
            demand(target.input in defs, "节点输入与本机 object_info 不一致")
            demand(
                not isinstance(graph[target.node]["inputs"][target.input], list),
                "绑定应指向源节点的字面值输入",
            )
            return defs[target.input]

        fields = {
            "prompt",
            "seed",
            "width",
            "height",
            "duration",
            "steps",
            "output_prefix",
        }
        if self.spec.selectable_sampling:
            fields.update(("sampler", "scheduler"))
            for key, choices in (("sampler", self.spec.samplers), ("scheduler", self.spec.schedulers)):
                demand(bool(choices) and bool(v.fields.get(key)), "缺少采样配置")
                for binding in v.fields[key]:
                    definition = input_metadata(binding)
                    available = definition[0] if isinstance(definition[0], list) else definition[1].get("options", [])
                    demand(all(choice in available for choice in choices), "采样选项与本机节点不一致")
        if self.advanced:
            fields.update(("sampler", "scheduler", "shift", "turbo_enabled"))
            if self.lora:
                fields.add("lora_strength")
            demand(
                self.spec.samplers and self.spec.schedulers and self.spec.shifts,
                "缺少采样参数白名单",
            )
        demand(
            set(v.fields) == fields and all(v.fields.values()),
            "参数或输出前缀 bindings 不完整",
        )
        demand(v.output_node in graph and v.output_key, "输出节点未绑定")
        allowed = {
            "image": {"first_frame", "last_frame"},
            "text": set(),
            "reference": {"ref_image", "ref_video", "ref_audio"},
        }
        demand(
            set(v.slots) <= allowed[self.mode] and all(v.slots.values()),
            "素材角色与模式不匹配",
        )
        if self.mode == "image":
            demand(
                len(v.slots.get("first_frame", [])) == 1
                and len(v.slots.get("last_frame", [])) <= 1,
                "需要有效的首尾帧绑定",
            )
        if self.mode == "reference":
            demand(bool(v.slots) != bool(v.reference_node), "参考模式需要一种素材绑定")
        if v.reference_node:
            demand(self.mode == "reference" and graph.get(v.reference_node, {}).get("class_type")
                   == "MiniMaxH3ReferenceToVideo", "参考节点类型不匹配")
            demand(all(k in objects for k in ("LoadImage", "LoadAudio", "LoadVideo", "GetVideoComponents")),
                   "参考素材加载节点缺失")
        if self.profile.bundled_lora:
            demand(self.spec.confirmed_loras == [self.profile.bundled_lora]
                   and [graph[m.node]["inputs"][m.input] for m in v.models if m.folder == "loras"]
                   == [self.profile.bundled_lora], "Turbo LoRA 与预设不符")
        seen = set()
        for target in [
            t for items in [*v.fields.values(), *v.slots.values()] for t in items
        ] + v.models:
            input_metadata(target)
            key = (target.node, target.input)
            demand(key not in seen, "节点字段绑定重复")
            seen.add(key)
        demand(sum(m.base for m in v.models) == 1, "请明确绑定唯一的基础扩散模型")
        declared = set()
        for model in v.models:
            name = graph[model.node]["inputs"][model.input]
            demand(
                isinstance(name, str)
                and safe_relative(name)
                and safe_relative(model.folder),
                "模型相对路径无效",
            )
            model_file = inside(cfg.comfy_dir("model"), model.folder, name)
            demand(
                model_file.is_file() and model_file.stat().st_size > 0,
                "MODEL_NOT_FOUND · 缺少模型文件：" + name,
            )
            definition = input_metadata(model)
            choices = (
                definition[0]
                if isinstance(definition[0], list)
                else (
                    definition[1].get("options")
                    if len(definition) > 1 and isinstance(definition[1], dict)
                    else None
                )
            )
            if choices is not None:
                demand(name in choices, "ComfyUI 未识别模型：" + name)
            if model.base:
                if self.profile.checkpoint:
                    demand(
                        name == self.profile.checkpoint,
                        "CHECKPOINT_MISMATCH · 导出图与注册的 checkpoint 不一致",
                    )
                family = self.spec.family.lower()
                demand(
                    model.folder == "diffusion_models"
                    and re.search(
                        r"(?:^|[_-])" + family + r"(?:[_\-.]|$)",
                        Path(name).name.lower(),
                    ),
                    "基础权重与 FL2VA / Ref2VA 家族不匹配",
                )
            if model.folder == "diffusion_models":
                other = "ref2va" if self.spec.family == "FL2VA" else "fl2va"
                demand(other not in name.lower(), "工作流混用了两种基础模型家族")
            if model.folder.lower() == "loras" or "lora" in name.lower():
                if self.advanced or Path(name).name.casefold() in {
                    l.filename.casefold() for l in LORAS.values()
                }:
                    demand(
                        self.lora
                        and model.folder == "loras"
                        and name == self.lora.filename,
                        "LORA_NODE_MISSING · 此配置禁止额外或未经验证的 LoRA",
                    )
                demand(
                    name in self.spec.confirmed_loras, "需要明确确认 LoRA 文件与兼容性"
                )
            declared.add((model.node, model.input))
        for node_id, n in graph.items():
            for key, value in n["inputs"].items():
                if isinstance(value, str) and value.lower().endswith(
                    (".safetensors", ".ckpt", ".gguf", ".pth", ".pt", ".bin")
                ):
                    demand((node_id, key) in declared, "存在未声明的模型绑定")
        spec = self.spec
        if self.advanced:
            self.verify_profile(graph, v, objects, input_metadata)
        for r in spec.resolutions:
            Parameters(
                duration=spec.durations[0],
                resolution=r,
                steps=spec.steps[0],
                preset=spec.preset,
                seed=0,
            )
        demand(
            all(0 < d <= 600 for d in spec.durations)
            and all(1 <= s <= 200 for s in spec.steps),
            "生成参数越界",
        )
        sizes = [list(map(int, r.split("x"))) for r in spec.resolutions]
        for field, values in {
            "duration": spec.durations,
            "steps": spec.steps,
            "width": [r[0] for r in sizes],
            "height": [r[1] for r in sizes],
        }.items():
            for binding in v.fields[field]:
                for value in values:
                    value_out = translated(binding, value)
                    meta = input_metadata(binding)
                    bounds = (
                        meta[1] if len(meta) > 1 and isinstance(meta[1], dict) else {}
                    )
                    if meta[0] in ("INT", "FLOAT"):
                        demand(
                            isinstance(value_out, (int, float))
                            and (meta[0] != "INT" or int(value_out) == value_out)
                            and bounds.get("min", value_out)
                            <= value_out
                            <= bounds.get("max", value_out),
                            "参数选项与 ComfyUI 节点范围不匹配",
                        )
                    if isinstance(meta[0], list):
                        demand(value_out in meta[0], "参数枚举与 ComfyUI 节点不匹配")
        for field in ("prompt", "seed", "output_prefix"):
            demand(
                all(not b.values for b in v.fields[field]), "动态字段不能使用固定值映射"
            )
        for kind in ("input", "output"):
            demand(cfg.comfy_dir(kind).is_dir(), f"本机 ComfyUI {kind} 目录缺失")

    def verify_profile(self, graph, variant, objects, metadata):
        """Check only declared bindings; there is no assumed H3 loader class."""
        models = variant.models
        demand(
            any(m.folder == "text_encoders" for m in models)
            and len(
                {graph[m.node]["inputs"][m.input] for m in models if m.folder == "vae"}
            )
            >= 2,
            "MODEL_NOT_FOUND · 需要绑定编码器、视频 VAE 和音频 VAE",
        )
        for field, allowed in (
            ("sampler", self.spec.samplers),
            ("scheduler", self.spec.schedulers),
        ):
            for binding in variant.fields[field]:
                meta = metadata(binding)
                choices = (
                    meta[0]
                    if isinstance(meta[0], list)
                    else (
                        meta[1].get("options", [])
                        if len(meta) > 1 and isinstance(meta[1], dict)
                        else []
                    )
                )
                demand(
                    all(translated(binding, val) in choices for val in allowed),
                    "采样选项与本机 object_info 不符",
                )
        for binding in variant.fields["turbo_enabled"]:
            demand(
                metadata(binding)[0] == "BOOLEAN" and not binding.values,
                "Turbo 必须显式绑定布尔输入",
            )
        for field, values in (
            ("shift", self.spec.shifts),
            ("lora_strength", [0, 1.5] if self.lora else []),
        ):
            for binding in variant.fields.get(field, []):
                meta = metadata(binding)
                bounds = meta[1] if len(meta) > 1 else {}
                demand(
                    meta[0] in ("FLOAT", "INT")
                    and not binding.values
                    and all(
                        bounds.get("min", x) <= x <= bounds.get("max", x)
                        for x in values
                    ),
                    "数值范围与节点不符",
                )
                if field == "lora_strength":
                    demand(meta[0] == "FLOAT", "LoRA strength 需要浮点输入")
        loaders = [m for m in models if m.folder == "loras"]
        demand(
            len(loaders) == int(bool(self.lora)),
            "LORA_NODE_MISSING · 需要独立的基座图或单 LoRA 图",
        )
        if not self.lora:
            demand(variant.loader is None, "基座图必须绕过 LoRA")
            return
        loader = loaders[0]
        contract = variant.loader
        demand(
            contract
            and all(b.node == loader.node for b in variant.fields["lora_strength"]),
            "LORA_NODE_MISSING · 缺少 loader 类型声明或强度绑定",
        )
        node = graph[loader.node]
        info = objects[node["class_type"]]
        inputs = {
            **info["input"].get("required", {}),
            **info["input"].get("optional", {}),
        }
        link = node["inputs"].get(contract.model_input)

        def output_type(node_id, index):
            outputs = objects[graph[node_id]["class_type"]].get("output", [])
            return outputs[index] if 0 <= index < len(outputs) else None

        demand(
            isinstance(link, list)
            and inputs.get(contract.model_input, [None])[0] == contract.model_type
            and output_type(*link) == contract.model_type
            and output_type(loader.node, contract.model_output) == contract.model_type,
            "LORA_NODE_MISSING · loader 输入输出与 object_info 类型不匹配",
        )

        def upstream(start):
            pending = [start]
            seen = set()
            while pending:
                node_id = pending.pop()
                if node_id not in seen:
                    seen.add(node_id)
                    pending.extend(
                        val[0]
                        for val in graph[node_id]["inputs"].values()
                        if isinstance(val, list)
                    )
            return seen

        base = next(m.node for m in models if m.base)
        consumers = [
            (objects[graph[n]["class_type"]]["input"], field)
            for n in upstream(variant.output_node)
            for field, value in graph[n]["inputs"].items()
            if value == [loader.node, contract.model_output]
        ]
        demand(
            base in upstream(link[0])
            and consumers
            and all(
                {**meta.get("required", {}), **meta.get("optional", {})}.get(
                    field, [None]
                )[0]
                in (contract.model_type, "*")
                for meta, field in consumers
            ),
            "LORA_NODE_MISSING · 输出路径未使用绑定的基座和 LoRA",
        )

    def render(self, req, uploads, prefix):
        counts = dict(Counter(req.roles))
        for expected, v, original in self.graphs:
            if not v.reference_node and counts != expected:
                continue
            graph = copy.deepcopy(original)
            if self.advanced:
                for model in v.models:
                    if model.base:
                        graph[model.node]["inputs"][model.input] = (
                            self.profile.checkpoint
                        )
                    elif model.folder == "loras":
                        graph[model.node]["inputs"][model.input] = self.lora.filename
            width, height = map(int, req.parameters.resolution.split("x"))
            values = {
                **req.parameters.model_dump(),
                "prompt": req.prompt,
                "width": width,
                "height": height,
                "output_prefix": prefix,
            }
            for key, targets in v.fields.items():
                for t in targets:
                    graph[t.node]["inputs"][t.input] = translated(t, values[key])
            for role, targets in v.slots.items():
                for t, name in zip(targets, uploads[role], strict=True):
                    graph[t.node]["inputs"][t.input] = name
            if self.guarded_unet:
                for m in v.models:
                    if m.base and graph[m.node]["class_type"] == "UNETLoader":
                        graph[m.node]["class_type"] = "FoxMotionUNETLoader"
            if v.reference_node:
                bind_references(graph, v.reference_node, uploads)
            return graph, v
        raise InferenceError("没有与素材数量匹配的已验证工作流")


def bind_references(graph, target, uploads):
    inputs = graph[target]["inputs"]
    loaders = {"ref_image": ("LoadImage", "image"), "ref_video": ("LoadVideo", "file"),
               "ref_audio": ("LoadAudio", "audio")}
    for role, (kind, field) in loaders.items():
        for index, filename in enumerate(uploads.get(role, [])):
            node = f"studio_{role}_{index}"
            graph[node] = {"class_type": kind, "inputs": {field: filename}}
            if role == "ref_video":
                split = node + "_frames"
                graph[split] = {"class_type": "GetVideoComponents", "inputs": {"video": [node, 0]}}
                node = split
            inputs[f"{role}s.{role}_{index}"] = [node, 0]
