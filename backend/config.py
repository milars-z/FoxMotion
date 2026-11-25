from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    inference_backend: Literal["mock", "comfyui"] = "mock"
    studio_host: Literal["127.0.0.1"] = "127.0.0.1"
    studio_port: int = Field(7860, ge=1, le=65535)
    comfyui_url: str = "http://127.0.0.1:8188"
    data_dir: Path = Path("data")
    result_dir: Path | None = None
    workflow_dir: Path = Path("workflows")
    h3_verification_dir: Path | None = None
    comfyui_home: Path = Path("/opt/ComfyUI")
    comfyui_input_dir: Path | None = None
    comfyui_output_dir: Path | None = None
    comfyui_model_dir: Path | None = None
    max_file_mb: int = Field(64, ge=1, le=1024)
    max_request_mb: int = Field(192, ge=1, le=4096)
    mock_stage_seconds: float = Field(1, ge=0, le=30)
    comfy_timeout_seconds: int = Field(3600, ge=5)
    gpu_idle_release_seconds: int = Field(0, ge=0)
    worker_poll_seconds: float = Field(0.4, ge=0.05, le=10)

    @model_validator(mode="after")
    def separate_results(self):
        if self.result_dir:
            data, results = self.data_dir.resolve(), self.result_dir.resolve()
            if results != data / "outputs" and (
                results.is_relative_to(data) or data.is_relative_to(results)
            ):
                raise ValueError("独立成片目录与 DATA_DIR 不可重叠")
        return self

    @field_validator("comfyui_url")
    @classmethod
    def local_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "::1"}
            or url.username
            or url.password
            or url.path.strip("/")
            or url.query
            or url.fragment
        ):
            raise ValueError("ComfyUI 必须使用回环地址的 HTTP 服务")
        return value.rstrip("/")

    def comfy_dir(self, kind: Literal["input", "output", "model"]) -> Path:
        path = getattr(self, f"comfyui_{kind}_dir")
        return (
            path or self.comfyui_home / ("models" if kind == "model" else kind)
        ).resolve()

    @property
    def verification_dir(self) -> Path:
        return (self.h3_verification_dir or self.data_dir / "compatibility").resolve()
