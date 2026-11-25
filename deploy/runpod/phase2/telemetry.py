"""Sample GPU0 and the current cgroup. NVML PCIe samples are device-wide KiB/s."""
import ctypes as c
import json
from pathlib import Path
import time


class Memory(c.Structure):
    _fields_ = [(k, c.c_ulonglong) for k in ("total", "free", "used")]


class Util(c.Structure):
    _fields_ = [(k, c.c_uint) for k in ("gpu", "memory")]


def counters(path):
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


def collect(directory, pid, done):
    lib = c.CDLL("libnvidia-ml.so.1")
    if lib.nvmlInit_v2():
        raise RuntimeError("nvmlInit")
    device = c.c_void_p()
    if lib.nvmlDeviceGetHandleByIndex_v2(0, c.byref(device)):
        raise RuntimeError("GPU 0")
    group = Path("/sys/fs/cgroup")
    proc = Path("/proc") / str(pid)
    with (directory / "samples.jsonl").open("x", buffering=1) as out:
        while not done.is_set():
            mem, util = Memory(), Util()
            if lib.nvmlDeviceGetMemoryInfo(device, c.byref(mem)) or lib.nvmlDeviceGetUtilizationRates(device, c.byref(util)):
                raise RuntimeError("NVML sample")
            pcie = {}
            for direction, name in enumerate(("tx_kib_s","rx_kib_s")):
                val = c.c_uint()
                if not lib.nvmlDeviceGetPcieThroughput(device, direction, c.byref(val)):
                    pcie[name] = val.value
            io = counters(proc / "io") if proc.exists() else {}
            row = dict(t=time.time(), gpu_bytes=mem.used, gpu_pct=util.gpu,
                       ram=int((group / "memory.current").read_text()), events=counters(group / "memory.events"),
                       rss_pages=int((proc / "statm").read_text().split()[1]) if proc.exists() else None,
                       read_bytes=io.get("read_bytes:"), **pcie)
            print(json.dumps(row), file=out)
            done.wait(1)
    lib.nvmlShutdown()
