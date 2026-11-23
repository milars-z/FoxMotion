# 数据流与 API 契约

浏览器 → 同源 FastAPI → SQLite 队列 → 独立 Python Worker → Mock 或本机 ComfyUI → EBS 输出文件。开发时 Vite 代理 /api，生产由 FastAPI 挂载 frontend/dist。以项目根目录为工作目录。

|方法与路径|用途|
|---|---|
|GET /api/health|进程存活|
|GET /api/system|后端、Worker 心跳、运行环境和磁盘|
|GET /api/capabilities|各模式 family、ready、reason、options 和上传限制|
|GET /api/models、GET /api/loras|注册的 checkpoint 与 LoRA、权重文件存在性、推荐设置和逐模式验证状态|
|POST /api/jobs|multipart 的 payload 为 JSON 字符串，files 按 roles 顺序重复，返回 201|
|GET /api/jobs|历史倒序，通过 limit、offset 翻页|
|GET /api/jobs/{id}|任务状态、参数、输入元数据和阶段|
|POST /api/jobs/{id}/cancel|排队立即取消，执行中协作取消|
|POST /api/jobs/{id}/regenerate|复制独占输入，使用原参数产生新任务|
|DELETE /api/jobs/{id}|删除终态任务及其私有文件，返回 204|
|GET /api/jobs/{id}/result|在线播放，Range 支持，download=true 附件下载|

parameters_json 继续保存提交时的参数快照。可选字段为 model_profile_id、lora_id、lora_strength、sampler、scheduler、shift、turbo_enabled。API 入队、Worker 执行和重新生成均校验选择；强度为 0 时解析到独立基座图。[参数与请求示例](README_H3_LORA_INTEGRATION.md)

任务的 SQLite 字段见 backend/services/schema.sql。创建时间和 SQLite rowid 决定 FIFO 顺序。Worker 独占文件锁、即时写事务和运行中唯一索引共同保护领取。服务重启时保留 queued；Worker 获锁后将遗留 running 标为 interrupted。切换后端后，旧后端排队任务会明确失败。重新生成保留实际种子。

启动后执行：

```powershell
Invoke-RestMethod http://127.0.0.1:7860/api/capabilities
Invoke-RestMethod http://127.0.0.1:7860/api/jobs
Invoke-RestMethod http://127.0.0.1:7860/openapi.json
```

从 Bash 直接调用 Mock 接口可用以下示例。返回的 job_id 可用于详情、取消和下载。请求中 seed 为 -1 时，服务在入队前确定实际种子并返回。

```bash
curl -fsS -F 'payload={"mode":"text","prompt":"缓慢向前的镜头","parameters":{"duration":3,"resolution":"640x360","steps":8,"seed":-1,"preset":"Mock Standard"},"roles":[]}' http://127.0.0.1:7860/api/jobs
```
