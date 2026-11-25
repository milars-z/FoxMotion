# RunPod 快速恢复 · 2026-11-26

本次配套版本由三个固定位置标识。在 Pod Bash 中先执行下面的公共步骤。清单字段 `project` 是本版 GitHub 提交的完整 SHA；在每次恢复前读取并核对，避免分支次日向前移动造成版本错配。

```bash
set -euo pipefail
R=20261125T170005Z-5d466095
S=20261125T170005Z-d3da746800da44feb2872cb4793a923f
A=/workspace/runtime_backup/phase2-runtime/$R
C=/workspace/config/runpod/closeout/$R
P=/opt/projects/FoxMotion
test "$(findmnt -T /workspace -n -o TARGET,FSTYPE | xargs)" = "/workspace fuse.geesefs"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
(cd "$A" && sha256sum -c COMPLETE.sha256)
(cd "$C" && sha256sum --quiet -c SHA256SUMS)
FOX_SHA=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["project"])' "$A/manifest.json")
COMFY_REV=fe4195f7f4275f2626cbafc703acc3ddde1e5490
printf 'FoxMotion commit %s\n' "$FOX_SHA"
```

### A · 容器盘保留

```bash
cd "$P"
test "$(git rev-parse HEAD)" = "$FOX_SHA"
git status --short --branch
test "$(git -C /opt/ComfyUI rev-parse HEAD)" = "$COMFY_REV"
test -s /opt/video-runtime/data/studio.db
bash deploy/runpod/check_env.sh
bash deploy/runpod/start.sh
```

启动器检查已运行的进程并启动缺失服务。当前配置关闭自动推理预热。接着执行下方健康检查。

### B · 容器盘丢失

1. 在 RunPod 为兼容 GPU 分配同一基础镜像并挂回原 Global Volume 至 `/workspace`。恢复原有公钥登录。Windows 可用 `Get-Content "$env:USERPROFILE/.ssh/id_ed25519.pub"` 取得公钥。通过可信 RunPod 控制台核对新服务器 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` 指纹，在本地只接受与此一致的新记录。
2. 在 Windows 的 `~/.ssh/config` 更新别名，替换下面两项占位值。首次连接可用 `ssh -o StrictHostKeyChecking=ask foxmotion-pod hostname` 核对显示的指纹，之后用 `ssh -o BatchMode=yes foxmotion-pod`。遇到旧键冲突，先通过控制台核实主机变化，只移除与旧主机及端口相符的 known_hosts 条目。

```sshconfig
Host foxmotion-pod
    HostName NEW_POD_IP
    Port NEW_SSH_PORT
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ForwardAgent no
    StrictHostKeyChecking yes
```

3. 在新 Pod 上执行公共步骤。准备项目源，当前目录与 /opt/ComfyUI、/opt/video-runtime 均应为新路径。已有部署转回 A，遇到部分恢复保留目录作检查。

```bash
install -d /opt/projects
test ! -e "$P"
git clone --single-branch --branch deploy/runpod https://github.com/milars-z/FoxMotion.git "$P"
```

若私有仓库在 Pod 缺少读取权限，在已认证 Windows 工作副本执行下列四行。使用以前不存在的临时 bundle 文件，并确认 fetch 成功；如果发生错误，停止后续步骤。只把 GitHub 上该分支的 Git 对象经 SSH 发送到新 Pod。

```powershell
git fetch origin refs/heads/deploy/runpod:refs/remotes/origin/deploy/runpod
$bundle = Join-Path $env:TEMP ("FoxMotion-" + [guid]::NewGuid() + ".bundle")
git bundle create $bundle refs/remotes/origin/deploy/runpod
scp -o BatchMode=yes -o ForwardAgent=no $bundle foxmotion-pod:/tmp/foxmotion-restore.bundle
```

在 Pod 上仅当项目目录不存在时采用这个替代检出步骤。

```bash
test ! -e "$P"
git init -b deploy/runpod "$P"
git -C "$P" fetch /tmp/foxmotion-restore.bundle refs/remotes/origin/deploy/runpod
git -C "$P" merge --ff-only FETCH_HEAD
git -C "$P" remote add origin https://github.com/milars-z/FoxMotion.git
```

4. 在同一 Pod shell 继续。只在新路径恢复，匹配固定检出与配套包。还原工作流前核对所有同名文件。模型仍在原卷，以下检查读取文件元数据。逐字节比对八个模型可在需要排查介质故障时另行进行，依据见 models.json 的 evidence 字段。

```bash
cd "$P"
test -z "$(git status --porcelain --untracked-files=no)"
if [ "$(git rev-parse HEAD)" != "$FOX_SHA" ]; then git switch --detach "$FOX_SHA"; fi
test "$(git rev-parse HEAD)" = "$FOX_SHA"
test ! -e /opt/ComfyUI
test ! -e /opt/video-runtime
stage=$(mktemp -d /opt/foxmotion-comfy-XXXXXX)
tar -xzf "$C/comfy-checkout.tar.gz" -C "$stage"
git clone --depth=1 "file://$stage/comfy.git" /opt/ComfyUI
test "$(git -C /opt/ComfyUI rev-parse HEAD)" = "$COMFY_REV"
test ! -e deploy/runpod/.env
install -m 600 "$C/deployment.env" deploy/runpod/.env
while IFS= read -r f; do
    target=/workspace/config/workflows/$f
    mkdir -p "$(dirname "$target")"
    if [ ! -e "$target" ]; then cp --no-clobber "$C/workflows/$f" "$target"; fi
    cmp "$C/workflows/$f" "$target"
done < <(cd "$C/workflows" && find . -type f | sort)
python3 - "$C/models.json" <<'PY'
import json, pathlib, sys
m = json.load(open(sys.argv[1]))
for item in m["files"]:
    assert (pathlib.Path(m["root"]) / item["path"]).stat().st_size == item["bytes"], item["path"]
print(len(m["files"]), "weight files present")
PY
cmp frontend/package-lock.json "$C/frontend-package-lock.json"
test ! -e frontend/node_modules
tar -xzf "$C/frontend-node_modules.tar.gz" -C frontend
bash deploy/runpod/check_env.sh
bash deploy/runpod/runtime.sh restore "$R"
test ! -e /opt/video-runtime/data
test "$(cat /workspace/backup/$S.sha256)" = "$(sha256sum /workspace/backup/$S.tar | cut -d' ' -f1)"
bash deploy/runpod/restore.sh "$S"
bash deploy/runpod/start.sh
```

### 服务、模型和本地网页

```bash
cd /opt/projects/FoxMotion
/opt/video-runtime/studio/bin/supervisorctl -c /opt/video-runtime/supervisord.conf status
curl -fsS http://127.0.0.1:7860/api/health
curl -fsS http://127.0.0.1:7860/api/system | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["backend"],d["worker"]["online"]); assert d["worker"]["online"]'
curl -fsS http://127.0.0.1:8188/queue | python3 -c 'import json,sys; print({k:len(v) for k,v in json.load(sys.stdin).items()})'
curl -fsS http://127.0.0.1:7860/api/gpu
bash deploy/runpod/studio_cli.sh preflight
bash deploy/runpod/warmup-status.sh
```

进程健康、GPU 模型驻留、完整推理验证各有独立指标。刚启动时显存通常较低。预热记录按 Comfy PID 和启动时刻区分；手动推理可通过 GPU 面板观察。需要实际 FL2VA 推理预热且允许使用一个生成任务时，在两个队列均空后执行：

```bash
cd /opt/projects/FoxMotion
/opt/video-runtime/comfy-venv/bin/python deploy/runpod/phase2/warmup.py run /opt/video-runtime /workspace/config/workflows/image.api.json
bash deploy/runpod/warmup-status.sh
```

Windows 保持以下 OpenSSH 进程运行，浏览器打开 http://127.0.0.1:17860/ 。端口占用时选另一个本地端口；服务只绑定 Pod 回环地址。

```powershell
ssh -N -T -o BatchMode=yes -o ForwardAgent=no -o ExitOnForwardFailure=yes -L 127.0.0.1:17860:127.0.0.1:7860 foxmotion-pod
```

### 故障与回滚

- 卷未挂载或 checksum 不符时中止恢复，核对卷和配套 ID。不要向空挂载点启动写入，也不要覆盖旧快照。普通历史恢复要求空的本地数据目录，持久成片仍取自原卷。
- `Base image Python / GPU libraries differ` 或导入失败时，按 manifest.image 选择同一 Ubuntu、Python 及系统 PyTorch/CUDA 栈；检查同一绝对前缀、兼容 GPU、FFmpeg/libx264 及磁盘空间。两个 venv 会复用基础镜像的系统包。
- `checkout differs` 时按 manifest.project 与 manifest.comfy 核对两个检出。已有其他工作或成片的路径留作原状，在新的兼容容器盘/干净路径恢复。
- 先检查 supervisor、工作流校验、preflight、cgroup memory.events 以及 GPU 面板；本地诊断日志可能含匿名路径，勿放入备份或公开报告。显存热状态不会跨进程、断电或换机保存。
- 需要回滚到上一版时，在新容器盘选择 Phase 3 套件：运行包 `20261125T153126Z-d3446a13`，配置目录 `/workspace/config/runpod/phase3/20261125T152711Z-fc7357cb`，FoxMotion `f0ac389`。先核实该包的 manifest 与 COMPLETE.sha256，使用其配套配置和前端，并选与该版数据库兼容的普通快照。旧版没有本版保密上传能力。[旧版配套说明](RUNPOD_PHASE3.md)
