# RunPod A40 首尾帧回归、模型驻留与运行环境恢复

## 实测结论

2026-11-25，在这台专用 A40 上选用 `--cache-classic`，保留 DynamicVRAM、`--disable-pinned-memory` 和 `--disable-async-offload`。相同热任务从 **1008.24 秒降至 180.70 秒**，减少 827.54 秒（82.1%），相当于 5.58 倍速度。冷任务相差 10.90 秒（0.85%）。每个条件只有一次，没有清空文件缓存；冷启动差异不能归因于缓存策略。热任务的实测机制更明确：五个权重文件打开调用降至零，文本编码和六步采样仍实际执行。四次输入都通过原工作流生成 H.264 + AAC，22 帧、24 fps、约 0.917 秒，软件逐帧及音频解码通过。

机器为 NVIDIA A40 46068 MiB、cgroup v2 内存上限 49,999,998,976 字节、CPU quota 765000/100000。系统 Ubuntu 24.04.5、Python 3.12.3、torch 2.8.0+cu129、CUDA runtime 12.9。FoxMotion `deploy/runpod@822fda404c45ec73777cd0ed0570a64dd980b791`，ComfyUI `fe4195f7f4275f2626cbafc703acc3ddde1e5490`。Comfy 启动日志识别了 DynamicVRAM 和 comfy-aimdo 0.4.13。对照顺序是原配置冷、热，再重启 Comfy 单独测试 classic 冷、热；每对共用一个 PID。

| 阶段，秒 | 原配置冷 | 原配置热 | classic 冷 | classic 热 |
|---|---:|---:|---:|---:|
| 五个 loader 函数合计 | 207.33 | 174.29 | 167.21 | 0.00 |
| 其中 Qwen loader | 199.52 | 164.53 | 156.26 | 0.00 |
| 其中扩散主模型 loader | 4.37 | 7.38 | 7.77 | 0.00 |
| Qwen 文本与首尾图像条件编码 | 293.09 | 255.05 | 283.09 | 136.66 |
| 两张关键帧 VAE 编码合计 | 11.93 | 9.64 | 13.53 | 0.25 |
| 六步扩散采样 | 636.40 | 469.88 | 688.12 | 29.06 |
| 视频 VAE 解码 | 74.52 | 49.36 | 71.84 | 1.55 |
| 音频 VAE 解码 | 16.23 | 12.71 | 13.49 | 0.24 |
| load_models_gpu 调用合计，嵌套指标 | 18.02 | 13.05 | 12.08 | 0.66 |
| **提交至成功事件，总生成时间** | **1287.84** | **1008.24** | **1276.94** | **180.70** |

Loader 包含工作流的两个 VAE、Qwen、主模型和 LoRA，反映节点构造与读取成本。动态映射权重后续触页、上传和按需反量化的等待也落在实际执行的编码或采样阶段。GPU 计算段以相同同步边界测量。总时间另外包括图调度、条件处理和编码封装；ffprobe、ffmpeg 复验及结束后观察时间另计。分项有包含关系，不能叠加还原总数。

| 资源与行为 | 原配置冷 | 原配置热 | classic 冷 | classic 热 |
|---|---:|---:|---:|---:|
| 峰值 GPU 显存，MiB | 45484 | 45484 | 45516 | 45068 |
| 平均 GPU 利用率 / 峰值，% | 2.1 / 100 | 2.5 / 100 | 2.3 / 100 | 12.8 / 100 |
| cgroup RAM 采样峰值，GiB | 46.57 | 46.57 | 46.57 | 46.57 |
| Comfy RSS 采样峰值，MiB | 5803 | 7004 | 5857 | 5887 |
| memory.events.max 增量 | 1341 | 1911 | 1614 | 637 |
| oom / oom_kill 增量 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 板卡 PCIe 接收峰值，MiB/s | 3805 | 3418 | 3756 | 6961 |
| 板卡 PCIe 发送峰值，MiB/s | 433 | 425 | 432 | 431 |
| 工作流内 load_torch_file 调用数 | 5 | 5 | 5 | 0 |
| 结束后约 10–20 秒显存中位数，MiB | 1004 | 1004 | 44844 | 44108 |

四次均触及 cgroup 上限，max 增长表示有内存上限压力，未发生 OOM 或 kill。RSS 只计 Comfy 进程，cgroup 还含文件缓存及其他进程。整卡 NVML 速率记录的是采样窗口峰值；classic 热任务仍有显著 CPU/GPU 传输。原配置热任务的进程 read_bytes 增量仅 8192 字节，classic 热为 0；由此无法推算两者从 FUSE 后端各读了多少。

### 驻留、代价与未采用方案

- 原配置两次在采样结束时均观测到 MiniMaxH3 的 patcher loaded_size 约 31.69 GiB。进入空闲后模型管理清单为空，板卡仍有 1004 MiB。热任务重新进入五个 loader。
- classic 热任务命中 15 个节点，包括 5 个权重 loader。更改提示词与 seed 后，Qwen 重新编码，采样器发出第 1–6 步进度。热任务文本编码期间，主模型的五秒采样值在 22.94–26.47 GiB，编码器最高 14.54 GiB。扩散阶段主模型重新升至 31.69 GiB。结束后主模型仍为 31.69 GiB，文本编码器约 7.38 GiB，视频和音频 VAE 分别约 2.63、0.41 GiB。这些都是 patcher 的 loaded_size 计数，含实现开销；板卡显存另由 NVML 计量。
- 原配置在这两个作业间未能保留主模型。classic 复用了加载器并保住大部分主模型，同时允许编码阶段让出部分显存。这个代价体现在约 43 GiB 的空闲板卡占用，热任务文本编码仍用时 136.66 秒。推荐范围限于当前专用 A40、串行 FL2VA 工作流。
- 本轮未运行 highvram、gpu-only、放开锁页或异步卸载的组合。四次预算全部用于一项变量的成对对照，没有将其他方案列为实测失败，也没有验证生产分辨率的质量、峰值和速度。

## 复现口径

在 Pod 的 FoxMotion 项目根目录执行以下命令。API 图 SHA-256 为 `d155c947c5486e2bb79cdfe262430fcad357c2effbc7fec14a8890b26c0d664e`，image binding 为 `72c5877ee9a389119da20b76a3f30541889b1f242d30d189d224203f35a61d89`。基准使用持久卷中的 `image.api.json`，在内存中替换首尾帧、提示词、seed、画布、时长和输出前缀。两张 PNG 由 Pillow 生成，内容是草地、蓝天和移动的小球。每个视频使用原生 FL2VA、Qwen3VL、音视频 VAE、Turbo LoRA、LCM 和原工作流的六步 sigma。输入 320×192、0.9 秒，节点取整到 22 帧、24 fps。H3 节点接受 32 像素倍数及 17k+5 帧，22 帧低于源码记载的训练时长范围。Studio 标准预设仍是 1280×704、5 秒。这里的性能数据只适用于小图回归。

冷启动指重新启动 Comfy 进程后的第一条图。没有清理 Linux 或 Global Volume 的文件缓存。热启动是同一 PID 中第二条图，冷、热分别使用 seed 41、42，并替换一个提示词。两组使用相同的成对输入，热任务会重新文本编码和采样。所有成片通过输出节点历史、ffprobe 双流与帧数、ffmpeg 全流解码检查。这个实验从 Comfy HTTP/WS 入口运行，不经过 Studio 的作业发布流程。

### 源码与资源策略

- `comfy/model_management.py` 和 `comfy/pinned_memory.py` 控制锁页主机内存的配额和注册。关闭 pinned memory 会限制这一传输机制。CPU 副本、内存映射和按需上传依然存在。
- `--disable-async-offload` 将该模块的专用 offload stream 数量设为 0，`get_offload_stream()` 返回 None。模块依然可在 CPU/GPU 之间移动，传输与计算重叠的条件发生变化。
- `main.prompt_worker` 选择 execution cache。`--cache-none` 为输出和节点对象建立 NullCache；`--cache-classic` 使用按输入签名管理的输出缓存及按节点 ID 管理的对象缓存。修改提示词或 seed 会使相应下游失效，独立加载器可继续复用。显存生命周期另由 `current_loaded_models`、弱引用、`load_models_gpu`、`free_memory` 和动态 patcher 管理。空闲 GC 和 CUDA allocator 的保留空间也会影响 nvidia-smi 读数。
- 这个 ComfyUI commit 中，`enables_dynamic_vram()` 会在 highvram 下关闭 DynamicVRAM。`unet_offload_device()` 此时取计算设备，文本编码器和 VAE 的 offload device 在本配置下仍为 CPU。高显存模式同时改变主模型加载与回退策略。主模型文件约 31.70 GiB，编码器约 14.61 GiB，A40 总计约 44.99 GiB。此次先比较缓存，并保留原生按需腾挪策略。
- Comfy 日志报告的主机总 RAM 大于容器配额。采样器从 `/sys/fs/cgroup/memory.current` 与 `memory.events` 读取容器用量和事件，另记 Comfy RSS；三者的范围不同。NVML 每秒记录设备显存、利用率和 PCIe 双向速率，模型清单每五秒及关键函数边界采集。PCIe 为整卡采样，不能换算为每份权重的精确传输字节数。FUSE/mmap 的实际后端读盘量也不能由 loader 时间或进程 read_bytes 单独确定。

### 配置与就绪

在容器盘 `deploy/runpod/.env` 设置 `COMFY_NODE_CACHE=none` 或 `classic`。两种模式都保留 `--disable-pinned-memory --disable-async-offload`。变更模式时等待 Studio 和 Comfy 队列均空闲，按顺序执行：

```bash
bash deploy/runpod/stop.sh
bash deploy/runpod/start.sh
bash deploy/runpod/warmup-status.sh
```

`COMFY_WARMUP=off` 是默认值；设置 `inference` 后，启动脚本在后台排入上述合成工作流，生成一条真实视频并保留输出。相同 Comfy PID 和进程启动时钟只试一次，重复启动不会不断生成。若启动时队列已忙，会跳过，可在空闲后重跑 start.sh。预热日志在 `$RUNTIME_DIR/logs/warmup.log`，逐步状态在 `$RUNTIME_DIR/warmup/<pid>-<start_ticks>/state.json`。只读 status 查询同时检查当前 Supervisor PID 和回环队列。进程就绪表示 HTTP 可答复；模型就绪表示该进程的预热已走到首个扩散进度事件；推理就绪表示本次预热成片已通过完整音视频解码。模型就绪记录曾成功执行，它并不测量当前剩余的驻留字节；进程重启会产生新的就绪记录。关闭预热时，后两项没有预热证明就显示 false。

### 备份与重建

```bash
bash deploy/runpod/runtime.sh backup
bash deploy/runpod/runtime.sh verify BACKUP_ID
# 新 Pod：相同基础镜像、解释器路径、两个固定 commit 和相同 /workspace，先还原部署文件、设置 .env
bash deploy/runpod/runtime.sh restore BACKUP_ID
bash deploy/runpod/restore.sh HISTORY_SNAPSHOT_ID
bash deploy/runpod/start.sh
```

runtime 包含两个 venv、Node、GPU 依赖约束及前端 dist。版本清单包含两套 Python 包的名称和版本、Python ABI 环境、镜像提供的 torch/vision/audio/CUDA、Node、FFmpeg、驱动和两个 Git commit。每份备份使用唯一目录，最后写入 SHA-256 完成标记；verify 和 restore 都检查清单、包校验值、原始 venv 前缀及基础环境，在本机隔离目录解包并导入关键包。restore 需要最终 runtime 路径尚不存在。已有前端构建必须一致。只有通过检查的目录才会启用。基础镜像 GPU 库由原镜像提供，项目与 Comfy 源码由固定检出提供，历史和上传使用原有 snapshot，工作流与权重沿用原卷。恢复依赖时无需 pip、npm 或重新获取权重。

回滚缓存时设 `COMFY_NODE_CACHE=none` 和 `COMFY_WARMUP=off`，在队列空闲时运行上述 stop/start。既有归档、成片和快照可独立使用。


## 部署验收和恢复凭据

classic 热任务由实际预热控制器发起。成片验证后，同一 PID 的 process_ready、model_ready 和 inference_ready 均为 true。结束后 13.5、58.5、118.5、178.6 秒，MiniMaxH3 的 loaded_size 均为 31.689 GiB。约三分钟时 nvidia-smi 报告 43531 MiB、GPU 利用率 0%。观测结束后重启正式 Comfy 以撤下实验探针，部署 `COMFY_NODE_CACHE=classic`、`COMFY_WARMUP=off`。此次重启后空闲显存 271 MiB，进程就绪，等待首个实际任务加载模型；未再发起视频生成。正式 Comfy 使用原生 main.py，Studio 与 worker 正常，image FL2VA preflight READY，两个 API 分别只监听 127.0.0.1:8188 和 127.0.0.1:7860。

正式部署还修复了两个独立启动问题。迁移的 Comfy 检出缺少默认资产数据库的父目录，启动时曾报告 SQLite 无法打开文件。部署脚本改用 runtime 下的 Comfy 用户目录，新数据库通过 quick_check。快速重启时，原端口探测曾将 TIME_WAIT 当作端口被占用；已让探测 socket 使用 SO_REUSEADDR，并验证有实际监听器时仍能拒绝冲突。二者在完成四次实验后修复，未计入性能对照。原工作树已有的锁页、异步卸载和缓存启动参数已纳入补丁。

| 归档 | 实际检查与范围 | SHA-256 |
|---|---|---|
| 原 foxmotion-runtime.tar.gz，774607387 字节 | 完整遍历 tar、gzip CRC 通过；有两个代码树、两个 venv 和前端 dist。未发现 gpu-constraints.txt 或 Node 可执行文件 | `5015f4077d2818d651235a571853e863d92c127193e0ca015ff50b604e9bc24b` |
| 原 node.tar.gz，55149249 字节 | 完整遍历、CRC 通过，含 node/bin/node | `37c339393c5ccffe0f3b413aca1859152041d096863e79025bb660a9d1aa2777` |
| 新 runtime.tar.gz，754198229 字节 | 新建、重新读取并校验，隔离解包、启用前端及 runtime、再导入关键包通过 | `806ec9a5dee6ace300c3855d3564f12324b9c0cac01d753417958e6e69b8472d` |
| 新 manifest.json | Studio 可见 28 个包、Comfy 可见 200 个包；记录 Node v22.18.0、驱动 595.112.07、glibc 2.39 和基础镜像 CUDA 栈 | `1e694ce0110713c85624baf670bf3a57c11497b56f83be7730542b59d3b62e2d` |

旧归档此前没有配套 SHA-256。本次指纹从现存文件计算，可用于今后的迁移校验。新包位于 `/workspace/runtime_backup/phase2-runtime/20261125T142721Z-a6eec76d/`。恢复演练在当前 Pod 的隔离目录完成，未重建另一台 Pod。当前运行环境、基座模型、历史和快照均保留。

### 取回本轮代码和实验记录

持久目录 `/workspace/runtime_backup/phase2-evidence/20261125T142043Z-e4a925` 包含四条中性样片、每秒采样、WebSocket 事件、阶段与模型驻留日志、汇总 JSON、环境恢复验收、部署报告以及从 822fda4 开始的完整修改补丁。补丁在该 commit 的干净导出中校验、应用，并与工作区逐文件对比。重新部署时在 822fda4 干净检出上运行：

```bash
EVIDENCE=/workspace/runtime_backup/phase2-evidence/20261125T142043Z-e4a925
(cd "$EVIDENCE" && sha256sum --quiet -c SHA256SUMS)
git apply --check "$EVIDENCE/FoxMotion-phase2.patch"
git apply "$EVIDENCE/FoxMotion-phase2.patch"
# 在新 Pod 配好部署 .env 后执行
bash deploy/runpod/runtime.sh restore 20261125T142721Z-a6eec76d
```

`deploy/runpod/phase2/probe.py` 使用实际 API 图和调度端点；加 `--measure` 同时写出硬件采样。`profile_comfy.py` 是仅供基准使用的原生 main.py 包装器。持久目录的 pair.sh 和 profile-switch.py 记录本次的确切启动顺序。需要重新计算表格时，在该目录执行下列只读汇总：

```bash
cd /workspace/runtime_backup/phase2-evidence/20261125T142043Z-e4a925
/opt/video-runtime/comfy-venv/bin/python /opt/projects/FoxMotion/deploy/runpod/phase2/summarize.py   baseline-cold baseline-hot classic-cold classic-hot
```
