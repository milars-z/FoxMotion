# H3 Video Studio

个人视频生成工作台。React 中文界面通过本机 FastAPI 提交任务，独立 Worker 串行执行，SQLite 与媒体文件共同保存在 DATA_DIR。Windows 可直接使用 CPU Mock 模式，从提交一直测试到 MP4 播放、下载和删除。

## Windows 快速启动

在项目根目录打开 PowerShell。需要 Python 3.12、Node.js 20 或更新版本、带 ffprobe 和 libx264 的 FFmpeg。本项目本地验收使用 Node 22。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev_windows.ps1
```

打开 http://127.0.0.1:5173，选择「文生视频」，输入场景并点击「生成视频」。Mock 会根据所选时长与分辨率编码动态校准图。待机监看区的等高线画面是本地绘制的测试信号。第一次启动会安装项目依赖并从 .env.example 创建 .env；已有配置会保留。已安装依赖时可加 `-SkipInstall`。Ctrl+C 停止该次启动的进程。

## 运行验收

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:e2e
```

端到端测试使用独立临时数据目录和本机 Edge。生产静态页面由 FastAPI 在 7860 提供。更多步骤见 [本地开发](docs/LOCAL_DEVELOPMENT.md)。

## 工程目录

```text
frontend/             React + TypeScript、Vite、Vitest、Playwright
backend/api/          FastAPI、上传、任务生命周期、Range 视频响应
backend/models/       参数、输入角色、能力与任务契约
backend/services/     SQLite schema、事务、媒体校验、文件隔离
backend/inference/    公共接口、Mock、ComfyUI、工作流适配器
backend/worker.py     单实例 Worker、重启恢复、心跳
workflows/bindings/   可复制的绑定示例；本地私有配置由 Git 忽略
scripts/              Windows 开发与 Ubuntu/systemd 部署
tests/                API、队列、恢复和 ComfyUI 协议测试
docs/                 操作手册
data/                 自动创建的 inputs、outputs、temp、studio.db
```

## 接入真实模型

Windows Mock 已支持选择 non-pruned FL2VA profile 与 HMNSFW Extreme Realism、调节强度、手动应用采样建议并查看历史参数。真实 H3 + LoRA 待 L40S 验证。注册接口、只读权重检查、独立基座/LoRA 工作流绑定和验收命令见 [H3 + LoRA 操作手册](docs/README_H3_LORA_INTEGRATION.md)。

缺少真实 API 工作流时，ComfyUI 模式返回 `NOT_CONFIGURED`。仓库内的 bindings 示例不能直接提交给 ComfyUI。请在目标机器手工验证模型文件与官方模板，按 [工作流导出](docs/WORKFLOW_EXPORT.md) 和 [ComfyUI 对接](docs/COMFYUI_INTEGRATION.md) 配置。FL2VA 与 Ref2VA 分别管理。GPU 显存占用、生成质量、速度及 L40S 上的真实工作流仍待实测。

Ubuntu 安装、加密 EBS 核对、systemd 和 Windows SSH 隧道见 [AWS 部署](docs/AWS_DEPLOYMENT.md)。接口与队列见 [架构](docs/ARCHITECTURE.md)，访问边界与备份见 [安全说明](docs/SECURITY.md)。
