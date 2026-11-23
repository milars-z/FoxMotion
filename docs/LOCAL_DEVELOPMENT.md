# Windows 本地开发

在项目根目录运行。推荐 Python 3.12 和 Node 22。FFmpeg 须包含 H.264 编码与 ffprobe。可以先检查：

```powershell
py -0p
node --version
npm.cmd --version
ffmpeg -hide_banner -encoders | Select-String libx264
ffprobe -version
```

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev_windows.ps1
```

如 3.12 来自其他发行版，可显式选择解释器，例如 `-Python C:\Python312\python.exe`。已建好的 .venv 优先复用。第一次启动安装依赖时需要互联网；日后使用以下命令离线启动。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev_windows.ps1 -SkipInstall
```

访问 http://127.0.0.1:5173 。Vite 把同源 /api 转发到 Studio。另一个 PowerShell 可检查：

```powershell
Invoke-RestMethod http://127.0.0.1:5173/api/health
Invoke-RestMethod http://127.0.0.1:7860/api/system
Invoke-RestMethod http://127.0.0.1:7860/api/jobs
```

分别调试时，在三个终端里执行以下三行，每个终端都从项目根目录开始：

```powershell
.\.venv\Scripts\python.exe -m backend
.\.venv\Scripts\python.exe -m backend.worker
npm.cmd --prefix frontend run dev
```

使用自定义 STUDIO_PORT 时，独立 Vite 终端也需要设置同名环境变量。一键脚本会读取 .env 并传给 Vite。静态网页测试可执行 `npm.cmd --prefix frontend run build`，随后重启 API 并访问 http://127.0.0.1:7860。

## 验收与故障复现

1. 提交两个文生任务。选中第二个任务可看到排队状态；取消它，第一项继续执行。把 .env 中 MOCK_STAGE_SECONDS 调到 3 并重启，可以留出更多操作时间。
2. 播放并下载完成的视频；刷新网页，在任务历史中选择它。点击「重新生成」会沿用实际种子并复制素材。
3. 在图生模式选择 PNG / JPEG / WebP 首帧，按需提供尾帧。在参考模式上传多张图片、MP4 / WebM 或 WAV / MP3。通过缩略图旁的移除按钮调整素材。文件数量以上方能力与控件提示为准。
4. 用精确提示词 `[mock:fail]` 提交，查看失败原因。终态任务支持确认删除。Mock 的提示词、步数、种子和参考素材用于链路测试，输出内容是校准图。
5. 执行下列自动测试。端到端测试自行启动临时的 17860 静态服务和 15173 Vite 代理，分别验证生产与开发入口。测试数据与工作台隔离。Windows 使用已安装的 Microsoft Edge；Linux 可先在 frontend 目录运行 `npx playwright install --with-deps chromium`。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run test:e2e
```

遇到引擎离线，检查 /api/system 的 worker.online 和 worker.backend。端口被占用可用 `Get-NetTCPConnection -LocalPort 5173,7860 -State Listen` 查看占用者。开发启动日志位于 DATA_DIR 下的 logs。要看实时 Worker 日志，可单独以前台方式运行 Worker。停止一键脚本后再分别启动。
