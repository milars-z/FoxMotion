# FoxMotion Phase 3 多模式部署

## 工作流和模型

在 Studio 选择「文生视频」会走 FL2VA。在「参考生成」中可选择 Ref2VA 基座或 Turbo 4-step。原首尾帧继续走原有 image binding。同一个 Worker 串行派发任务，所有生成和模型操作均在 Pod 的回环接口完成。

| Studio 路由 | 主模型 | 采样 | 参考素材 |
|---|---|---|---|
| 文生视频 | 已有 FL2VA non-pruned INT8 ConvRot | 原模板 res_multistep / simple，提供 6、12、20 步 | 无 |
| 首尾帧 | 同一 FL2VA 权重 | 保留原六步 Turbo 工作流 | 原首、尾帧绑定 |
| 参考生成，基座 | Ref2VA pruned INT8 ConvRot | 原模板 res_multistep / simple，提供 6、12、20 步 | 图、视频、音频混合 |
| 参考生成，Turbo | 同一 Ref2VA 权重，加载官方分发的 Ref2V Turbo LoRA | 作者工作流的 Euler / simple、4 步、LoRA 强度 1、视频与音频 shift 12/3 | 同上 |

新模式都提供 Prompt、Seed、320×192 至 1280×704 的四档分辨率及 0.9、2、5 秒时长。H3 在 24 fps 下把帧数对齐至 17k+5。当前线上基座使用 res_multistep / simple，可调步数。Turbo 固定四步和作者采样组合。另行验证的可选配置位于 `deploy/runpod/workflows/sampling-options/`。它为标准基座增加 res_multistep / euler 与 simple / normal 的白名单。当前进程保留实际生成所用的 bindings。后续维护窗口更新 API 与 Worker 后，备份线上 bindings，再按文件名安装此可选配置即可启用选择器。工作流安装器只处理本项目提供的文件，发现同名文件内容不一致会停止。

从本机已安装的 Comfy 官方原生模板转换出 API 图，把默认剪枝 FL2VA 和 FP16 视频 VAE 替换为现场已有的指定权重。两个模式共用原有 Qwen3VL、Video VAE、Audio VAE 和相同 loader 标识。Ref2V Turbo 的 scheduler 从原始 UNET 取输入，LoRA 接 sigma shift 后进入 guider，遵循作者图的连线。模板包版本、三个原图的 SHA-256 和作者仓库 revision 记录在 `deploy/runpod/workflows/sources.json`。[Comfy T2V 原图](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_t2v.json) · [Comfy Ref2VA 原图](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_r2v.json) · [Turbo 作者原图](https://github.com/ModelTC/Minimax-H3-Turbo/blob/main/example_workflows/video_minimax_h3_ref2v_lightx2v_turbo.json)

| 新增持久权重 | 字节 | SHA-256 |
|---|---:|---|
| minimax_h3_ref2va_pruned_int8_convrot.safetensors | 20970379616 | `9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779` |
| minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors | 1956193000 | `5b9ab5ade15d0775676d01a907268a69a1468dc6033b3b0d3ded5502f3ebb84c` |

下载前按文件名扫描模型目录。按官方固定 revision 下载缺失文件，校验临时文件和持久卷回读的 SHA-256 后发布。原有 FL2VA、编码器、VAE 和 LoRA 原位复用。[Comfy-Org 权重清单](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/4b53a35191437de05370da548e59aeabe2a5a09a)

### 参考素材

按类型从 1 编号，可混合最多 9 张图、3 段视频、3 段音频。预览中的前移、后移决定同类型素材顺序，删除会重新编号。提示词示例为「依照 <Picture 2> 的颜色，采用 <Video 1> 的运动和 <Audio 1> 的节拍」。提交时校验引用的序号。原始上传保留在 Studio 历史。视频输入取画面，声音可单独上传，因此视频不会占用独立音频的序号。Worker 把视频标准化为 24 fps 并限制解码画布，音频标准化为 32 kHz 双声道。每段最多取前 15 秒，视频至少需要 5 帧；H3 还会按照生成帧数裁剪视频参考。

### GPU 生命周期

- 保留 `--cache-classic --disable-pinned-memory --disable-async-offload` 和 DynamicVRAM。工作流按实际权重选择分支。普通任务结束时不调用 /free。
- 固定版本的 `execution.py` 在每次图运行前按输入签名设置 classic cache 并清理不再使用的输出。相同主模型及共享 loader 保持同一类、节点 ID、文件和输入。参考图的可变输入由原生 Autogrow 绑定生成，替换素材或 Prompt 会使相应条件与采样节点重新执行。
- `FoxMotionUNETLoader` 是原生 UNETLoader 的薄包装。仅在 loader 真正执行时检查 Comfy 正在管理的 H3 patcher 及实际源文件。若原生缓存清理后仍有不同 H3 基座驻留，调用原生 free_memory，并通过 keep_loaded 排除共享组件。随后执行原有 loader。原生缓存已处理旧基座时，无需主动卸载。
- 切换 LoRA 时原生 LoraLoader 按文件路径复用或替换其权重，load_lora_for_models 克隆基座 patcher 并在克隆上登记新补丁。文件、强度和基座输入参与节点缓存签名。CPU 验证以两个不同 LoRA 作用于小型线性层，按 A、裸基座、B、裸基座检查张量精确相等。
- 固定版 Comfy 的 POST /free 只设置队列标志。卸载和 cache reset 发生在 prompt worker 的安全边界。Studio 的「释放显存」在 Worker 领取任务所用的同一把锁下检查 Studio 队列，再请求原生 /free。Comfy 入口将 /prompt 与 /free 的受理串行化，队列有任务时返回 409；Studio 等到卸载确认后才报告成功。该操作保留 Studio 历史、成片和权重。
- GPU 面板显示当前设备用量、实际 patcher 报告的驻留字节、工作状态、cgroup memory.current/max 和 memory.events。设备用量与各 patcher 字节之和可能不同，CUDA 上下文、激活和分配器也会占用显存。
- `GPU_IDLE_RELEASE_SECONDS=0` 默认保留缓存。正整数秒启用空闲释放。修改部署环境变量后在双队列空闲时重启服务。仅项目自带 `foxmotion_runtime` 扩展列入 Comfy 白名单；继续禁用外部 API 节点和其他自定义节点。

### 部署与回滚

```bash
cd /opt/projects/FoxMotion
bash deploy/runpod/install-workflows.sh
bash deploy/runpod/start.sh
bash deploy/runpod/studio_cli.sh preflight
curl -fsS http://127.0.0.1:7860/api/gpu
```

升级已运行实例时先确认两个队列空闲，再使用 stop.sh / start.sh。首次发布新权重后若 FUSE 尚未使 Comfy 的文件名缓存失效，等待文件可读并在空闲时重启 Comfy。运行库版本保持 torch 2.8.0+cu129、CUDA 12.9 和 ComfyUI fe4195f7f4275f2626cbafc703acc3ddde1e5490。

Phase 2 在 Pod 本地 Git 中单独保存为 `35b866d`。回滚时可在单独干净检出使用该 commit 与 Phase 2 的前端构建。沿用原 image binding、classic 配置、历史库和成片目录，只启用原首尾帧功能。Phase 2 原离线包做精确 commit 校验，清洁恢复顺序见该阶段报告；Phase 3 的版本配套恢复信息另存于持久卷。

## 2026-11-25 真实生成验收

单卡 NVIDIA A40、46,068 MiB，容器 RAM 上限 49,999,998,976 字节（46.57 GiB）。两次均由 Chromium 提交 Studio 网页表单，经同一个 Worker 调用回环 ComfyUI，Seed 12345、320×192、24 fps、目标 0.9 秒。第一条用橙色球与蓝色桌面的文字描述，第二条使用两张几何图形、两段合成运动视频和两段合成音频，调整图片顺序后在 Prompt 引用六个素材。首轮两次预算已用完。

| 实验 | Worker 领取到成片入库 | Comfy 执行 | 采样进度条 | 观测显存峰值¹ | 观测 cgroup RAM 峰值 |
|---|---:|---:|---:|---:|---:|
| T2V，FL2VA 基座，6 步 | 1,216.91 秒 | 1,215.23 秒 | 约 14 秒 | 43.93 GiB | 46.566 GiB |
| Ref2VA，Turbo，4 步 | 532.88 秒 | 530.49 秒 | 约 5 秒 | 37.83 GiB | 46.566 GiB |

¹ 显存和 RAM 取 Studio 设备接口约每两秒一次的观测最大值。T2V 自领取后 3.32 秒起有 578 次采样；Ref2VA 提交页关闭时观测脚本退出，Worker 继续执行，按相同任务 ID 恢复观测后有 223 次采样，前 68.62 秒无记录。总耗时来自 Worker 时间戳与 Comfy 执行历史，覆盖完整任务。进度条只计采样迭代；本轮无独立文本编码、权重读盘、VAE 编解码耗时或整程 GPU 利用率，因此不把剩余耗时分摊到这些阶段。两条使用不同基座和输入，耗时也不构成冷热提速对照。Phase 2 的同工作流冷热对照在该阶段报告中。

- 两个 MP4 都是 H.264 + AAC，视频实解 22 帧、0.916667 秒。Chromium 在历史记录中实际播放、下载；Range 请求均返回 206。整文件 FFmpeg 解码通过，下载 SHA-256 分别为 `ce609c8523faaa8ba85a7f1fabfd19836e14556bb7b68c134c95d21a7d0f7a02` 和 `bee89726b23ec48de9cfb593e897abfe76ed07939ddb5757751f53338e487e98`，与持久卷的两个成片逐一相同。
- 首条 Comfy 运行记录加载了基座、文本编码器和两个 VAE。第二条原生执行历史明确列出缓存命中 `140:128`（Qwen）、`140:119` 与 `140:120`（两个 VAE），日志只请求新的 H3 基座。全部已采样时刻中，两扩散基座同时报告非零驻留的次数为 0。交接前 Ref2VA patcher 带补丁并驻留 19.53 GiB，Qwen 14.63 GiB、Video VAE 2.63 GiB、Audio VAE 0.59 GiB。首次空闲 NVIDIA 驱动读数 38,699 / 46,068 MiB，利用率 0%。
- 两次采样窗口内 `memory.events.max` 分别增加 1,073 和 2,459 次，触及 cgroup 限额，`oom`、`oom_kill` 均为 0。Ref2VA 结束时 `max` 累计 9,652。首次空闲采样的内存包括约 41.41 GiB 文件页和 4.28 GiB 匿名页，实际交接读数另存于 `handoff.json`。
- 后端 70 项、前端 6 项测试通过，前端 lint 和类型检查、生产构建通过。固定 ComfyUI 的原生验证器接受 8 个默认工作图，以及包含默认和可选采样配置在内的 27 个图。原首尾帧在原生图中通过验证；Phase 3 没有额外生成首尾帧或裸 Ref2VA 成片。对 Turbo 的 208 个 LoRA 目标检查权重名和张量形状均匹配，两次运行的定向日志未报权重不匹配。
- T2V 执行中前端禁用释放按钮，Studio 拒绝释放请求并返回 409。释放成功与等待确认路径使用模拟 HTTP 传输回归。为保留 Ref2VA 热状态，交接期间只读查询；空闲时的真实释放及自动释放未在 GPU 上执行。空闲释放保持默认关闭。
- 实际上线的默认绑定与实测内容逐文件相同。源码另保存了更精确的空闲计时、按完整文件路径比较基座和可选采样绑定。新 Python 逻辑在下一次计划内重启时载入，额外采样器需单独启用。当前网页与回环服务可直接手动生成。

### 持久卷交接文件

本次交接证据位于 `/workspace/runtime_backup/phase3-evidence/20261125T152711Z-fc7357cb`，其中 `measurements.json` 记录两次任务指标，`handoff.json` 记录备份后的服务、队列、GPU 和内存，`runtime-receipt.json` 给出该 commit 对应的离线包 ID 和校验值。代码增量 Git bundle 以 `822fda4` 为共同基线，含 Phase 2 和 Phase 3 两次提交。使用已有该基线的克隆运行 `git bundle verify <bundle>`，再从 bundle fetch `refs/heads/deploy/runpod` 即可恢复提交。有效部署参数、原有首尾帧及三套新工作流与绑定的备份位于 `/workspace/config/runpod/phase3/20261125T152711Z-fc7357cb`。校验命令为在对应目录运行 `sha256sum -c SHA256SUMS`。只在迁移后的对应 checkout 用 `deploy/runpod/runtime.sh restore <ID>`；在线备份和隔离恢复校验无需停止服务。
