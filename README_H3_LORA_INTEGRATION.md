# H3 Video Studio — MiniMax H3 + HMNSFW LoRA 集成变更说明

> **用途**：将本文件交给 Codex，要求它在**已有的** H3 Video Studio 项目里执行增量修改。不要重新创建项目、替换前后端架构，也不要在没有 GPU 的 Windows 环境中擅自下载大模型。
>
> **目标**：先让网页、API、任务管理和工作流适配器为指定 H3 基座 + LoRA 做好准备；等 AWS EC2 L40S 48 GB 实例可用后，验证真实推理。

## 0. Codex 工作原则（必须遵守）

1. 先读取当前仓库的 `README.md`、前后端 API、数据库 schema、`InferenceBackend`/`WorkflowAdapter`、配置系统和测试。根据**现有目录**改造，不要假设此前建议的目录已经存在。
2. 尽可能保持现有 API 和数据格式向后兼容；数据库 schema 需要迁移时提供可重复、安全的迁移脚本。
3. **区分“作者声称”“第三方收录”“本地验证通过”**。不得把仅有说明文档的兼容关系显示成 `verified`。
4. 不猜测 ComfyUI H3 节点 `class_type`、节点 ID、LoRA 注入位置或 API 输入字段。必须通过真实导出的 **API format** 工作流和 `/object_info` 校验。
5. 当前先实现 Mock 端到端；没有 AWS GPU 时，真实推理标记 `NOT_CONFIGURED` / `UNVERIFIED`，绝不可伪造成功。
6. 不自动下载巨型 checkpoint，不修改 GPU 驱动，不公开 Web UI。不要将任何模型权重、个人素材或任务数据库提交 Git。

## 1. 已知权重与证据等级

### 1.1 LoRA（用户已有本地文件）

| 字段 | 值 | 证据 |
|---|---|---|
| 显示名 | HMNSFW Extreme Realism | Sentinel7 README |
| 本地文件名 | `HMNSFW-Extreme-Realism.safetensors` | 用户提供 |
| 文件大小 | 约 **592 MB** | 用户测量；不是校验和 |
| 类型 | LoRA（不是完整 H3 模型） | Sentinel7 README 声明；待检查 safetensors 键 |
| 基础模型家族 | MiniMax H3 | Sentinel7 README |
| 版本标识 | Civitai version ID `3446189`；标题写 V3，但正文仍提 V2.5 | Sentinel7 README；暂不据此推断架构版本 |
| 触发词 | 无 | Sentinel7 README |
| 作者推荐采样器 | `Euler` | Sentinel7 README |
| 作者推荐 Scheduler | `Simple` / `Beta` / `Normal` | Sentinel7 README |
| 作者推荐 Steps | **18** | Sentinel7 README |
| 作者推荐 Shift | **12** | Sentinel7 README |
| 作者推荐 Turbo LoRA | 默认 **None**；作者称也可与 Turbo 搭配 | Sentinel7 README |
| 推荐 Strength | **0.6–1.0**，首测取 **0.8** | 第三方页面转录的作者 Civitai 说明；不在用户提供的 README 正文 |
| 作者提到的运行基座 | **FL2VA INT8 ConvRot（非剪枝）** | 第三方页面转录的作者 Civitai 说明；需实机确认 |

原始 README：https://huggingface.co/Sentinel7/h3/blob/main/2834417/3446189/README.md

作者 Civitai 页面：https://civitai.com/models/2834417?modelVersionId=3446189

注意：不能仅凭 `.safetensors`、592MB、文件名或这些文字断言与 ComfyUI 的某个版本 100% 兼容。

### 1.2 基础模型优先级

**首选：** `minimax_h3_fl2va_int8_convrot.safetensors`（**non-pruned**，文件约 34 GB，磁盘显示 GiB 数可能不同）。

**保留为兼容性实验备选：** `minimax_h3_fl2va_pruned_int8_convrot.safetensors`（约 21 GB）。这不是同一个检查点；可能存在结构差异，**不可静默替换，也不得预先声明 LoRA 兼容**。

两个文件位于：
https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/diffusion_models

基础模型文件放到：

```text
ComfyUI/models/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors
```

还需要与所选**官方 ComfyUI H3 工作流**一致的 Qwen3-VL 文本/视觉编码器及 Video/Audio VAE。不要只下载 DiT 和 LoRA 就声称模型完整。文件名应从当前官方工作流和模型仓库核实，不擅自混用不同运行时的转换权重。

官方 H3 工作流：https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native

**重要**：官方 ComfyUI 示例工作流可能默认选用 `pruned_int8_convrot` 和 Turbo LoRA。集成此 LoRA 时，应为 non-pruned 基座建立**独立配置**，检查模型加载/LoRA 节点是否支持该组合。不要只靠替换文件名硬跑。

## 2. 要达到的结果

Web UI 继续提供：文生视频、首/尾帧图生视频、参考图片/视频上传、任务管理和视频预览（后端尚未实现的模式继续禁用）。

本次**只新增/调整**：

- 模型选择：`MiniMax H3 FL2VA INT8 ConvRot (non-pruned)` 为 LoRA 实测候选；原有模型预设保留。
- LoRA 选择：`None` / `HMNSFW Extreme Realism`；未来可扩展为多款，但首版**同一任务只加载一款**。
- LoRA Strength：0.0–1.5 的可调范围；选中此 LoRA 时建议 **0.8**，说明作者参考区间 **0.6–1.0**。0 表示禁用 LoRA 效果。
- 采样参数建议预设：`Euler`、`Beta`、`18 steps`、`shift 12`、`Turbo off`。请提供“**应用推荐参数**”按钮，不要悄悄覆盖用户已经手动调整的参数。
- 兼容性提示：显示 `unverified` / `missing_file` / `missing_workflow` / `ready` / `incompatible` 等状态及原因；`ready` 仅在真实环境通过指定校验后才能出现。
- 任务详情：记录当时使用的 checkpoint profile、LoRA ID、Strength、Sampler、Scheduler、Steps、Shift、Turbo、Seed，保证实验可复现。

> 本项目不需要加入露骨内容的演示 prompt 或图库素材。功能测试用中性场景；成人向权重仅用于模型注册、参数管理和技术兼容性检查。所有生成内容须合法、涉及成年人且获得适当同意，不得用于非自愿真人性影像。

## 3. 配置应由后端提供，不要前端硬编码

优先沿用现有配置机制；如果目前没有注册表，新增结构化 JSON/YAML 或 Pydantic 配置。下面是**示意数据结构，不代表已验证可运行**：

```yaml
model_profiles:
  - id: h3_fl2va_int8_convrot
    title: MiniMax H3 FL2VA INT8 ConvRot (non-pruned)
    family: minimax_h3_fl2va
    checkpoint: minimax_h3_fl2va_int8_convrot.safetensors
    expected_subdir: diffusion_models
    verified: false

loras:
  - id: hmnsfw_extreme_realism
    title: HMNSFW Extreme Realism
    filename: HMNSFW-Extreme-Realism.safetensors
    expected_subdir: loras
    base_family: minimax_h3_fl2va
    preferred_model_profile: h3_fl2va_int8_convrot
    default_strength: 0.8
    strength_min: 0.0
    strength_max: 1.5
    author_reference_strength: [0.6, 1.0]
    recommended_sampler: euler
    recommended_scheduler: beta
    recommended_steps: 18
    recommended_shift: 12
    recommended_turbo: false
    compatibility: unverified
```

实际参数枚举取决于 ComfyUI **具体节点的合法选项**；例如 `Euler`/`Beta` 只是文档中的展示名称，提交前要映射到正确的节点值，并对不支持的组合报错。

## 4. 后端 API 与数据模型增量修改

依照现有 API 命名和结构扩展，而不是强制更名：

1. `GET /api/models`（若已有同类 capabilities 接口则扩展）：模型 profile、可用模式、对应权重存在性及验证状态。
2. `GET /api/loras`：返回注册的 LoRA ID、显示名、作者推荐参数、验证状态、缺失原因；**绝不返回服务器绝对文件路径**。
3. `POST /api/jobs` 的参数增加：
   - `model_profile_id: string`
   - `lora_id: string | null`
   - `lora_strength: number`（0–1.5）
   - `sampler`, `scheduler`, `steps`, `shift`, `turbo_enabled`, `seed`（沿用已有字段则不重复）
4. 数据库保存上述每次提交时的**不可变参数快照**，旧记录可迁移并保留。
5. 服务端必须校验：LoRA 是否在白名单、checkpoint family 是否对应、参数是否合法、模式是否支持、文件及真实工作流是否存在。未知 ID/非法路径应拒绝。
6. Mock 模式应支持保存/返回全部参数，并完整测试队列；结果明确标为 `mock`，**不能把 Mock 的成功当成 GPU 验证通过**。
7. 不得允许用户通过请求字段直接加载任意本地 `.safetensors` 路径。

## 5. ComfyUI 适配：最重要的工程部分

保留统一 `InferenceBackend` 接口，在现有 `ComfyUIBackend` / `WorkflowAdapter` 中增量实现：

- 工作流采用 ComfyUI **API format JSON**，与可视化编辑器普通保存的工作流 JSON 分开。
- 按 `model_profile_id` 选择 API 工作流及显式 bindings；将 model checkpoint、prompt、LoRA、strength、采样器、scheduler、steps、shift、Turbo、seed、输入图片映射至**实际存在**的节点字段。
- **不能假设标准 `LoraLoader` 一定支持 MiniMax H3**。先检查当前 ComfyUI 版本、工作流所用原生/自定义节点、`GET /object_info` 及 loader 输入输出类型。
- 支持绕过 LoRA（`None` 或 strength=0），并保证原始基础工作流仍能运行。
- **Turbo 默认关闭**，不能在官方示例已有 Turbo 设置的情况下意外再次叠加。
- 对 LoRA 不支持或不匹配的情况返回明确错误（如 `LORA_NODE_MISSING`、`LORA_KEYS_INCOMPATIBLE`、`MODEL_NOT_FOUND`、`WORKFLOW_NOT_CONFIGURED`），避免“显示加载成功但实际未生效”。
- 如果没有真实 API 工作流和合法节点映射，直接显示 `NOT_CONFIGURED`，禁止伪造节点 ID/示例推理成功。
- 加入一个只读检查工具，使用 `safetensors.safe_open` **仅检查 header/keys/shape/metadata**，输出文件大小、SHA256（可选，避免每次重复全文件哈希）、tensor 数量、典型 LoRA key、目标模块特征、兼容性疑点；不加载全部权重到 RAM/GPU。
- 根据 ComfyUI 实际 loader 的报错与未匹配 key 报告验证成功/失败；仅检查 header 并不能证明能正常生成。

建议输出详细但不泄露用户输入素材/提示词的诊断日志。

## 6. Windows 本地实现与自动化测试

**不依赖 GPU/ComfyUI 进程也必须通过：**

- 前端正确获取并显示 LoRA 清单、状态、参数说明；默认 None；应用作者预设后控件更新。
- 选择 LoRA 后提交任务，API 和 SQLite 保存完整参数；页面刷新后能回显。
- `strength=0` 路径按无 LoRA 处理；非法负数、超过最大值、未知 LoRA ID/模型 ID 被拒绝。
- 切换模型 profile 时，不兼容 LoRA 被禁用并提示原因。
- 没有真实 workflow 时，`comfyui` 模式明确报未配置，`mock` 模式仍能完成测试视频任务。
- 以 mocked ComfyUI `/object_info` 和 `/prompt` 响应测试 WorkflowAdapter；断言 **无合法映射就不会提交**。
- 旧任务记录仍可读取；所有已有 API 测试、前端构建和 lint/typecheck 继续通过。

若已建 `scripts/dev_windows.ps1`，确保它继续可用；不要添加需要 AWS 才能执行的本地启动条件。

## 7. AWS L40S 上的验证顺序（**仅准备脚本，不要现在远程执行**）

1. 检查 `nvidia-smi`、驱动、PyTorch/CUDA、`df -h`、剩余显存/内存，以及 ComfyUI 版本。驱动升级必须人工确认。
2. 在**加密 EBS** 上存放模型和输出，避免使用停止实例后数据丢失的 NVMe 实例盘。
3. 安装基础 FL2VA INT8 ConvRot checkpoint、工作流要求的 text encoder / video VAE / audio VAE；在没有 LoRA 时先跑中性短视频 smoke test。
4. 将 `HMNSFW-Extreme-Realism.safetensors` 拷贝到 `ComfyUI/models/loras/`；检查 safetensors header、文件哈希（若有官方值）、loader 节点支持情况。
5. 在同一基础模型上启用 LoRA：**strength 0.8、Euler、Beta、18 steps、shift 12、Turbo off**，先跑短视频技术测试。记录 loader 是否报告未匹配 key，若失败不要自动切换 pruned checkpoint。
6. 如出现 OOM，先收集 GPU/CPU 峰值占用、模型卸载状态及实际节点日志，再判断 offload/量化备选；不要静默用 pruned 替代。
7. 只有“实际完成加载 + LoRA 确实生效 + 完成生成”的组合才写入本地兼容性记录并标记 `ready`；记录 ComfyUI commit、工作流 hash、模型文件 hash、LoRA hash、GPU/驱动和结果。
8. 保证 FastAPI（如 `127.0.0.1:7860`）与 ComfyUI（`127.0.0.1:8188`）只监听回环地址，用 Windows SSH 隧道访问，不开放公网媒体上传入口。

## 8. Codex 需要提交的交付物

- 增量修改过的前端组件、后端 schema/API、推理适配器、模型注册配置和自动化测试。
- 文档：在原 `README.md` 增加 LoRA 入口，并更新部署文档、API 参数说明、验证步骤。
- 只读 LoRA 检查工具 + 示例调用；不要修改用户原始 safetensors 文件。
- 测试报告：列出**实际执行过**的命令、通过/失败情况、尚未验证的 GPU 项目。
- 差异清单：原有模型预设和旧任务是否保持兼容、是否有迁移、如何回滚。

### 验收标准

- [ ] 本地 Windows Mock 模式的完整生成/预览流程不回归。
- [ ] 前端能选择 `None` 或 HMNSFW，并能调整 strength 与应用推荐参数。
- [ ] API、数据库、历史详情保存模型和 LoRA 参数快照。
- [ ] 注册表与 API 不暴露任意文件路径，也不能被客户端绕过白名单。
- [ ] 未经验证时，ComfyUI 实推状态保持 `unverified`/`not_configured`，不显示“已可用”。
- [ ] 真实工作流绑定从实际导出的 API JSON 读取，不包含猜造的节点 ID。
- [ ] AWS 安装脚本不会自动下载大模型、更新驱动或公开端口。
- [ ] 已完成 lint/typecheck、后端测试、前端测试及回归测试（若工具环境允许）。

---

## 给 Codex 的最终执行指令

> 现在扫描已有项目并执行以上**增量改造**。先输出简短的文件变更计划，然后直接修改代码、运行测试、修复错误。**不要仅返回解释或伪代码**。在没有 GPU、真实 ComfyUI H3 工作流以及 LoRA 文件路径之前，先把 Mock 和接口、配置、兼容性探测全部实现；真实推理验证保持待办并给出清晰操作说明。不要中途要求重建项目。
