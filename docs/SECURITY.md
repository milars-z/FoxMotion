# 私有运行与文件管理

生产 Studio 仅监听 127.0.0.1:7860。ComfyUI 仅监听 127.0.0.1:8188。安全组保留个人来源的 SSH 访问；浏览器使用本地端口转发。专用非 root 账户及 SSH 登录是访问边界，同一服务器上的其他本地账户仍可能访问回环端口。浏览器写操作校验 Origin，服务端限制 Host。

## 验证边界

```bash
ss -ltn | grep -E ':(7860|8188) '
curl -fsS http://127.0.0.1:7860/api/health
systemctl cat h3-api h3-worker
git check-ignore data/studio.db .env workflows/text.api.json example.safetensors example.mp4
git status --short --untracked-files=all
```

Studio 使用本地字体、CSS 和绘制的监看图。推理目标固定为回环 IP，HTTP 客户端关闭代理环境继承与自动跳转。生产 ComfyUI 禁用云推理节点，使用离线模型配置。工作流及节点扩展由管理员审阅，扩展会在 ComfyUI 的系统账户下执行代码。控制外部网络访问时可在实例/网络层限制服务出站。初次安装 npm/Python 包、手工下载模型与推理运行分开进行。

上传接受受限数量、文件体积、MIME、扩展名和可解析媒体内容。图片有像素数限制，音视频通过本地 ffprobe 校验。每个任务在独占 UUID 目录保存 UUID 文件名。重新生成会复制素材。删除只清理该任务的文件与专属 ComfyUI staging；数据库墓碑支持重启后重试清理。对已删除行且文件仍被 Windows 播放器占用的情况，关闭播放器后重启 API。应用日志使用任务编号、事件与错误类型；提示词、素材和模型保存在受保护的数据卷中。ComfyUI 自身日志与 history 另由本机管理员管理。

## 备份及恢复

在受控目录保存加密备份，备份前排空队列并停止服务以获得一致的数据库和文件。以下命令假设生产数据位于 /srv/h3studio/data，备份目标已经挂载且启用了加密。

```bash
cd /srv/h3studio/app
bash scripts/stop_services.sh
umask 077
tar -C /srv/h3studio -czf "/mnt/encrypted-backup/h3-data-$(date +%Y%m%d-%H%M%S).tgz" data
bash scripts/start_services.sh
```

恢复测试优先将归档解到新的私有目录，再把 DATA_DIR 指向新目录并启动。单独备份受限的 .env、私有工作流和模型清单。自定义 DATA_DIR 和模型目录建议放在仓库外。EBS 删除策略、备份和快照保留期应由管理员核对。
