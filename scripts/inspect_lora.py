"""Read-only safetensors inspection. Never materializes a weight tensor."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from safetensors import SafetensorError, safe_open


def inspect(path, hash_file=False):
    with safe_open(str(path), framework="numpy") as weights:
        keys = list(weights.keys())
        pairs = [
            k
            for k in keys
            if re.search(r"(?:lora[._](?:up|down|[ab])|lokr|hada)", k, re.IGNORECASE)
        ]
        examples = [
            {"key": key, "shape": weights.get_slice(key).get_shape()}
            for key in (pairs or keys)[:12]
        ]
        metadata = weights.metadata() or {}
    report = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "tensor_count": len(keys),
        "typical_keys": examples,
        "metadata": metadata,
        "target_examples": list(
            dict.fromkeys(
                re.split(r"[._]lora[._]", k, flags=re.IGNORECASE)[0] for k in pairs
            )
        )[:12],
        "compatibility": "unverified",
        "notes": ["需要用实际 loader 核对目标模块和未匹配 key"]
        if pairs
        else ["未识别到常见 LoRA 键名，请人工核对文件类型与 loader"],
    }
    if hash_file:
        with path.open("rb") as stream:
            report["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("file", type=Path)
    cli.add_argument("--sha256", action="store_true")
    args = cli.parse_args()
    try:
        print(json.dumps(inspect(args.file, args.sha256), indent=2, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, SafetensorError) as exc:
        cli.exit(1, f"Header inspection failed: {type(exc).__name__}\n")
