import httpx
from backend.services import gpu

import json
import logging
import platform
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi import Request as HttpRequest
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.config import Settings
from backend.inference.base import build_backend
from backend.models.jobs import KINDS, TERMINAL, Job, Request
from backend.services.catalog import catalog
from backend.services.files import (
    MEDIA,
    MIME_ALIASES,
    clear_job,
    inside,
    job_dir,
    save_upload,
)
from backend.services.store import Store, now

log = logging.getLogger("h3.api")

UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["payload"],
                    "properties": {
                        "payload": {
                            "type": "string",
                            "description": "JSON: mode, prompt, parameters, roles (same order as files)",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                        },
                    },
                }
            }
        },
    }
}


def public(row):
    return {
        **{
            k: row[k]
            for k in (
                "job_id",
                "mode",
                "prompt",
                "backend",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "stage",
                "error_message",
                "comfy_prompt_id",
            )
        },
        "parameters": json.loads(row["parameters_json"]),
        "input_files": json.loads(row["input_files_json"]),
        "cancel_requested": bool(row["cancel_requested"]),
    }


def create_app(cfg: Settings):
    app = FastAPI(title="H3 Video Studio", docs_url=None, redoc_url=None)
    store, engine = Store(cfg.data_dir), build_backend(cfg)
    app.state.store = store
    app.state.engine = engine
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def private_access(request: HttpRequest, call_next):
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and (
                urlsplit(origin).netloc != request.headers.get("host")
                or urlsplit(origin).scheme != "http"
            ):
                return JSONResponse({"detail": "拒绝跨站请求"}, 403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站请求"}, 403)
            if request.method == "POST":
                if request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "请使用固定长度上传"}, 411)
                size = request.headers.get("content-length", "")
                if not size.isdecimal():
                    return JSONResponse({"detail": "请求需要 Content-Length"}, 411)
                if int(size) > cfg.max_request_mb * 1024**2:
                    return JSONResponse({"detail": "上传总量超过限制"}, 413)
        res = await call_next(request)
        res.headers["X-Content-Type-Options"] = "nosniff"
        res.headers["Referrer-Policy"] = "no-referrer"
        res.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            res.headers["Cache-Control"] = "private, no-store"
        return res

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/system")
    def system():
        return {
            "backend": cfg.inference_backend,
            "python": platform.python_version(),
            "disk_free_gib": round(shutil.disk_usage(store.root).free / 2**30, 1),
            "worker": store.worker(),
        }

    @app.get("/api/gpu")
    async def gpu_status():
        if cfg.inference_backend != "comfyui":
            raise HTTPException(404, "当前为 Mock")
        try:
            return await gpu.status(cfg, store)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Comfy GPU 状态不可用") from exc

    @app.post("/api/gpu/release")
    async def gpu_release():
        if cfg.inference_backend != "comfyui":
            raise HTTPException(409, "当前为 Mock")
        try:
            await gpu.release(cfg, store)
        except gpu.Busy as exc:
            raise HTTPException(409, str(exc)) from exc
        except (httpx.HTTPError, RuntimeError) as exc:
            raise HTTPException(503, "Comfy 未确认显存释放") from exc
        return {"released": True}

    @app.get("/api/capabilities")
    async def capabilities():
        return {
            "backend": cfg.inference_backend,
            "modes": await engine.capabilities(),
            "catalog": await catalog(cfg, engine),
            "max_file_mb": cfg.max_file_mb,
            "max_request_mb": cfg.max_request_mb,
            "media": MEDIA,
            "mime_aliases": MIME_ALIASES,
        }

    @app.get("/api/models")
    async def models():
        return (await catalog(cfg, engine))["profiles"]

    @app.get("/api/loras")
    async def loras():
        return (await catalog(cfg, engine))["loras"]

    async def check(req):
        try:
            await engine.validate(req)
        except RuntimeError as err:
            raise HTTPException(503, str(err)) from err
        except ValueError as err:
            raise HTTPException(422, str(err)) from err

    def get(job_id):
        row = store.get(str(job_id))
        if not row:
            raise HTTPException(404, "任务不存在")
        return row

    def persist(req, uploads):
        job_id = str(uuid4())
        root = job_dir(store.root / "inputs", job_id)
        root.mkdir()
        try:
            inputs = [
                save_upload(f, root, KINDS[role], role, cfg.max_file_mb * 1024**2)
                for role, f in zip(req.roles, uploads, strict=True)
            ]
            if req.parameters.seed == -1:
                req.parameters.seed = secrets.randbelow(2**31)
            with store.connection(True) as db:
                row = store.insert(db, job_id, req, inputs, cfg.inference_backend)
        except BaseException:
            clear_job(store.root / "inputs", job_id)
            raise
        log.info("job=%s event=created", job_id)
        return public(row)

    @app.post(
        "/api/jobs", status_code=201, response_model=Job, openapi_extra=UPLOAD_SCHEMA
    )
    async def create(request: HttpRequest):
        async with request.form(
            max_files=20, max_fields=1, max_part_size=64 * 1024
        ) as form:
            try:
                req = Request.model_validate_json(form.get("payload", ""))
            except ValidationError as err:
                problem = err.errors(
                    include_input=False, include_context=False, include_url=False
                )[0]
                field = ".".join(str(part) for part in problem["loc"])
                detail = (
                    problem["msg"]
                    if problem["type"] == "value_error"
                    else "字段值、类型或范围无效"
                )
                raise HTTPException(
                    422, f"任务参数无效 · {field or 'payload'} · {detail}"
                ) from err
            except TypeError:
                raise HTTPException(422, "任务 JSON 无效，请检查模式、提示词和参数")
            files = form.getlist("files")
            if (
                set(form.keys()) - {"payload", "files"}
                or len(files) != len(req.roles)
                or any(not isinstance(f, UploadFile) for f in files)
            ):
                raise HTTPException(422, "上传文件与角色列表不匹配")
            await check(req)
            try:
                return await run_in_threadpool(persist, req, files)
            except ValueError as err:
                raise HTTPException(422, str(err)) from err
            except RuntimeError as err:
                raise HTTPException(503, str(err)) from err
            except OSError as err:
                raise HTTPException(
                    507, "无法保存素材，请检查磁盘空间及目录权限"
                ) from err

    @app.get("/api/jobs", response_model=list[Job])
    def listing(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return [public(row) for row in store.list(limit, offset)]

    @app.get("/api/jobs/{job_id}", response_model=Job)
    def details(job_id: UUID):
        return public(get(job_id))

    @app.post("/api/jobs/{job_id}/cancel", response_model=Job)
    def cancel(job_id: UUID):
        key = str(job_id)
        with store.connection(True) as db:
            row = db.execute(
                "SELECT status FROM jobs WHERE job_id=?", (key,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "任务不存在")
            if row[0] == "queued":
                db.execute(
                    "UPDATE jobs SET status='canceled',stage='已取消',finished_at=? WHERE job_id=?",
                    (now(), key),
                )
            elif row[0] == "running":
                db.execute(
                    "UPDATE jobs SET cancel_requested=1,stage='正在取消' WHERE job_id=?",
                    (key,),
                )
            elif row[0] != "canceled":
                raise HTTPException(409, "任务已经结束")
        return public(get(key))

    @app.post("/api/jobs/{job_id}/regenerate", status_code=201, response_model=Job)
    async def regenerate(job_id: UUID):
        row = public(get(job_id))
        req = Request(
            mode=row["mode"],
            prompt=row["prompt"],
            parameters=row["parameters"],
            roles=[f["role"] for f in row["input_files"]],
        )
        await check(req)

        def clone():
            key = str(uuid4())
            root = job_dir(store.root / "inputs", key)
            try:
                with store.connection(True) as db:
                    if not db.execute(
                        "SELECT 1 FROM jobs WHERE job_id=?", (str(job_id),)
                    ).fetchone():
                        raise HTTPException(404, "原任务已删除")
                    root.mkdir()
                    inputs = []
                    for meta in row["input_files"]:
                        src = inside(
                            job_dir(store.root / "inputs", str(job_id)), meta["name"]
                        )
                        name = f"{uuid4()}{src.suffix}"
                        shutil.copyfile(src, root / name)
                        inputs.append({**meta, "name": name})
                    result = store.insert(db, key, req, inputs, cfg.inference_backend)
                return public(result)
            except BaseException:
                clear_job(store.root / "inputs", key)
                raise

        return await run_in_threadpool(clone)

    def sweep(key):
        for part in ("inputs", "outputs", "temp"):
            clear_job(store.root / part, key)
        if cfg.result_dir and cfg.result_dir.resolve() != store.root / "outputs":
            if not cfg.result_dir.is_dir():
                raise OSError("持久卷输出目录不可用")
            clear_job(cfg.result_dir, key)
        for kind in ("input", "output"):
            base = cfg.comfy_dir(kind) / "h3studio"
            if base.exists():
                clear_job(base, key)
        with store.connection(True) as db:
            db.execute("DELETE FROM trash WHERE job_id=?", (key,))

    with store.connection() as db:
        pending_cleanup = [row[0] for row in db.execute("SELECT job_id FROM trash")]
    for key in pending_cleanup:
        try:
            sweep(key)
        except OSError:
            log.warning("job=%s event=cleanup_pending", key)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete(job_id: UUID):
        key = str(job_id)
        row = get(key)
        if row["status"] not in TERMINAL:
            raise HTTPException(409, "请先取消正在执行或排队的任务")
        if row["status"] == "interrupted" and row["backend"] == "comfyui":
            from backend.inference.comfyui import ComfyUIBackend

            if not await ComfyUIBackend(cfg).safe_to_clean(row["comfy_prompt_id"]):
                raise HTTPException(409, "请待本机 ComfyUI 队列空闲后再删除中断任务")
        with store.connection(True) as db:
            db.execute("DELETE FROM jobs WHERE job_id=?", (key,))
            db.execute("INSERT OR IGNORE INTO trash VALUES(?)", (key,))
        try:
            await run_in_threadpool(sweep, key)
        except OSError:
            log.warning("job=%s event=cleanup_pending", key)
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/result")
    def result(job_id: UUID, download: bool = False):
        row = get(job_id)
        if row["status"] != "completed" or not row["output_path"]:
            raise HTTPException(409, "任务尚无可用结果")
        path = inside(job_dir(store.root / "outputs", str(job_id)), row["output_path"])
        if cfg.result_dir:
            published = inside(job_dir(cfg.result_dir, str(job_id)), row["output_path"])
            if published.is_file():
                path = published
        if not path.is_file():
            raise HTTPException(410, "结果文件已丢失")
        return FileResponse(
            path,
            media_type=MEDIA["video"][path.suffix.lower()],
            filename=f"h3-{job_id}{path.suffix}" if download else None,
        )

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="studio")
    return app


def from_environment():
    return create_app(Settings())
