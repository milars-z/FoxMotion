# 从官方模板导出并绑定

1. 在本机 ComfyUI 的模板库中分别打开官方 MiniMax H3 文生、图生或参考生成模板。EC2 上操作模板可临时建立 `ssh -N -L 127.0.0.1:8188:127.0.0.1:8188 ubuntu@你的主机`，再通过本机 8188 访问编辑器。普通使用者只需 Studio 的 7860 隧道。[官方模板](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-native)
2. 检查每个加载器当前选中的权重及实际目录。文本与首/尾帧用 FL2VA，参考图片、视频、音频用 Ref2VA。按目标机器能力选择量化与依赖。默认配置不包含 LoRA。如模板保留 LoRA 加载器，应确认文件、来源与兼容性后显式列入 confirmed_loras，或在编辑器中移除该分支再导出。Sentinel7/h3 留待单独验证。
3. 先用自己的小型测试素材在 ComfyUI 出片，选定拟开放的参数范围。使用菜单中的 **Export (API)** / **Save (API Format)**；某些版本需先开启开发者选项。将 JSON 放入 workflows/text.api.json 等文件。编辑器的画布 JSON 含 nodes 与 links，API 格式的顶层键为节点 ID，节点项含 class_type 与 inputs。
4. 做一次手动执行，从本机 /history/实际prompt_id 查看保存节点的输出数组键、文件名和子目录。确认该保存节点能以相对 filename prefix 输出 MP4 或 WebM。对于拆分的宽高、时长帧数等控件，追踪到可写的源节点字面值。
5. 从项目根目录复制示例。示例中的节点 ID、字段、参数范围、时长到帧数映射以及输出键都要按导出结果替换。

```bash
cp -n workflows/bindings/text.example.json workflows/bindings/text.json
python3 - <<'PY'
import json
from pathlib import Path
g = json.loads(Path('workflows/text.api.json').read_text())
for node, item in g.items():
    print(node, item['class_type'], list(item['inputs']))
PY
```

## 填写 bindings

|配置项|填写方法|
|---|---|
|family、preset|与已运行的基础模型一致，preset 作为界面名称。|
|durations、resolutions、steps|仅列已验证的秒数、宽x高和步数。所有组合均须适用于该模式的所有变体。|
|fields|prompt、seed、width、height、duration、steps、output_prefix 分别绑定一个或多个源节点的 node 与 input。|
|values|需要换算时建立显式值表，例如把界面时长映射到实测的整数帧数。表的键使用规范数字字符串，不含单位。|
|models|为每个权重选择器填写 node、input、模型根目录下的 folder，唯一的扩散模型标 base=true。图内所有权重路径均须声明。|
|slots|角色名到有序目标数组，元素逐一绑定实际素材加载器的文件输入。|
|output_node、output_key|填实测保存节点 ID 和其 history 输出数组键。|

文生的 slots 为空。图生应先导出仅首帧的图，将 first_frame 绑定到实测加载器；首尾帧版本另作一个 variant，额外绑定 last_frame。参考模式可按不同的 ref_image、ref_video、ref_audio 数量建立变体。每个 variant 的 slots 精确描述输入数量。未连接或残留样例素材的分支请在导出前清理。节点中的本地媒体路径应使用该任务实际绑定的输入。

```bash
.venv/bin/python -m backend.preflight
curl -fsS http://127.0.0.1:7860/api/capabilities | python3 -m json.tool
```

就绪表示结构、节点目录与本地模型预检通过。在工作台对每个导出的组合再提交一次，核对输出时长、尺寸、音轨、首尾帧与参考内容。新增真实导出和绑定默认由 Git 忽略。
