"""Operator-reviewed local smoke-test records, bound to the files actually used."""

import hashlib
import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .base import InferenceError


@lru_cache(maxsize=64)
def _digest(path, size, mtime, ctime, inode):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sha256(path: Path):
    stat = path.stat()
    return _digest(
        path.resolve(), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
    )


class SmokeRecord(BaseModel):
    bindings_sha256: str
    workflow_sha256: str
    checkpoint_sha256: str
    lora_sha256: str | None
    comfy_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    gpu: str = Field(min_length=1)
    driver: str = Field(min_length=1)
    prompt_id: str = Field(min_length=1)
    baseline_prompt_id: str | None = None
    result: Literal["passed", "incompatible"]
    loaded: bool
    generated: bool
    unmatched_keys: int = Field(ge=0)
    effect_observed: bool


def runtime_identity(cfg):
    commit = subprocess.check_output(
        ["git", "-C", str(cfg.comfyui_home), "rev-parse", "HEAD"],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=5,
    ).strip()
    card = subprocess.check_output(
        [
            "nvidia-smi",
            "-i",
            "0",
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    gpu, driver = (part.strip() for part in card.strip().rsplit(",", 1))
    return commit, gpu, driver


def require_smoke(cfg, manifest, workflow, checkpoint, lora):
    graph_hash = sha256(workflow)
    record_path = cfg.verification_dir / f"{manifest.stem}.{graph_hash}.json"
    if not record_path.is_file():
        raise InferenceError("UNVERIFIED · 此工作流缺少本机 GPU 验证记录")
    try:
        contents = record_path.read_bytes()
        record = SmokeRecord.model_validate(json.loads(contents))
        revoked = record_path.with_suffix(
            "." + hashlib.sha256(contents).hexdigest() + ".incompatible"
        )
        identity = runtime_identity(cfg)
        if (
            record.bindings_sha256 != sha256(manifest)
            or record.workflow_sha256 != graph_hash
            or record.checkpoint_sha256 != sha256(checkpoint)
            or record.lora_sha256 != (sha256(lora) if lora else None)
            or (record.comfy_commit, record.gpu, record.driver) != identity
        ):
            raise InferenceError("UNVERIFIED · 工作流、权重或运行环境与验证记录不一致")
        if revoked.exists() or record.result == "incompatible" or record.unmatched_keys:
            raise InferenceError("LORA_KEYS_INCOMPATIBLE · 本机验证记录报告不匹配")
        if not (
            record.loaded
            and record.generated
            and (
                not lora
                or (
                    record.effect_observed
                    and record.baseline_prompt_id
                    and record.baseline_prompt_id != record.prompt_id
                )
            )
        ):
            raise InferenceError("UNVERIFIED · 缺少加载、生成或 LoRA 对照记录")
        return revoked
    except (ValidationError, ValueError, OSError, subprocess.SubprocessError) as err:
        raise InferenceError("UNVERIFIED · 无法核对本机验证记录和环境") from err
