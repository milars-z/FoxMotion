from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4
from backend.services import privacy

from backend.models.jobs import Capability
from backend.models.registry import DEFAULT_PROFILE, check_ids


class InferenceError(Exception):
    """A privacy-safe message suitable for display."""


class Canceled(Exception):
    pass


class LostExecution(InferenceError):
    """Remote execution state is uncertain. Do not resubmit automatically."""


class Run:
    def __init__(self, store, job, private_path=None):
        self.store, self.job = store, job
        self.id = job["job_id"]
        self.private_path = private_path
        self.staging_key = str(uuid4()) if private_path else self.id
        self.comfy_subfolder = ("foxmotion-private/" if private_path else "h3studio/") + self.staging_key
        if private_path:
            privacy.write_json(private_path / "stage.json", self.staging_key)
        self.inputs = private_path / "inputs" if private_path else store.root / "inputs" / self.id
        self.outputs = private_path / "result" if private_path else store.root / "outputs" / self.id
        self.outputs.mkdir(exist_ok=True)

    def stage(self, text):
        self.store.field(self.id, "stage", text)

    def canceled(self):
        return bool(self.store.get(self.id)["cancel_requested"])

    def check(self):
        if self.canceled():
            raise Canceled()

    def remote(self, prompt_id):
        if self.private_path:
            return
        self.store.field(self.id, "comfy_prompt_id", prompt_id)


class InferenceBackend(ABC):
    @abstractmethod
    async def capabilities(self) -> list[Capability]: ...

    async def selection_cap(self, mode, profile_id=DEFAULT_PROFILE, lora_id=None):
        check_ids(profile_id, lora_id, mode)
        return next(cap for cap in await self.capabilities() if cap.mode == mode)

    async def validate(self, req):
        from backend.models.jobs import validate_request

        p = req.parameters
        cap = await self.selection_cap(req.mode, p.model_profile_id, p.active_lora)
        validate_request(req, cap)

    @abstractmethod
    async def generate(self, run: Run) -> Path: ...


def build_backend(settings):
    if settings.inference_backend == "mock":
        from .mock import MockBackend

        return MockBackend(settings)
    from .comfyui import ComfyUIBackend

    return ComfyUIBackend(settings)
