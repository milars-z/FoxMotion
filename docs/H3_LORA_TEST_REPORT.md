# H3 + LoRA 增量验收记录

2026-11-24，Windows、Python 3.12.14、Node.js 22、Edge。项目使用既有 SQLite、单 Worker 和 CPU Mock。测试素材为合成张量、校准视频及中性提示词。

| 实际执行 | 结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q` | 50 项通过。覆盖原有 API、队列、取消、文件管理，以及新参数快照、旧记录、白名单、0 强度旁路、错误绑定拒绝、哈希失效和 loader 报错撤销。|
| 在 frontend 执行 `npm.cmd run lint`、`npm.cmd run typecheck` | ESLint 与 TypeScript 均通过。|
| 在 frontend 执行 `npm.cmd test` | 5 项通过，包括手调参数保留、显式应用推荐值和切回不兼容 profile。|
| 在 frontend 执行 `npm.cmd run test:e2e` | TypeScript 与 Vite 生产构建通过，2 个 Edge 场景通过。覆盖生产静态页的上传、播放、下载和任务管理，以及 Vite 入口的 LoRA 参数提交、完成、刷新回显与 MP4 读取。|
| `.private\format\bin\ruff.exe check backend tests scripts/inspect_lora.py`、`.venv\Scripts\python.exe -m pip check` | Python lint 和依赖一致性检查通过。Ruff 为本机已有开发工具。|
| `python scripts/inspect_lora.py <临时合成文件> --sha256` | 只读解析 101 字节样本、1 个张量、shape [1, 2]，返回哈希与 unverified。|
| Git Bash `bash -n scripts/check_environment.sh` | 语法检查通过。|
| `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev_windows.ps1 -SkipInstall` | 更新后的本机服务启动成功。通过 5173 代理实读 system、models、loras，Worker 在线，注册项 hmnsfw_extreme_realism 的真实状态为 missing_file。|

验收期间补齐了 safetensors 的 NumPy 运行依赖，修正了自动化测试中选择框的定位及 TypeScript 类型。ComfyUI 协议测试在独立临时目录使用测试节点、测试 history、测试验收记录。生产兼容性目录没有通过测试写入验收结论。真实权重的 header、loader 匹配情况、A/B 效果、显存占用、Ubuntu/systemd 运行与 GPU 出片均待目标机器验证。

## 修改文件清单

以下路径相对工程根目录。

| 部分 | 新增 | 修改 |
|---|---|---|
| API、模型与任务 | backend/models/registry.py、backend/services/catalog.py | backend/api/app.py、backend/models/jobs.py、backend/config.py、backend/worker.py、backend/preflight.py |
| 推理与验证 | backend/inference/evidence.py、backend/record_smoke.py | backend/inference/base.py、backend/inference/mock.py、backend/inference/workflow.py、backend/inference/comfyui.py |
| 配置与工具 | scripts/inspect_lora.py | .env.example、requirements.txt、constraints.txt、scripts/check_environment.sh |
| 前端 | frontend/src/ProfilePicker.tsx、frontend/eslint.config.js | frontend/src/Controls.tsx、frontend/src/Monitor.tsx、frontend/src/types.ts、frontend/src/style.css、frontend/package.json、frontend/package-lock.json |
| 自动化测试 | tests/test_lora.py | frontend/src/studio.test.tsx、frontend/e2e/studio.spec.ts |
| 文档 | docs/README_H3_LORA_INTEGRATION.md、docs/H3_LORA_TEST_REPORT.md | README.md、docs/ARCHITECTURE.md、docs/AWS_DEPLOYMENT.md |

原需求文档位于工程根目录。本次复用原有 parameters_json 列，无数据库迁移。旧记录的原始 JSON 在读取和重新生成后均通过保留性测试。AWS 准备、状态判定与回滚步骤见 [H3 + LoRA 操作手册](README_H3_LORA_INTEGRATION.md)。
