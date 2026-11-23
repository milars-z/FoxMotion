import asyncio
import json
import shutil
import time
from collections import defaultdict
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from backend.models.jobs import (
    FAMILY,
    KINDS,
    MODES,
    Capability,
    Request,
    validate_request,
)
from backend.models.registry import DEFAULT_PROFILE, manifest_path
from backend.services.files import inside, validate_media

from .base import Canceled, InferenceBackend, InferenceError, LostExecution
from .workflow import WorkflowAdapter, demand, safe_relative


class ComfyUIBackend(InferenceBackend):
    def __init__(self, settings, transport=None, websocket=connect):
        self.cfg, self.transport, self.websocket = settings, transport, websocket
        self._objects = None
        self._expires = 0.0

    def client(self):
        return httpx.AsyncClient(
            base_url=self.cfg.comfyui_url,
            transport=self.transport,
            trust_env=False,
            timeout=10,
            follow_redirects=False,
        )

    @staticmethod
    async def get(client, path):
        result = await client.get(path)
        result.raise_for_status()
        return result.json()

    async def object_info(self):
        if self._objects is None or time.monotonic() >= self._expires:
            async with self.client() as client:
                self._objects = await self.get(client, "/object_info")
            self._expires = time.monotonic() + 2
        return self._objects

    async def capabilities(self):
        return [await self.selection_cap(mode) for mode in MODES]

    async def selection_cap(self, mode, profile_id=DEFAULT_PROFILE, lora_id=None):
        path = manifest_path(self.cfg, mode, profile_id, lora_id)
        try:
            if not path.is_file():
                raise InferenceError(
                    "NOT_CONFIGURED / WORKFLOW_NOT_CONFIGURED · 缺少此组合的 API 工作流"
                )
            if not shutil.which("ffprobe"):
                raise InferenceError("缺少 ffprobe")
            objects = await self.object_info()
            adapter = await asyncio.to_thread(
                WorkflowAdapter, self.cfg, mode, objects, profile_id, lora_id
            )
            return adapter.cap
        except (httpx.HTTPError, ValueError):
            reason = "UNVERIFIED · 无法读取本机 ComfyUI 节点清单"
        except InferenceError as err:
            reason = str(err)
        return Capability(mode=mode, family=FAMILY[mode], ready=False, reason=reason)

    @staticmethod
    def queues(state):
        return {
            name: [row[1] for row in state[name]]
            for name in ("queue_running", "queue_pending")
        }

    async def safe_to_clean(self, prompt_id):
        try:
            async with self.client() as client:
                q = self.queues(await self.get(client, "/queue"))
                return all(
                    prompt_id not in ids if prompt_id else not ids for ids in q.values()
                )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            return False

    async def inputs(self, client, run, files):
        names = defaultdict(list)
        sub = f"h3studio/{run.id}"
        root = inside(self.cfg.comfy_dir("input"), sub)
        root.mkdir(parents=True, exist_ok=True)
        for f in files:
            run.check()
            path = inside(run.inputs, f["name"])
            name = path.name
            if KINDS[f["role"]] == "image":
                with path.open("rb") as stream:
                    res = await client.post(
                        "/upload/image",
                        files={"image": (name, stream, f["mime"])},
                        data={"type": "input", "subfolder": sub, "overwrite": "false"},
                    )
                res.raise_for_status()
                info = res.json()
                demand(
                    info.get("type") == "input"
                    and info.get("subfolder", "").replace("\\", "/") == sub
                    and info.get("name") == name,
                    "ComfyUI 图片上传位置不匹配",
                )
            else:
                await asyncio.to_thread(shutil.copyfile, path, inside(root, name))
            names[f["role"]].append(sub + "/" + name)
        return names

    async def save_result(self, run, v, history):
        outputs = (
            history.get("outputs", {}).get(v.output_node, {}).get(v.output_key, [])
        )
        candidates = [
            f
            for f in outputs
            if isinstance(f, dict)
            and f.get("type") == "output"
            and Path(f.get("filename", "")).suffix.lower() in (".mp4", ".webm")
        ]
        demand(len(candidates) == 1, "工作流必须输出一个可播放的 MP4 或 WebM")
        f = candidates[0]
        sub = f["subfolder"].replace("\\", "/")
        demand(
            sub == f"h3studio/{run.id}"
            and safe_relative(f["filename"])
            and "/" not in f["filename"],
            "拒绝读取任务专属目录以外的输出",
        )
        src = inside(self.cfg.comfy_dir("output"), sub, f["filename"])
        dest = run.outputs / f"{uuid4()}{src.suffix.lower()}"
        demand(src.is_file(), "ComfyUI 结果文件不存在")
        await asyncio.to_thread(shutil.copyfile, src, dest)
        await asyncio.to_thread(validate_media, dest, "video")
        return dest

    async def monitor(self, run, client, ws, pid, v, deadline, revoked=None):
        next_queue = 0
        while time.monotonic() < deadline:
            rec = (await self.get(client, "/history/" + pid)).get(pid)
            if rec:
                status = rec.get("status", {})
                if revoked is not None and v.loader:
                    loader_nodes = {m.node for m in v.models if m.folder == "loras"}
                    diagnostics = [
                        entry[1].get("exception_message", "")
                        for entry in status.get("messages", [])
                        if isinstance(entry, list)
                        and len(entry) == 2
                        and isinstance(entry[1], dict)
                        and str(entry[1].get("node_id")) in loader_nodes
                    ]
                    if any(
                        token in str(msg).lower()
                        for msg in diagnostics
                        for token in (
                            "unmatched key",
                            "lora key not loaded",
                            "size mismatch",
                            "shape mismatch",
                        )
                    ):
                        revoked.touch()
                        run.check()
                        raise InferenceError(
                            "LORA_KEYS_INCOMPATIBLE · 本机 loader 报告键或形状不匹配"
                        )
                if status.get("status_str") == "error":
                    run.check()
                    raise InferenceError(
                        "ComfyUI 工作流执行失败，请检查本机 ComfyUI 服务日志"
                    )
                if status.get("completed"):
                    run.check()
                    run.stage("保存视频输出")
                    return await self.save_result(run, v, rec)
            if time.monotonic() >= next_queue:
                q = self.queues(await self.get(client, "/queue"))
                if run.canceled():
                    if pid in q["queue_pending"]:
                        (
                            await client.post("/queue", json={"delete": [pid]})
                        ).raise_for_status()
                    elif pid in q["queue_running"]:
                        # Newer local servers provide a targeted cancellation route.
                        # Older servers finish this prompt and we discard its result.
                        reply = await client.post(
                            "/api/jobs/" + pid + "/cancel", json={}
                        )
                        if reply.status_code not in (404, 405):
                            reply.raise_for_status()
                    if not any(pid in ids for ids in q.values()):
                        raise Canceled()
                    run.stage("正在取消，等待本机释放执行资源")
                else:
                    run.stage(
                        "本机节点执行中"
                        if pid in q["queue_running"]
                        else "等待本机输出"
                    )
                next_queue = time.monotonic() + 1.5
            if ws is None:
                await asyncio.sleep(0.6)
                continue
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.65)
                if isinstance(msg, str):
                    event = json.loads(msg)
                    if (
                        event.get("data", {}).get("prompt_id") == pid
                        and not run.canceled()
                        and event.get("type")
                        in ("executing", "progress", "execution_start")
                    ):
                        run.stage("本机节点执行中")
                # Throttle history reads even if the server streams many node events.
                await asyncio.sleep(0.2)
            except TimeoutError:
                pass
            except (WebSocketException, OSError, ValueError):
                ws = None
        raise LostExecution("等待 ComfyUI 超时，请核对本机队列后重新生成")

    async def generate(self, run):
        deadline = time.monotonic() + self.cfg.comfy_timeout_seconds
        client_id = str(uuid4())
        submitted = False
        async with self.client() as client:
            ws = None
            try:
                files = json.loads(run.job["input_files_json"])
                req = Request(
                    mode=run.job["mode"],
                    prompt=run.job["prompt"],
                    parameters=json.loads(run.job["parameters_json"]),
                    roles=[f["role"] for f in files],
                )
                p = req.parameters
                adapter = await asyncio.to_thread(
                    WorkflowAdapter,
                    self.cfg,
                    req.mode,
                    await self.get(client, "/object_info"),
                    p.model_profile_id,
                    p.active_lora,
                )
                validate_request(req, adapter.cap)
                run.stage("等待本机队列空闲")
                while any(self.queues(await self.get(client, "/queue")).values()):
                    run.check()
                    if time.monotonic() >= deadline:
                        raise InferenceError("等待本机队列超时")
                    await asyncio.sleep(1)
                names = await self.inputs(client, run, files)
                graph, v = adapter.render(req, names, f"h3studio/{run.id}/{uuid4()}")
                with suppress(OSError, WebSocketException, TimeoutError):
                    ws = await self.websocket(
                        self.cfg.comfyui_url.replace("http://", "ws://", 1)
                        + "/ws?clientId="
                        + client_id,
                        open_timeout=3,
                        close_timeout=1,
                        proxy=None,
                    )
                run.check()
                submitted = True
                res = await client.post(
                    "/prompt", json={"client_id": client_id, "prompt": graph}
                )
                if res.status_code == 400:
                    raise InferenceError("ComfyUI 拒绝工作流，请检查节点和输入绑定")
                res.raise_for_status()
                pid = res.json()["prompt_id"]
                if not isinstance(pid, str) or not safe_relative(pid) or "/" in pid:
                    raise LostExecution("ComfyUI 返回了无效任务 ID，请核对本机队列")
                run.remote(pid)
                return await self.monitor(
                    run,
                    client,
                    ws,
                    pid,
                    v,
                    deadline,
                    adapter.revocation_paths.get(v.workflow),
                )
            except (httpx.HTTPError, KeyError, ValueError, OSError, TypeError) as err:
                if submitted:
                    raise LostExecution(
                        "无法确认 ComfyUI 执行结果，请检查本机队列与输出目录"
                    ) from err
                raise InferenceError("本机 ComfyUI 请求或文件校验失败") from err
            finally:
                if ws is not None:
                    with suppress(OSError, WebSocketException):
                        await ws.close()
