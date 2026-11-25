from backend.models.registry import DEFAULT_PROFILE, LORAS, PROFILES, manifest_path


def real_status(cfg, profile, lora, mode, cap):
    present = (
        cfg.comfy_dir("model") / "diffusion_models" / profile.checkpoint
    ).is_file()
    if not present or (
        lora and not (cfg.comfy_dir("model") / "loras" / lora.filename).is_file()
    ):
        return "missing_file", "MODEL_NOT_FOUND · 缺少本机注册权重"
    if not manifest_path(cfg, mode, profile.id, lora.id if lora else None).is_file():
        return "missing_workflow", "NOT_CONFIGURED · 缺少此组合的 API 工作流"
    if cfg.inference_backend == "mock":
        return "unverified", "Mock 只验证参数与队列，真实推理待验证"
    if cap.ready and profile.bundled_lora:
        return "configured", "官方 Turbo 工作流及本机权重已配置"
    if cap.ready:
        return "ready", "本机验证记录与实际权重、工作流和运行环境一致"
    if any(
        code in cap.reason
        for code in (
            "LORA_KEYS_INCOMPATIBLE",
            "LORA_NODE_MISSING",
            "CHECKPOINT_MISMATCH",
        )
    ):
        return "incompatible", cap.reason
    if "MODEL_NOT_FOUND" in cap.reason:
        return "missing_file", cap.reason
    return "unverified", cap.reason


async def catalog(cfg, engine):
    profiles = []
    loras = []
    routes = []
    for profile in PROFILES.values():
        entry = profile.model_dump()
        entry["file_present"] = (
            (cfg.comfy_dir("model") / "diffusion_models" / profile.checkpoint).is_file()
            if profile.checkpoint
            else None
        )
        entry["checks"] = []
        profiles.append(entry)
        if profile.id == DEFAULT_PROFILE:
            continue
        for lora in [
            None,
            *(
                item
                for item in LORAS.values()
                if item.preferred_model_profile == profile.id
            ),
        ]:
            checks = []
            for mode in profile.modes:
                lid = lora.id if lora else None
                cap = await engine.selection_cap(mode, profile.id, lid)
                status, reason = real_status(cfg, profile, lora, mode, cap)
                checks.append(
                    {
                        "mode": mode,
                        "status": status,
                        "verified": status == "ready",
                        "reason": reason,
                    }
                )
                routes.append(
                    {
                        "model_profile_id": profile.id,
                        "lora_id": lid,
                        "mode": mode,
                        "capability": cap,
                        "status": status,
                        "reason": reason,
                    }
                )
            if lora:
                loras.append(
                    {
                        **lora.model_dump(),
                        "file_present": (
                            cfg.comfy_dir("model") / "loras" / lora.filename
                        ).is_file(),
                        "checks": checks,
                    }
                )
            else:
                entry["checks"] = checks
    for item in profiles + loras:
        states = {c["status"] for c in item["checks"]}
        item["compatibility"] = (
            next(iter(states))
            if len(states) == 1
            else ("unverified" if states else "workflow_managed")
        )
    return {
        "profiles": profiles,
        "loras": loras,
        "routes": routes,
        "default_profile_id": DEFAULT_PROFILE,
    }
