"""Use local captions and selectively retire native classic execution caches."""
import asyncio
import subprocess
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import httpx
from PIL import Image, ImageOps

from backend.inference.base import InferenceError
from backend.services import privacy
from backend.services.files import inside

MODEL_NODES = {"UNETLoader", "FoxMotionUNETLoader", "CLIPLoader", "VAELoader",
               "LoraLoaderModelOnly", "MiniMaxH3SigmaShift"}
ALLOWED = MODEL_NODES | {
    "BasicGuider", "BasicScheduler", "ComfyMathExpression", "CreateVideo", "GetImageSize",
    "KSamplerSelect", "LoadImage", "LoadAudio", "LoadVideo", "GetVideoComponents",
    "ManualSigmas", "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo", "PrimitiveFloat",
    "PrimitiveInt", "RandomNoise", "SamplerCustomAdvanced", "SaveVideo", "VAEDecode", "VAEDecodeAudio",
}


def clean_image(src, dst):
    with Image.open(src) as original:
        pixels = ImageOps.exif_transpose(original).convert("RGBA")
        pixels.info.clear()
        pixels.save(dst, format="PNG")


def copy_video(src, dst):
    # Copy the encoded streams only, dropping graph metadata and chapters.
    subprocess.run(["ffmpeg","-nostdin","-v","error","-i",str(src),"-map","0:v:0","-map","0:a:0?",
                    "-c","copy","-map_metadata","-1","-map_chapters","-1",str(dst)],
                   capture_output=True, check=True, timeout=180)


def protect_graph(graph, variant, folder, caption):
    if any(node["class_type"] not in ALLOWED for node in graph.values()):
        raise InferenceError("保密模式仅支持已审核的本机 H3 工作流")
    namespace = PurePosixPath("foxmotion-private", folder.name)
    outputs = [n for n in graph.values() if n["class_type"] == "SaveVideo"]
    if len(outputs) != 1:
        raise InferenceError("保密工作流需要唯一的成片输出")
    prefix = PurePosixPath(outputs[0]["inputs"].get("filename_prefix", ""))
    if prefix.parent != namespace or str(UUID(prefix.name)) != prefix.name:
        raise InferenceError("保密成片输出越过了任务目录")
    for node in graph.values():
        media_field = {"LoadImage":"image", "LoadAudio":"audio", "LoadVideo":"file"}.get(node["class_type"])
        if media_field:
            source = node["inputs"].get(media_field)
            if not isinstance(source, str):
                raise InferenceError("保密素材需要已验证的本地文件")
            name = PurePosixPath(source).name
            if (source != str(namespace / name) or name in {"", ".", ".."} or "\\" in name
                    or not inside(folder, name).is_file()):
                raise InferenceError("保密素材输入越过了任务目录")
    targets = variant.fields.get("prompt", [])
    if not targets or any(graph[t.node]["class_type"] not in
                          {"MiniMaxH3ImageToVideo","MiniMaxH3ReferenceToVideo"} or t.input != "prompt" for t in targets):
        raise InferenceError("此工作流的保密提示词绑定未经验证")
    for t in targets:
        graph[t.node]["inputs"][t.input] = ["private_caption", 1]
    caption_dir = privacy.local_dir(folder/"caption")
    Image.new("RGB",(1,1)).save(caption_dir/"caption.png")
    (caption_dir/"caption.txt").write_text(caption, encoding="utf-8")
    graph["private_caption"] = {"class_type":"LoadImageTextDataSetFromFolder",
                                "inputs":{"folder":f"foxmotion-private/{folder.name}/caption"}}


def maintenance_graph(graph):
    keep = {k:v for k,v in graph.items() if v["class_type"] in MODEL_NODES}
    if any(isinstance(val,list) and val[0] not in keep for n in keep.values() for val in n["inputs"].values()):
        raise InferenceError("模型缓存依赖包含任务数据，无法保密清理")
    # These model-only nodes are disconnected from the sole output. Cached models keep
    # their original IDs/signatures; uncached loaders are never executed.
    return {**keep, "private_zero":{"class_type":"PrimitiveInt","inputs":{"value":0}},
            "private_done":{"class_type":"PreviewAny","inputs":{"source":["private_zero",0]}}}


async def clean(client, journal):
    """Resume after lost responses or a Worker crash. Never resubmit the generation."""
    state = privacy.read_json(journal)
    generation, scrub = state["generation"], state["scrub"]
    async def get(url):
        return (await client.get(url)).raise_for_status().json()
    async def delete_history(pid):
        (await client.post("/history",json={"delete":[pid]})).raise_for_status()
        return not await get("/history/"+pid)

    try:
        if any((await get("/queue")).values()):
            return False
        if not state.get("scrubbed"):
            history = (await get("/history/"+scrub)).get(scrub)
            if not history:
                # Submit only a constant output; this never samples or decodes media.
                res = (await client.post("/prompt",json={"prompt_id":scrub,"client_id":scrub,
                         "prompt":state["graph"],"extra_data":{"preview_method":"none"}})).raise_for_status()
                if res.json().get("prompt_id") != scrub:
                    return False
                for _ in range(30):
                    await asyncio.sleep(.2)
                    history = (await get("/history/"+scrub)).get(scrub)
                    if history:
                        break
            if not history:
                return False
            if not history.get("status",{}).get("completed"):
                if await delete_history(scrub):
                    state["scrub"] = str(uuid4())
                    privacy.write_json(journal, state)
                return False
            state["scrubbed"] = True
            privacy.write_json(journal, state)
        if not await delete_history(generation) or not await delete_history(scrub):
            return False
        journal.unlink()
        return True
    except (httpx.HTTPError, ValueError, KeyError, OSError):
        return False
