"""Record a manually executed local ComfyUI smoke test. Does not submit prompts."""

import argparse
import os
import subprocess
from uuid import UUID, uuid4

import httpx

from backend.config import Settings
from backend.inference.base import InferenceError
from backend.inference.evidence import SmokeRecord, runtime_identity, sha256
from backend.inference.workflow import Manifest, demand, read
from backend.models.registry import H3, LORAS, PROFILES, manifest_path
from backend.services.files import inside, validate_media


def inspect_run(cfg, mode, lid, pid):
    manifest = manifest_path(cfg, mode, H3, lid)
    spec = Manifest.model_validate(read(manifest))
    with httpx.Client(base_url=cfg.comfyui_url, trust_env=False, timeout=10) as client:
        record = (
            client.get("/history/" + str(UUID(pid))).raise_for_status().json().get(pid)
        )
    demand(record, "本机 history 中没有该 prompt_id")
    graph = record["prompt"][2]

    def execution_inputs(g):
        return {key: (n["class_type"], n["inputs"]) for key, n in g.items()}

    for variant in spec.variants:
        workflow = inside(cfg.workflow_dir, variant.workflow)
        if execution_inputs(read(workflow)) == execution_inputs(graph):
            break
    else:
        raise InferenceError(
            "执行图与已导出的绑定工作流不一致，请导出实际运行的图（含固定种子）"
        )
    values = {
        key: [graph[t.node]["inputs"][t.input] for t in targets]
        for key, targets in variant.fields.items()
    }
    demand(
        values.get("turbo_enabled")
        and all(v is False for v in values["turbo_enabled"]),
        "基线验收需要 Turbo off",
    )
    if lid:
        demand(
            values.get("lora_strength") and all(v > 0 for v in values["lora_strength"]),
            "LoRA 验收需要非零强度",
        )
    weights = [(m, graph[m.node]["inputs"][m.input]) for m in variant.models]
    demand(
        [name for m, name in weights if m.base] == [PROFILES[H3].checkpoint],
        "运行图基础权重不符",
    )
    demand(
        [name for m, name in weights if m.folder == "loras"]
        == ([LORAS[lid].filename] if lid else []),
        "运行图 LoRA 不符",
    )
    generated = (
        record.get("status", {}).get("completed")
        and record["status"].get("status_str") == "success"
    )
    if generated:
        files = record["outputs"][variant.output_node][variant.output_key]
        videos = [
            f
            for f in files
            if f.get("type") == "output"
            and f["filename"].lower().endswith((".mp4", ".webm"))
        ]
        demand(len(videos) == 1, "无法核对本次产出的视频")
        f = videos[0]
        validate_media(
            inside(cfg.comfy_dir("output"), f.get("subfolder", ""), f["filename"]),
            "video",
        )
    compare = {
        k: v for k, v in values.items() if k not in ("output_prefix", "lora_strength")
    }
    compare["slots"] = {
        r: [graph[t.node]["inputs"][t.input] for t in targets]
        for r, targets in variant.slots.items()
    }
    return manifest, workflow, bool(generated), compare


def record(cfg, mode, lid, pid, baseline, unmatched, effect):
    demand(unmatched >= 0, "未匹配 key 数量必须非负")
    manifest, workflow, generated, params = inspect_run(cfg, mode, lid, pid)
    if not unmatched:
        demand(generated, "history 缺少成功执行或可读输出")
        if lid:
            demand(
                effect and baseline and baseline != pid, "需要独立基线与实际效果确认"
            )
            _, _, base_generated, base_params = inspect_run(cfg, mode, None, baseline)
            demand(
                base_generated and base_params == params,
                "基线的种子、采样参数、提示词或素材不一致",
            )
    commit, gpu, driver = runtime_identity(cfg)
    model_root = cfg.comfy_dir("model")
    receipt = SmokeRecord(
        bindings_sha256=sha256(manifest),
        workflow_sha256=sha256(workflow),
        checkpoint_sha256=sha256(
            inside(model_root, "diffusion_models", PROFILES[H3].checkpoint)
        ),
        lora_sha256=sha256(inside(model_root, "loras", LORAS[lid].filename))
        if lid
        else None,
        comfy_commit=commit,
        gpu=gpu,
        driver=driver,
        prompt_id=pid,
        baseline_prompt_id=baseline,
        result="incompatible" if unmatched else "passed",
        loaded=not unmatched,
        generated=generated,
        unmatched_keys=unmatched,
        effect_observed=effect,
    )
    directory = cfg.verification_dir
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{manifest.stem}.{receipt.workflow_sha256}.json"
    temp = directory / f"{uuid4()}.tmp"
    try:
        temp.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["text", "image"], required=True)
    parser.add_argument("--lora-id", choices=list(LORAS))
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--baseline-prompt-id")
    parser.add_argument(
        "--unmatched-keys",
        type=int,
        required=True,
        help="Count from the reviewed local loader report",
    )
    parser.add_argument(
        "--confirm-effect",
        action="store_true",
        help="Operator confirmed the LoRA A/B effect",
    )
    args = parser.parse_args()
    try:
        print(
            record(
                Settings(),
                args.mode,
                args.lora_id,
                args.prompt_id,
                args.baseline_prompt_id,
                args.unmatched_keys,
                args.confirm_effect,
            )
        )
    except (
        InferenceError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        httpx.HTTPError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(
            1,
            str(exc)
            if isinstance(exc, InferenceError)
            else "无法核对本机 history、权重或运行环境\n",
        )
