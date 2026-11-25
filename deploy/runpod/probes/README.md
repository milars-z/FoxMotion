# RunPod 验证探针

这四个脚本来自 Phase 3 和 3.1 的隔离验证。合成素材在临时目录内生成，运行时无需私人素材。基座切换探针模拟模型列表，LoRA 探针检查小型 CPU 权重的精确相等性。保密探针使用固定版本的原生 classic 执行器、合成图像、测试载入节点和真实张量引用。它验证 8 个 H3 图及各自的维护图。图生成器只读当前回环服务的节点清单。

在项目根目录执行以下 CPU 检查。它们不向生产队列提交 prompt。

```bash
cd /opt/projects/FoxMotion
source deploy/runpod/common.sh
export PYTHONPATH="$project" CUDA_VISIBLE_DEVICES=""
out=$(mktemp -d "$RUNTIME_DIR/tmp/fixture-graphs-XXXXXX")
"$studio" deploy/runpod/probes/render_fixture_graphs.py "$out"
"$comfy" deploy/runpod/probes/check_private_cache.py "$out"
"$comfy" deploy/runpod/probes/check_lora_isolation.py
"$comfy" deploy/runpod/probes/check_base_switch.py
```

Phase 2 的性能采集器和阶段计时器仍位于 `deploy/runpod/phase2/`。历史配对运行脚本、下载及发布驱动、迁移编辑器、一次性调度器和浏览器安装目录保留在当时的实验目录；其具体功能由受版本管理的部署入口、自动化测试和实测报告覆盖。生产启动不会安装或执行这些实验驱动。
