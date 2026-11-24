# FoxMotion 在 RunPod Secure Cloud 的部署与恢复

本手册适用于 Ubuntu 24.04 GPU Pod、50 GB 容器盘和挂载于 `/workspace` 的 `fuse.geesefs` Global Volume。部署从 CPU Mock 开始。Windows 已测试业务流程与快照逻辑；Pod 上的 Linux 依赖安装、Supervisor、真实挂载、A40 H3 基座和 LoRA 出片均待实测。

## 从已克隆的仓库启动 Mock

在 VS Code Remote SSH 的 **Pod 终端**运行。首次启动选用已完成 GPU 检查的 RunPod PyTorch 镜像。只给 SSH 守护进程映射 TCP 端口，移除模板中不需要的 HTTP/TCP 转发与 Jupyter 等公开入口。

```bash
cd /opt/projects/FoxMotion
git status --short --branch
git fetch origin
git switch deploy/runpod
git pull --ff-only origin deploy/runpod
test -e deploy/runpod/.env || cp deploy/runpod/config.env.example deploy/runpod/.env
bash deploy/runpod/check_env.sh
bash deploy/runpod/bootstrap.sh
bash deploy/runpod/start.sh
curl -fsS --noproxy '*' http://127.0.0.1:7860/api/system
ss -lnt
```

预期 `backend=mock`、`worker.online=true`，Studio 监听 `127.0.0.1:7860`。若现场已有未提交修改，先保留该工作区，可另外 clone 本分支到容器盘的新目录。脚本根据自身位置定位项目。基础命令需要 Bash、Git、curl、tar/xz、sha256sum、util-linux、Python 3.12（含 venv/ensurepip）以及带 libx264 的 FFmpeg/ffprobe。缺少系统命令时在 Pod 镜像中补齐后重试。启动时不需要 Vite 开发服务器。

在 **Windows PowerShell** 使用 RunPod Connect 中「SSH over exposed TCP」给出的 SSH 地址、端口、账号和私钥。下面的 `foxmotion-pod` 指向 VS Code 已使用的同一直接 TCP SSH Host 别名。保持窗口打开，访问 [本地 Studio](http://localhost:7860)。若 Windows 开发服务占用 7860，将 `-L` 的第一个端口改为 17860，然后访问 `http://localhost:17860`。[RunPod SSH 指南](https://docs.runpod.io/pods/configuration/use-ssh)

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:7860:127.0.0.1:7860 foxmotion-pod
```

提交两个 Mock 任务，等待第一条出片，检查播放、下载和刷新后的历史，取消第二条。执行 `find /workspace/outputs -type f` 核对 UUID 目录下的成片，再测试网页删除。第二条若已开始或完成，可在生成期间另提交并立即取消。

## 原有架构与分盘

React 构建产物由 FastAPI 提供。API 写 SQLite 队列，持有本机文件锁的独立 Worker 串行领取任务，通过原有 Mock 或 ComfyUI HTTP/WS 适配器执行。AWS 使用 systemd 和 EBS，此分支给 Pod 单独使用 Supervisor、私有 Unix socket 和每个服务各自的进程组。SSH 会话结束后服务继续运行。启动脚本只在本机回环地址检查健康。

| 路径或设置 | 内容与生命周期 |
| --- | --- |
| `/opt/projects/FoxMotion`、`/opt/ComfyUI` | Git 检出、前端 node_modules 与 dist。由 Git 及 bootstrap 重建。 |
| `RUNTIME_DIR=/opt/video-runtime` | Studio venv、单独的 ComfyUI venv、Node 回退安装、npm/HF 缓存、临时文件、轮转日志、进程 socket。 |
| `DATA_DIR=/opt/video-runtime/data` | SQLite/WAL、Worker 锁、上传、生成中间文件、本机 smoke 记录。历史和输入按快照恢复。 |
| `/opt/video-runtime/comfy` | Comfy 输入、临时输出、工作目录。FFmpeg 的可寻址输出、验证、模型之外的频繁写入均落在容器盘。 |
| `COMFYUI_MODEL_DIR=/workspace/models` | 扩散模型、编码器、VAE、LoRA、embeddings 分类子目录。已有文件原位复用。 |
| `WORKFLOW_DIR=/workspace/config/workflows` | 经审查、不含令牌或私人素材的 API 格式 JSON 及 bindings。按普通文件写入，启用前检查内容。 |
| `RESULT_DIR=/workspace/outputs` | 编码完成后按任务 UUID 顺序复制成片，关闭文件并读取校验 SHA-256，成功才提交完成状态。播放、Range 下载和按任务删除使用同一位置。 |
| `BACKUP_DIR=/workspace/backup` | 每份快照一个 tar 和最后写入的 SHA-256 完成标记。保留期限由卷所有者管理。 |

调整 `deploy/runpod/.env` 中的绝对路径。此文件由本地 Bash 读取，应由 Pod 所有者管理，放在容器盘。GitHub 仅保存示例。重建需要的路径值、镜像标识及 ComfyUI commit 可作为**纯文本记录**放入 `/workspace/config`，在新 Pod 人工核对并填入本地配置。推理图中的连接、节点、模型 profile 和参数仍由项目原有校验控制。

环境脚本检查项目与依赖目录所在文件系统，以及持久目录是否位于约定的实际挂载下。卷掉线时先修复挂载再启动。每组输出和备份目录只分配给一个运行中的 FoxMotion Pod。Global Volume 有跨挂载可见延迟，操作后应在重建或重新挂载的实例上再验一次成片与快照。其权限位、锁和原子 rename 有明确限制。[RunPod Global Volume 说明](https://docs.runpod.io/storage/globalvolume/overview)

## 为真实推理准备环境

1. 先完成以上 Mock 验收。停止服务并运行一次备份。
2. 在 `deploy/runpod/.env` 将 `INFERENCE_BACKEND` 改为 `comfyui`。将 `BASE_PYTHON` 指向 Pod 镜像实际可以导入 torch、torchvision、torchaudio 的 Python 3.12。设置 `COMFYUI_REV` 为你审查过的官方 40 位 Git commit。已有 `/opt/ComfyUI` 应与该提交一致；允许选择其他容器盘检查路径。原生 H3 基础模板文档列出的 ComfyUI 起点为 0.30.0。版本号本身不能证明插件或量化内核兼容。[ComfyUI H3 指南](https://docs.comfy.org/tutorials/video/minimax/minimax-h3) · [0.30.0 依赖](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/v0.30.0/requirements.txt)
3. 在持久卷准备合法取得的对应权重，按自己的官方模板导出 API JSON 并配置 bindings。FL2VA 对应文生和首尾帧，Ref2VA 对应参考素材。当前注册的 non-pruned FL2VA 与模板默认的 pruned 文件不同。脚本只清点已有权重并进行只读 mmap 首尾探测。每份权重还应对照发行方 SHA-256 完整校验。已有目录与模型不会被初始化过程替换。[模型文件及模板](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native) · [导出与映射](docs/WORKFLOW_EXPORT.md)
4. 运行以下命令。已有预装 GPU 组件通过单独的 Comfy venv 继承。bootstrap 先约束已装 GPU 包、检查 pip 的 wheel 计划，再按计划的精确 URL 和 SHA-256 安装其余依赖。解析冲突、缺 Linux wheel 或要求变更 torch/CUDA 栈时会失败并报告包名。应重新评估所选 ComfyUI commit 与镜像组合。Studio 单独使用项目的 Python 约束；Node 22+ 优先复用，缺少时下载已校验的官方 Node 分发包。

```bash
cd /opt/projects/FoxMotion
bash deploy/runpod/check_env.sh
bash deploy/runpod/bootstrap.sh
bash deploy/runpod/start.sh --comfy-only
curl -fsS --noproxy '*' http://127.0.0.1:8188/queue
bash deploy/runpod/studio_cli.sh preflight
```

此时 `preflight` 可以报告 `NOT_CONFIGURED`、`MODEL_NOT_FOUND` 或 `UNVERIFIED`。Comfy 启动参数使用回环地址、原生节点和本机额外模型路径配置，禁用 API 节点及自定义节点。`--comfy-only` 允许在 Studio 路由就绪前手动校验。Windows 可临时再开 SSH 隧道 `ssh -N -L 127.0.0.1:8188:127.0.0.1:8188 foxmotion-pod`。先用中性素材和固定 seed 完成基座最小工作流，核对 `/object_info`、`/history/{prompt_id}`、日志中的 loader，以及实际播放的文件；随后才做相同参数的 LoRA A/B。检查每个 loader 的未匹配 key 数与可见效果。将 [H3 验收手册](docs/README_H3_LORA_INTEGRATION.md) 中的 `.venv/bin/python -m backend.record_smoke` 替换为 `bash deploy/runpod/studio_cli.sh record_smoke`，按**实际观察值**传参。GPU 验收记录位于本机，重建后重新验证。然后运行：

```bash
bash deploy/runpod/studio_cli.sh preflight
bash deploy/runpod/start.sh
curl -fsS --noproxy '*' http://127.0.0.1:7860/api/capabilities
```

## 容量与待验证项目

用户提供的目标镜像为 Ubuntu 24.04.5 LTS、Python 3.12.3、NVIDIA 驱动 595.112.07，Pod 分配值为 9 vCPU 和 50 GB RAM。GPU 识别、FP16 CUDA 矩阵乘法、卷基础读写及 Remote SSH 是用户已完成的检查。

可参考的公开数字来自 Comfy 团队：其最小模型组合经剪枝、量化后，总内存占用约 42.5 GB，并使用动态显存卸载。这里的 non-pruned FL2VA 加 LoRA 使用另一套权重，A40 上的 GPU 峰值和 cgroup RAM 峰值均需分别测量。[Comfy 官方 H3 工程说明](https://blog.comfy.org/p/minimax-h3-day-0-support-in-comfyui)

Windows 工作区按普通文件长度实测，Studio 开发 venv 为 115.8 MiB，前端 node_modules 为 159.3 MiB，生产 dist 为 0.3 MiB。这些数字只覆盖本地应用，Linux 上的 ComfyUI、GPU 依赖、运行缓存需单独计量。

按 PyPI 发行元数据、Linux 环境标记和 CPython 3.12.3 的 wheel 标签核对，Studio 加 Supervisor 共 27 个 wheel，压缩下载量合计 27.72 MiB。`deploy/runpod/studio-linux.lock` 锁定 x86_64 发行文件及 SHA-256，bootstrap 同时应用项目原有 requirements 与 constraints。更改依赖版本或 CPU 架构时应重新核对这份清单。该统计不包括 ComfyUI、Node 或模型。

| 资源 | 现有依据 | Pod 上需要记录 |
| --- | --- | --- |
| Python、CUDA、torch | 用户已在 Python 3.12.3、torch 2.8.0+cu129 完成 FP16 CUDA 矩阵测试。驱动报告 13.2，PyTorch runtime 为 12.9。官方发布了 torch 2.8.0 / torchvision 0.23.0 / torchaudio 2.8.0 的 cu129 组合。 | 检查两个解释器中 torch 的实际路径、`pip check` 结果，以及 ComfyUI 的原生算子导入。 |
| 显存 | A40 的 46068 MiB 约为 45.0 GiB，属于 Ampere SM 8.6。FP16 测试尚不能覆盖 INT8 ConvRot、NVFP4 编码器及其回退实现。 | 实测加载、采样、音视频 VAE 解码、加 LoRA 四阶段峰值。记录模板、尺寸、帧数、精度、耗时和失败位置。 |
| 系统内存 | 50 GB 配额还需容纳 Python、模型 offload、读缓存、解码及其他常驻进程。 | 环境检查读取 cgroup v2 或 v1 的可见祖先限制、CPU quota 和进程亲和性。若显示 unlimited/unknown，核对控制台配额。追踪 memory.current、memory.events 的 oom_kill 差值。 |
| 容器盘 | non-pruned INT8 FL2VA 文件约 34 GB，NVFP4 AWQ 文本编码器约 15.7 GB，两者已合计 49.7 GB，另需 VAE、工作空间与应用。 | 保持模型在卷上。记录 Linux 轮子、Comfy 包、node_modules、输入、FFmpeg 暂存及 Comfy 原始输出实占空间。默认每个阶段要求至少 8 GiB 可用，此阈值用于提早发现空间压力，安装及出片峰值仍需实测。 |

文件体积是磁盘数据，运行峰值取决于驻留、反量化、激活和 offload。复用 torch 和关闭 pip 缓存能减少重复安装。全套依赖在该镜像上的增量大小尚未测量，50 GB 仍可能耗尽。若 FUSE 的 mmap 或随机读取失败、缓存挤占 RAM，优先增配合适的 POSIX 容量后重新设计模型暂存，仅在测得空间足够时尝试本地副本。[扩散权重体积](https://huggingface.co/Comfy-Org/MiniMax-H3/blob/main/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors) · [编码器体积](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/text_encoders) · [PyTorch 官方版本](https://pytorch.org/get-started/previous-versions/) · [NVIDIA A40](https://www.nvidia.com/en-us/data-center/a40/)

```bash
bash deploy/runpod/check_env.sh
df -h /opt/projects/FoxMotion /opt/video-runtime /workspace
du -sh /opt/video-runtime/* /opt/projects/FoxMotion/frontend/node_modules
nvidia-smi --query-gpu=timestamp,name,memory.used,memory.free,utilization.gpu --format=csv -l 1
# 在另一终端观察，Ctrl+C 停止采样
tail -f /opt/video-runtime/logs/{comfy,worker}.log
```

## 快照、停机和重建

成片在任务完成时写入卷。**历史、提示词和上传素材的恢复点是最近一次成功快照。** 在重要批次结束和释放 Pod 前运行 `backup.sh`。它通过 SQLite online backup 生成一致的库副本，流式归档该副本及它引用的上传。并发删除导致读取失败时，停止服务后重试。只有具有有效 `.sha256` 的同 ID tar 可恢复。模型、成片、已有卷上工作流、凭据、缓存、锁与旧 GPU 验收记录均不进入此归档。

```bash
cd /opt/projects/FoxMotion
bash deploy/runpod/stop.sh
bash deploy/runpod/backup.sh
ls -1 /workspace/backup/*.sha256
```

记下输出的 snapshot ID、仓库 commit、镜像与路径设置。脚本仅控制 FoxMotion 服务。控制台停止或重建 Pod 由所有者执行。异常断电时，快照后的任务历史和输入可能丢失，当时已发布的成片仍可按任务 UUID 在卷上查找。

在新 Pod 重新挂载**同一个**卷。如果缺代码，运行以下第一组命令；已有 clone 则按开头步骤更新。重新填写本地路径后先 bootstrap，**恢复完成后再首次启动 Studio**。

```bash
mkdir -p /opt/projects
git clone --branch deploy/runpod https://github.com/milars-z/FoxMotion.git /opt/projects/FoxMotion
cd /opt/projects/FoxMotion
test -e deploy/runpod/.env || cp deploy/runpod/config.env.example deploy/runpod/.env
bash deploy/runpod/bootstrap.sh
ls -1 /workspace/backup/*.sha256
read -rp 'Snapshot ID (不含扩展名): ' snapshot
bash deploy/runpod/restore.sh "$snapshot"
bash deploy/runpod/start.sh
```

restore 验证 SHA-256、归档路径、输入清单和 SQLite 完整性，最后在容器盘启用新目录。目标 DATA_DIR 必须尚不存在；恢复演练请指定新的 DATA_DIR。排队和执行中的旧任务均置为 interrupted，避免重复占用 GPU。恢复时发现对应成片已经删除，会明确标为缺失；卷存在可见性延迟时先确认成片可读再恢复。网页删除会清理在线成片和任务，既有快照仍持有当时的输入和历史。按个人留存期限另行清理整对快照文件。卷内快照与成片共用同一故障域，重要资料应另存受控、加密的独立副本。

## 平台条款与私有数据

RunPod 将应用配置、内容授权、备份与数据保护列入用户责任，条款将色情或露骨成人内容列为未经授权的内容。使用已获许可的中性素材测试基座和 LoRA，遵守模型、LoRA 各自许可证及肖像授权。SSH 隧道限制网页入口，卷内快照以明文保存提示词与上传。处理敏感数据前应确认账户成员、卷挂载权限、所在区域、传输及静态加密安排，并在需要时执行独立加密备份。勿将云密钥、SSH 私钥、模型或素材提交到 Git。Comfy 日志可能含节点诊断信息，分享前人工脱敏。[RunPod 服务条款](https://www.runpod.io/legal/terms-of-service) · [隐私政策](https://www.runpod.io/legal/privacy-policy) · [H3 许可说明](https://docs.comfy.org/tutorials/video/minimax/minimax-h3)

## 本地复查

Windows 在仓库根目录运行以下命令。自动化测试使用普通本地目录模拟独立成片盘、断写和重建，真实 FUSE 语义需在目标 Pod 复测。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run test:e2e
Get-ChildItem deploy/runpod/*.sh | ForEach-Object {
    & 'C:/Program Files/Git/bin/bash.exe' -n $_.FullName
    if ($LASTEXITCODE) { throw "Bash syntax check failed: $_" }
}
```
