# 连接本机 ComfyUI

在项目根目录的 .env 中配置下面的路径，全部指向同一台机器。ComfyUI 服务和 Studio 应使用能够读写对应任务目录的同一专用账户。

```dotenv
INFERENCE_BACKEND=comfyui
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_HOME=/opt/ComfyUI
COMFYUI_INPUT_DIR=/opt/ComfyUI/input
COMFYUI_OUTPUT_DIR=/opt/ComfyUI/output
COMFYUI_MODEL_DIR=/opt/ComfyUI/models
WORKFLOW_DIR=./workflows
```

## 检查模板与协议

```bash
curl -fsS http://127.0.0.1:8188/queue
curl -fsS http://127.0.0.1:8188/object_info > /tmp/h3-object-info.json
.venv/bin/python -m backend.preflight
curl -fsS http://127.0.0.1:7860/api/capabilities | python3 -m json.tool
```

每种模式由 bindings/text.json、image.json 或 reference.json 独立启用。适配器检查节点 class_type、绑定字段、权重文件、模型选择器枚举、基础模型家族和输出目录。请先按导出手册建立映射。每种可提交的输入数量组合都对应一个已导出的图，模型预设是该配置声明的固定基线。

运行时会等 /queue 空闲，向 /upload/image 上传 UUID 图片。视频和音频复制到已确认的 input/h3studio/任务UUID 下，将相对路径传给对应的本地加载节点。输出前缀固定为 output 下的该任务专属子目录。/prompt 接收含 prompt 与 client_id 的对象。Worker 持久化 prompt_id，通过同一 clientId 的 /ws 获取事件，通过 /history/{prompt_id} 确认最终结果。断开 WebSocket 后继续轮询 HTTP。仅显示阶段与耗时。

输出绑定指定历史记录中唯一视频数组。仅接收 type=output、专属目录下的 MP4 或 WebM，复制后用 ffprobe 检查，再交给带 Range 支持的 Studio 接口。存储两份输出所需空间应计入 EBS 预算。

## 取消、故障与重试

队列中的 Studio 任务立即取消。已提交的 ComfyUI 排队任务按 prompt_id 删除。本机 ComfyUI 支持按任务取消时会请求该接口；较旧版本会等执行结束后丢弃结果。Worker 确认资源释放后处理下一项。提交后连接丢失或等待超时会记录 interrupted，避免不确定状态下重复提交。通过 `curl -fsS http://127.0.0.1:8188/queue` 核对队列，再在工作台点「重新生成」。未知执行的文件清理会等待远端队列安全。

CPU 协议测试通过以下命令单独运行。测试专用节点仅存在于 pytest 临时目录。正式项目的 workflows 目录没有可执行的假模板。

```bash
.venv/bin/python -m pytest -q tests/test_comfyui.py
```

官方接口和 H3 家族说明见 [服务端路由](https://docs.comfy.org/development/comfyui-server/comms_routes) 与 [原生 H3 模板](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native)。批次大小、量化、权重精度、分辨率与时长都需要在目标卡上实测。
