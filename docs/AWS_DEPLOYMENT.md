# AWS EC2 私有部署

目标为 Ubuntu 24.04、Python 3.12、g6e.2xlarge。该规格有 1 张 NVIDIA L40S、48 GB 显存、64 GiB 内存及本地实例存储。创建实例时为系统和私有数据选择加密 EBS，按项目规划配置 300 GiB 并核对剩余量。权重、ComfyUI input/output、Studio DATA_DIR 均应位于持久卷。[AWS G6e 规格](https://aws.amazon.com/ec2/instance-types/g6e/)

## 1. 准备系统与卷

安全组只允许从本人 IP 访问 SSH。使用已配兼容 NVIDIA 驱动的镜像，或由管理员确认后安装驱动。先核对 Nvidia 驱动、实际卷 ID、加密与挂载。下面以 /srv/h3studio 为已准备好的 EBS 上的目录。如采用单个加密 EBS 根卷，项目与数据也可位于该根卷。单独的数据卷应先使用 UUID 配置 /etc/fstab 并完成挂载。

```bash
nvidia-smi
lsblk -o NAME,MODEL,SERIAL,SIZE,FSTYPE,MOUNTPOINTS
findmnt -T /srv
df -h /srv
sudo install -d -m 0700 -o "$USER" -g "$(id -gn)" /srv/h3studio
```

按 lsblk 的 SERIAL 对照 EC2/EBS 控制台中的卷 ID、Size 与 Encrypted。若在已配置 AWS CLI 权限的管理员终端中，可运行 `aws ec2 describe-volumes --volume-ids 你的卷ID --query 'Volumes[].{Size:Size,Encrypted:Encrypted,State:State}'`。实例存储与 EBS 都可能表现为 NVMe 设备。[AWS EBS 设备识别](https://docs.aws.amazon.com/ebs/latest/userguide/identify-nvme-ebs-device.html)

## 2. 放置源码并安装 Studio

通过自己的私有代码仓库克隆到 /srv/h3studio/app，或从 Windows 将源码目录 frontend、backend、workflows、scripts 和 requirements*.txt、constraints.txt、.env.example 通过 SSH 复制过去。排除 node_modules、dist、.venv、.env 及 data。使用具有 sudo 权限的普通服务账户运行脚本。系统路径请使用字母、数字、下划线、斜线、点和短横线。

```bash
cd /srv/h3studio/app
bash scripts/install_ubuntu.sh
chmod 600 .env
nano .env
```

安装脚本处理 Python venv、FFmpeg 和 Studio 依赖。它会复用本机 Node 20+，缺失时从 Node.js 官方发行目录获取 Node 22、核对 SHA-256 后安装到项目私有工具目录。设置 `DATA_DIR=/srv/h3studio/data`。先保留 `INFERENCE_BACKEND=mock`，验证基础服务。

```bash
bash scripts/deploy_ubuntu.sh
bash scripts/check_environment.sh
systemctl --no-pager status h3-api h3-worker
curl -fsS http://127.0.0.1:7860/api/system
```

## 3. 准备本机 ComfyUI 与模型

部署注册的 non-pruned H3 + HMNSFW 时，按 [LoRA 实机验收手册](README_H3_LORA_INTEGRATION.md) 依次完成无 LoRA 基线、权重 header 检查、固定种子 A/B 和本机验收记录，再启用对应生成组合。

单独安装官方 ComfyUI 到持久目录，例如 /srv/h3studio/ComfyUI。以下操作由管理员在选择与 H3 兼容的版本后执行，并记录 git rev-parse HEAD 的结果。使用与驱动及 L40S 匹配的 CUDA 版 PyTorch，按官方安装选择器获取对应 pip 命令，在 ComfyUI 自己的 .venv 内执行。[ComfyUI 仓库](https://github.com/Comfy-Org/ComfyUI) · [PyTorch 安装选择器](https://pytorch.org/get-started/locally/)

```bash
cd /srv/h3studio
git clone https://github.com/Comfy-Org/ComfyUI.git ComfyUI
cd ComfyUI
git rev-parse HEAD
python3.12 -m venv .venv
source .venv/bin/activate
# 在此 venv 执行已选定的 CUDA 版 PyTorch 安装命令。
python -m pip install -r requirements.txt
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
mkdir -p input output
python main.py --listen 127.0.0.1 --port 8188 --disable-api-nodes
```

按 [工作流导出](WORKFLOW_EXPORT.md) 手动放置已确认的权重，导出实际 API 图并完成 bindings。分别验证两个家族。预留空间容纳编码器、VAE、模型和输入/输出副本。结束手动 ComfyUI 后在 Studio 的 .env 设置 `COMFYUI_HOME=/srv/h3studio/ComfyUI` 与 `INFERENCE_BACKEND=comfyui`，再运行部署脚本。它会生成独立的 h3-comfyui、h3-api、h3-worker 单元并在能力预检通过后启动 Studio。重复部署前让队列排空；保留 .env、模型和数据库。需要变更 systemd 设置时使用 drop-in。

```bash
cd /srv/h3studio/app
bash scripts/deploy_ubuntu.sh
bash scripts/check_environment.sh
curl -fsS http://127.0.0.1:7860/api/capabilities | python3 -m json.tool
ss -ltn | grep -E ':(7860|8188) '
journalctl -u h3-api -u h3-worker -u h3-comfyui -n 60 --no-pager
```

## 4. Windows 访问与运维

在 Windows PowerShell 中运行，替换主机及 SSH 私钥路径。关闭本地开发服务以释放 7860，或将第一个 7860 改为其他本地端口。

```powershell
ssh -i "$HOME\.ssh\h3.pem" -o ExitOnForwardFailure=yes -N -L 127.0.0.1:7860:127.0.0.1:7860 ubuntu@你的EC2主机
```

保持终端打开，在浏览器访问 http://localhost:7860。用最短已验证配置出片，再逐步测试更长时长、分辨率和参考组合；观察 `watch -n 1 nvidia-smi` 与 `df -h /srv/h3studio`。当前交付的本地验收使用 Windows Mock 与协议替身，真实 EC2 上的驱动、systemd、显存峰值及 GPU 生成均需在此步骤验证。

```bash
bash scripts/stop_services.sh
bash scripts/start_services.sh
systemctl --no-pager status h3-api h3-worker
```
