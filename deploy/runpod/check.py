"""Read-only Pod checks. Imports torch only for the explicit GPU probe."""

import itertools
import json
import mmap
import os
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit

GIB = 1024**3


def disjoint(paths):
    for a, b in itertools.combinations(paths, 2):
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError(f"Overlapping deployment paths: {a}, {b}")


def paths(volume, runtime, project, comfy, data, *persistent):
    disjoint([volume, runtime, project, comfy])
    disjoint([data, volume, project, comfy])
    if runtime.is_relative_to(data):
        raise ValueError("DATA_DIR must not contain runtime")
    disjoint(
        [
            data,
            *(
                runtime / p
                for p in (
                    "comfy",
                    "comfy-venv",
                    "studio",
                    "node",
                    "cache",
                    "logs",
                    "tmp",
                    "supervisord.conf",
                    "operation.lock",
                    "supervisor.sock",
                    "supervisord.pid",
                    "extra-models.yaml",
                )
            ),
        ]
    )
    disjoint(persistent)
    if not all(volume in p.parents for p in persistent):
        raise ValueError("Persistent directories must be below the Global Volume")


def space(minimum, directories):
    for directory in directories:
        probe = next(p for p in (directory, *directory.parents) if p.exists())
        usage = shutil.disk_usage(probe)
        print(f"{directory}: free {usage.free / GIB:.1f} / {usage.total / GIB:.1f} GiB")
        if usage.free < minimum * GIB:
            raise ValueError(f"{directory}: need {minimum} GiB free headroom")


def resources(proc=Path("/proc/self")):
    # Map membership through mountinfo, including cgroup namespaces and v1 controllers.
    groups = [s.split(":", 2) for s in (proc / "cgroup").read_text().splitlines()]
    ceilings, quotas = [], []
    for line in (proc / "mountinfo").read_text().splitlines():
        mount, info = line.split(" - ")
        fs, _, options = info.split()[:3]
        if fs not in {"cgroup", "cgroup2"}:
            continue
        root, target = map(Path, mount.split()[3:5])
        for _, controllers, group in groups:
            if fs == "cgroup2" and controllers:
                continue
            if fs == "cgroup" and not set(controllers.split(",")).intersection(
                options.split(",")
            ):
                continue
            relative = (
                Path(group).relative_to(root)
                if Path(group).is_relative_to(root)
                else Path(group.lstrip("/"))
            )
            leaf = target / relative
            if ".." in relative.parts or not leaf.is_dir():
                leaf = target
            for directory in (leaf, *leaf.parents):
                if not directory.is_relative_to(target):
                    break

                def read(name, base=directory):
                    file = base / name
                    return file.read_text().strip() if file.is_file() else ""

                limit = read("memory.max") or read("memory.limit_in_bytes")
                if limit.isdigit() and 0 < int(limit) < 2**60:
                    ceilings.append(int(limit))
                cpu = read("cpu.max").split() or [
                    read("cpu.cfs_quota_us"),
                    read("cpu.cfs_period_us"),
                ]
                if cpu[0].isdigit() and int(cpu[0]) > 0 and cpu[1].isdigit():
                    quotas.append(int(cpu[0]) / int(cpu[1]))
                if directory == leaf:
                    for name in (
                        "memory.current",
                        "memory.usage_in_bytes",
                        "memory.events",
                        "cpuset.cpus.effective",
                    ):
                        if value := read(name):
                            print(f"{name}: {value}")
    affinity = len(os.sched_getaffinity(0))
    print(
        "CPU affinity:",
        affinity,
        "cgroup quota vCPU:",
        min(quotas) if quotas else "unlimited/unknown",
    )
    print(
        "cgroup RAM ceiling GiB:",
        round(min(ceilings) / GIB, 2) if ceilings else "unlimited/unknown",
    )


def gpu():
    import torch

    print(
        "torch",
        torch.__version__,
        "CUDA runtime",
        torch.version.cuda,
        "from",
        torch.__file__,
    )
    if (torch.__version__, torch.version.cuda) != ("2.8.0+cu129", "12.9"):
        raise ValueError("Select the confirmed Pod torch interpreter (BASE_PYTHON)")
    if not torch.cuda.is_available() or torch.cuda.get_device_capability() != (8, 6):
        raise ValueError("Expected the confirmed Ampere SM 8.6 CUDA device")
    a = torch.ones((32, 32), device="cuda", dtype=torch.float16)
    if (a @ a)[0, 0].item() != 32:
        raise ValueError("FP16 CUDA smoke failed")
    print(torch.cuda.get_device_name(), "CUDA FP16 smoke passed")


FOLDERS = ("diffusion_models", "text_encoders", "vae", "loras", "embeddings")


def inventory(models, workflows):
    for kind in FOLDERS:
        weights = sorted((models / kind).rglob("*.safetensors"))
        print(kind, len(weights), "files")
        for weight in weights:
            with (
                weight.open("rb") as stream,
                mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as view,
            ):
                header = int.from_bytes(view[:8], "little")
                if (
                    not 2 <= header <= min(len(view) - 8, 100_000_000)
                    or view[8:9] != b"{"
                ):
                    raise ValueError(f"Invalid safetensors header: {weight.name}")
                _ = view[
                    -1
                ]  # exercise a mapped page at EOF without materializing the model
            print(
                f"  {weight.relative_to(models)} {weight.stat().st_size / GIB:.2f} GiB"
            )
    manifests = list((workflows / "bindings").glob("*.json"))
    print(
        "Bindings:",
        ", ".join(p.name for p in manifests if not p.name.endswith(".example.json"))
        or "NOT_CONFIGURED",
    )


def gpu_package(name):
    name = name.lower().replace("_", "-")
    return name in {
        "torch",
        "torchvision",
        "torchaudio",
        "triton",
        "pytorch-triton",
    } or name.startswith(("nvidia-", "cuda-"))


def protect():
    installed = {
        dist.metadata["Name"]: dist.version
        for dist in metadata.distributions()
        if dist.metadata["Name"]
    }
    for name, version in sorted(installed.items()):
        if gpu_package(name):
            print(f"{name}=={version}")


def check_plan(report):
    changes = [item["metadata"]["name"] for item in report["install"]]
    blocked = [name for name in changes if gpu_package(name)]
    if blocked:
        raise ValueError(
            "Resolver would install/replace GPU stack: " + ", ".join(blocked)
        )
    print("ComfyUI wheel plan:", ", ".join(changes) or "already installed")
    lines = []
    for item in report["install"]:
        source = item["download_info"]
        url = source["url"]
        parsed = urlsplit(url)
        digest = source.get("archive_info", {}).get("hashes", {}).get("sha256", "")
        if (
            parsed.scheme != "https"
            or not parsed.path.endswith(".whl")
            or any(c.isspace() for c in url)
            or not re.fullmatch("[a-fA-F0-9]{64}", digest)
        ):
            raise ValueError("Expected an HTTPS wheel with a SHA-256 digest")
        lines.append(f"{url} --hash=sha256:{digest}\n")
    return "".join(lines)


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    if action == "paths":
        paths(*map(Path, args))
    elif action == "space":
        space(int(args[0]), map(Path, args[1:]))
    elif action == "resources":
        resources()
    elif action == "gpu":
        gpu()
    elif action == "inventory":
        inventory(*map(Path, args))
    elif action == "constraints":
        protect()
    elif action == "installed":
        for line in Path(args[0]).read_text().splitlines():
            if line and not line.startswith("#"):
                name, url = line.split(" @ ")
                version = Path(urlsplit(url).path).name.split("-")[1]
                try:
                    if metadata.version(name) == version:
                        continue
                except metadata.PackageNotFoundError:
                    pass
                raise SystemExit(1)
    elif action == "plan":
        Path(args[1]).write_text(check_plan(json.loads(Path(args[0]).read_text())))
    else:
        raise SystemExit("Unknown check")
