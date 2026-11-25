"""Summarize a pair.sh profile directory. Times are seconds and sizes are MiB."""
import argparse
import collections
import json
import os
from pathlib import Path
import statistics

MIB = 1024**2


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def summarize(run):
    state = json.loads((run/"state.json").read_text())
    start, end = state["submitted"], state["ended"]
    trace = rows(run.parent / (run.name.rsplit("-",1)[0] + ".jsonl"))
    stages = [x for x in trace if x.get("prompt") == state["prompt_id"]]
    by_stage = collections.defaultdict(float)
    for x in stages:
        label = x["stage"]
        if label in {"loader","decode","placement"}:
            label += "/" + str(x["node"])
        by_stage[label] += x["end"] - x["start"]
    samples = rows(run/"samples.jsonl")
    active = [x for x in samples if start <= x["t"] <= end]
    idle = [x for x in samples if x["t"] >= end+10]
    events = rows(run/"events.jsonl")
    inventories = [x for x in trace if x["stage"]=="inventory" and end+10 <= x["t"] <= samples[-1]["t"]]
    after = inventories[-1]["models"] if inventories else None
    checkpoints = {x["stage"]+"/"+str(x["node"]):x["models"] for x in stages if x["stage"] in {"diffusion","decode"}}
    cached = [x.get("nodes",[]) for x in events if x["kind"]=="execution_cached"]
    # NVML samples cover recent measurement windows, so sampled peaks do not
    # represent exact cumulative traffic or transfers attributable to one weight.
    return dict(seconds=round(end-start,3), stages={k:round(v,3) for k,v in by_stage.items()},
                gpu_peak_mib=max(x["gpu_bytes"] for x in active)/MIB,
                gpu_mean_pct=round(statistics.mean(x["gpu_pct"] for x in active),1),
                gpu_peak_pct=max(x["gpu_pct"] for x in active), ram_peak_mib=max(x["ram"] for x in active)/MIB,
                rss_peak_mib=max(x["rss_pages"] for x in active if x["rss_pages"] is not None)*os.sysconf("SC_PAGE_SIZE")/MIB,
                pcie_sample_peaks_mib_s={key:max((x[key] for x in active if key in x), default=0)/1024
                                        for key in ("tx_kib_s","rx_kib_s")},
                events_delta={k:samples[-1]["events"][k]-samples[0]["events"][k] for k in samples[0]["events"]},
                read_bytes_delta=active[-1]["read_bytes"]-active[0]["read_bytes"],
                idle_gpu_mib=round(statistics.median(x["gpu_bytes"] for x in idle)/MIB,2) if idle else None,
                idle_models=after, checkpoints=checkpoints, cached=cached,
                progress=[x["value"] for x in events if x["kind"]=="progress" and x.get("node")=="140:125"],
                weight_file_open_calls=sum(x["stage"]=="weight_open" for x in stages),
                validated=state["inference_ready"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs", nargs="+", type=Path)
    args=p.parse_args()
    print(json.dumps({r.name:summarize(r) for r in args.runs},indent=2))
