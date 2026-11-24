import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError

MEDIA = {
    "image": {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    },
    "video": {".mp4": "video/mp4", ".webm": "video/webm"},
    "audio": {".wav": "audio/wav", ".mp3": "audio/mpeg"},
}
MIME_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
}


def inside(root: Path, *parts) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve()
    if resolved != candidate or not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("文件路径超出工作目录")
    return candidate


def job_dir(root, job_id):
    if str(UUID(job_id)) != job_id:
        raise ValueError("任务编号无效")
    return inside(root, job_id)


def clear_job(root, job_id):
    path = job_dir(root, job_id)
    if path.exists():
        shutil.rmtree(path)


def publish_result(source: Path, root: Path, job_id: str):
    """Write the final object once. SQLite publishes its name after read-back."""
    directory = job_dir(root, job_id)
    directory.mkdir(exist_ok=True)
    target = inside(directory, source.name)
    if target.exists():
        raise FileExistsError("持久卷成片已存在")
    digest = hashlib.sha256()
    with source.open("rb") as src:
        # Job and output UUIDs are allocated by the single writer on this volume.
        out = target.open("wb")
        try:
            with out:
                while block := src.read(1024 * 1024):
                    digest.update(block)
                    out.write(block)
            with target.open("rb") as saved:
                if hashlib.file_digest(saved, "sha256").digest() != digest.digest():
                    raise OSError("持久卷成片校验失败")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return target


def validate_media(path, kind):
    ext = path.suffix.lower()
    if kind == "image":
        try:
            with Image.open(path) as im:
                if (
                    im.format
                    != {
                        ".png": "PNG",
                        ".webp": "WEBP",
                        ".jpg": "JPEG",
                        ".jpeg": "JPEG",
                    }[ext]
                    or im.width * im.height > 32_000_000
                ):
                    raise ValueError("图片格式或尺寸无效")
                im.verify()
            with Image.open(path) as im:
                im.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("图片无法解码") from exc
        return
    if not shutil.which("ffprobe"):
        raise RuntimeError("缺少 ffprobe，无法校验音视频")
    formats = {".mp4": "mov", ".webm": "matroska", ".wav": "wav", ".mp3": "mp3"}
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                formats[ext],
                "-show_entries",
                "format=format_name:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=15,
        )
        data = json.loads(probe.stdout) if not probe.returncode else {}
        if not any(s["codec_type"] == kind for s in data.get("streams", [])):
            raise ValueError("文件内容与声明的媒体类型不符")
    except (subprocess.TimeoutExpired, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("音视频无法解析") from exc


def save_upload(upload, root, kind, role, limit):
    name = upload.filename or ""
    if any(c in name for c in ("/", "\\", ":", "\x00")) or name in {".", ".."}:
        raise ValueError("文件名包含非法路径")
    ext = Path(name).suffix.lower()
    mime = MIME_ALIASES.get(upload.content_type, upload.content_type)
    if ext not in MEDIA[kind] or mime != MEDIA[kind][ext]:
        raise ValueError("素材扩展名或 MIME 类型不受支持")
    filename = f"{uuid4()}{ext}"
    path = inside(root, filename)
    size = 0
    with path.open("xb") as out:
        while block := upload.file.read(1024 * 1024):
            size += len(block)
            if size > limit:
                raise ValueError("单个素材超过上传上限")
            out.write(block)
    validate_media(path, kind)
    return {"role": role, "name": filename, "size": size, "mime": mime}
