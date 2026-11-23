# H3 + LoRA 接入与验证

当前交付支持 Windows Mock 的参数选择、排队、视频预览及历史回显。non-pruned FL2VA 与 HMNSFW Extreme Realism 的真实生成和 LoRA 效果待 L40S 实测。原始需求保存在[项目根目录的集成说明](../README_H3_LORA_INTEGRATION.md)。

## Windows 验收

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev_windows.ps1
Invoke-RestMethod http://127.0.0.1:7860/api/models
Invoke-RestMethod http://127.0.0.1:7860/api/loras
```

在文生或图生模式选择 non-pruned checkpoint profile 和 HMNSFW，初始强度为 0.8。点击「应用推荐参数」后核对 Euler、Beta、18 步、Shift 12、Turbo 关闭。使用「固定机位拍摄窗边的绿植」提交，刷新后在任务详情查看快照并播放校准片。将强度调到 0 会保留所选 ID 与 0 强度的实验记录，执行时使用独立基座图。原有工作流与参考模式维持原有能力。

作者 README 提供采样器、调度器、步数、Shift 与 Turbo 建议。0.6–1.0 的强度范围和 non-pruned 优先级来自需求文档引用的第三方转录。592 MB 为用户测量的近似大小。标题 V3 与正文 V2.5 不能用来推定 loader 版本。平台没有预设已匹配的 key 数或已验证的文件哈希。[作者 README](https://huggingface.co/Sentinel7/h3/blob/main/2834417/3446189/README.md) · [模型文件目录](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/diffusion_models)

## 任务 API

`GET /api/models` 与 `GET /api/loras` 返回注册 ID、文件名、存在性及按模式的核对状态。LoRA 条目附带参数建议和来源。`GET /api/capabilities` 保留原有 modes，并在 catalog.routes 提供每个模式、profile 与有效 LoRA 组合的控件选项。Mock 的 capability.ready 表示测试后端可以编码视频；同一条目中的 status 描述真实推理状态。公开接口不提供权重的绝对路径。

从 Bash 可向 Mock 提交以下任务：

```bash
curl -fsS -F 'payload={"mode":"text","prompt":"固定机位拍摄窗边的绿植","parameters":{"preset":"Mock Standard","duration":3,"resolution":"640x360","seed":42,"steps":18,"model_profile_id":"h3_fl2va_int8_convrot","lora_id":"hmnsfw_extreme_realism","lora_strength":0.8,"sampler":"euler","scheduler":"beta","shift":12,"turbo_enabled":false},"roles":[]}' http://127.0.0.1:7860/api/jobs
```

旧客户端可继续提交原五项参数。旧记录按原始 JSON 保留，新任务使用同一列存储完整快照，无 SQL 迁移。旧图中的高级参数显示「随工作流」。重新生成会复制参数并按当前可用性重新校验。

## AWS 准备

1. 确认模型、ComfyUI input/output 及 DATA_DIR 在加密 EBS 上。执行下列命令并记录版本、磁盘、RAM 与 GPU 空闲显存。
2. 将 non-pruned checkpoint 放到 diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors，实际导出的官方模板同时需要对应的 Qwen3-VL 编码器以及音、视频两个 VAE。pruned 权重保留作独立实验，注册的 profile 固定使用 non-pruned。[官方 H3 模板](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native)
3. 管理员将已有 HMNSFW-Extreme-Realism.safetensors 放入 ComfyUI/models/loras。用只读工具查看 metadata、键特征和 shape，可选择流式计算 SHA-256。工具只调用 safe_open 和 get_slice().get_shape()。仓库没有下载或替换权重的步骤。

```bash
bash scripts/check_environment.sh --host-only
free -h
nvidia-smi --query-gpu=name,driver_version,memory.free,memory.total --format=csv
git -C /srv/h3studio/ComfyUI rev-parse HEAD
.venv/bin/python scripts/inspect_lora.py /srv/h3studio/ComfyUI/models/loras/HMNSFW-Extreme-Realism.safetensors --sha256
```

## 实际工作流绑定

通过 SSH 隧道在本机 ComfyUI 中先跑通不含任何 LoRA 的固定种子短片，再在同一非剪枝权重、素材和采样参数下进行 LoRA 对照。将两个**实际运行的**图分别导出为 API format，保留两次不同的 prompt_id。检查 ComfyUI 日志中的 loader 使用情况与未匹配 key，并比较两份视频。为旧图填 confirmed_loras 不能启用注册的 HMNSFW。

沿用 [工作流导出格式](WORKFLOW_EXPORT.md)。每种输入数量变体必须使用与运行记录一致的 API JSON。文件名约定如下：

| 用途 | 文生模式的绑定文件 |
|---|---|
| 原有工作流 | workflows/bindings/text.json |
| non-pruned 基座，None 或强度 0 | workflows/bindings/h3_fl2va_int8_convrot.text.base.json |
| 同一基座上的 HMNSFW | workflows/bindings/h3_fl2va_int8_convrot.text.hmnsfw_extreme_realism.json |

图生将文件名中的 text 改为 image。真实 graph 和 bindings 均为私有文件。原 manifest 的 family、preset、durations、resolutions、steps、variants 继续适用。命名 profile 额外需要：

- manifest 根部填写 samplers、schedulers、shifts 允许值数组。每个 variant 的 fields 增加 sampler、scheduler、shift、turbo_enabled。采样枚举通过显式 values 将展示值映射到 /object_info 的实际枚举。Turbo 绑定布尔输入，当前命名 profile 仅开放 false。
- models 绑定实际加载器中的基座、编码器和两份 VAE。部署的基座文件名须与注册项完全一致。基座图中没有 LoRA 加载分支。
- LoRA 图的 models 额外声明一个 folder=loras 的选择器，confirmed_loras 包含该注册文件名。fields.lora_strength 绑定实际 loader 的浮点强度字段。variant.loader 声明 model_input（实际连线的输入名）、model_output（连接下游的输出序号）、model_type（从 /object_info 核对的类型）。适配器核对基座到 loader、loader 到保存路径的连接。节点 ID、class_type 和字段名全部来自本机导出。
- 两次验收固定 seed、Turbo off、相同的素材和采样值。第一次 LoRA 对照建议强度 0.8、Euler、Beta、18 步、Shift 12。OOM 时记录显存、RAM、节点与 offload 设置，再决定下一次实验。

## 生成本地验收记录

以下命令**读取已有 history 和本地输出**。先填入实际的基线与 LoRA 运行 UUID。首次计算大权重哈希可能耗时；运行中的 Studio 会按文件状态缓存哈希。服务账号须能运行 git 与 nvidia-smi。

```bash
read -rp '基座 prompt UUID: ' base_id
read -rp 'LoRA prompt UUID: ' lora_id
.venv/bin/python -m backend.record_smoke --mode text --prompt-id "$base_id" --unmatched-keys 0
.venv/bin/python -m backend.record_smoke --mode text --lora-id hmnsfw_extreme_realism --prompt-id "$lora_id" --baseline-prompt-id "$base_id" --unmatched-keys 0 --confirm-effect
.venv/bin/python -m backend.preflight
```

未匹配数量来自管理员实际审阅的 loader 报告。仅在已确认 A/B 效果时使用 --confirm-effect。若 loader 报告未匹配键，可用相同命令提供实际正数并省略 --confirm-effect，记录 incompatible。每个 variant 单独验收。记录工具核对实际执行图、成功 history、可解析视频和同参基线，原子写入 DATA_DIR/compatibility。H3_VERIFICATION_DIR 可指向其他私有目录。记录含绑定、工作流、基座和 LoRA 的 SHA-256、ComfyUI commit、GPU、驱动、运行 ID 和结论。部署时将 INFERENCE_BACKEND 设为 comfyui 并重启 API、Worker。只有当前图、节点、权重、机器信息和本机验收记录均通过检查时才显示 ready。升级自定义节点后须重新审阅其实现并重新验收。

## 回滚与复验

仅需停用实验时，在表单切回原有工作流，LoRA 选择 None。撤销 GPU 放行可将对应验收记录移到私有备份目录。回退整套代码前先排空队列并按安全手册备份 DATA_DIR，在备份副本上使用与旧代码配套的数据库版本；旧代码的严格 schema 不支持解析本版新增的参数字段。原任务及素材无需迁移或删除。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run test:e2e
```
