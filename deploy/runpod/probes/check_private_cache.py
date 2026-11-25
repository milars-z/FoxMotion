"""Fixed revision, isolated input/output paths and CPU only. Never contacts the running server."""
import asyncio, gc, json, os, shutil, subprocess, sys, tempfile, wave, weakref
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

project=Path('/opt/projects/FoxMotion')
comfy=Path('/opt/ComfyUI')
runtime=Path('/opt/video-runtime')
graphs_dir = Path(sys.argv[1]).resolve()
os.chdir(comfy)
sys.path[:0]=[str(comfy),str(project)]
sys.path.append(str(runtime/'studio/lib/python3.12/site-packages'))
scratch=tempfile.TemporaryDirectory(dir=runtime/'tmp')
base=Path(scratch.name)
inp=base/'input'
from PIL import Image
fixtures = inp/'phase3-synthetic'
fixtures.mkdir(parents=True)
for i, color in [(1, 'orange'), (2, 'blue')]:
    Image.new('RGB', (32,32), color).save(fixtures/f'image{i}.png')
    with wave.open(str(fixtures/f'audio{i}.wav'), 'wb') as f:
        f.setparams((1,2,8000,0,'NONE',''))
        f.writeframes(bytes(16000))
    subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i',f'color={color}:s=32x32:d=0.25',
                    '-threads','1','-c:v','libx264',str(fixtures/f'video{i}.mp4')],check=True)
(base/'custom_nodes').mkdir()
(base/'custom_nodes/foxmotion_runtime').symlink_to(project/'deploy/runpod/foxmotion_runtime')
sys.argv=[str(comfy/'main.py'),'--cpu','--cache-classic','--base-directory',str(base),'--input-directory',str(inp),
          '--extra-model-paths-config',str(runtime/'extra-models.yaml'),'--disable-api-nodes','--disable-all-custom-nodes',
          '--whitelist-custom-nodes','foxmotion_runtime']
import main,server,nodes,execution
from backend.inference import private_comfy
loop=asyncio.new_event_loop()
asyncio.set_event_loop(loop)
srv=server.PromptServer(loop)
class MockWeight:
    calls=0
    @classmethod
    def INPUT_TYPES(cls): return {"required":{"name":("STRING",)}}
    RETURN_TYPES=("STRING",)
    FUNCTION="load"
    def load(self,name):
        type(self).calls+=1
        return (name,)

async def verify():
    await nodes.init_extra_nodes(init_custom_nodes=True,init_api_nodes=False)
    field=SimpleNamespace(node="140:131",input="prompt")
    variant=SimpleNamespace(fields={"prompt":[field]})
    marker="SYNTHETIC_CANARY_83f9"
    folder=inp/'foxmotion-private'/str(uuid4())
    folder.mkdir(parents=True)
    for fixture in (inp/"phase3-synthetic").iterdir():
        if fixture.is_file(): shutil.copyfile(fixture,folder/fixture.name)
    graphs=sorted(graphs_dir.glob('*.json'))
    assert len(graphs)==8
    for file in graphs:
        graph=json.loads(file.read_text())
        sub="foxmotion-private/"+folder.name+"/"
        for n in graph.values():
            if n["class_type"]=="SaveVideo":
                n["inputs"]["filename_prefix"]=sub+str(uuid4())
            field_name={"LoadAudio":"audio","LoadVideo":"file","LoadImage":"image"}.get(n["class_type"])
            if field_name:
                n["inputs"][field_name]=sub+Path(n["inputs"][field_name]).name
        private_comfy.protect_graph(graph,variant,folder,marker)
        assert marker not in json.dumps(graph)
        pruned=private_comfy.maintenance_graph(graph)
        for which,g in (("private",graph),("maintenance",pruned)):
            valid,error,outputs,reasons=await execution.validate_prompt(str(uuid4()),g,None)
            assert valid,(file.name,which,error,reasons)
    print("validated",len(graphs),"H3 graphs and",len(graphs),"model-preserving maintenance graphs")
    # Register a tiny stand-in solely inside this test process. Exercise Comfy's real
    # executor, native caption node, cache keying and clean_unused twice.
    nodes.NODE_CLASS_MAPPINGS["VAELoader"]=MockWeight
    executor=execution.PromptExecutor(srv,execution.CacheType.CLASSIC,{"ram":0,"ram_inactive":0})
    probe={"warm":{"class_type":"VAELoader","inputs":{"name":"weight-fixture"}},
           "model_out":{"class_type":"PreviewAny","inputs":{"source":["warm",0]}},
           "caption":graph["private_caption"],
           "read":{"class_type":"PreviewAny","inputs":{"source":["caption",1]}},
           "reference":{"class_type":"LoadImage","inputs":{"image":"phase3-synthetic/image1.png"}},
           "reference_out":{"class_type":"PreviewAny","inputs":{"source":["reference",0]}}}
    async def execute(g,outputs):
        await executor.execute_async(g,str(uuid4()),{},outputs)
        assert executor.success and not any(t=="execution_error" for t,_ in executor.status_messages)
    await execute(probe,["model_out","read","reference_out"])
    assert marker in str(executor.history_result)
    cached=await executor.caches.outputs.get("caption")
    tensor=weakref.ref(cached.outputs[0][0])
    reference=weakref.ref((await executor.caches.outputs.get("reference")).outputs[0][0])
    del cached
    weight=await executor.caches.outputs.get("warm")
    scrub=private_comfy.maintenance_graph(probe)
    await execute(scrub,["private_done"])
    await asyncio.sleep(0)
    gc.collect()
    assert (await executor.caches.outputs.get("warm")) is weight and MockWeight.calls==1
    assert "caption" not in executor.caches.outputs.all_node_ids() and tensor() is None and reference() is None
    assert marker not in str(executor.history_result)+str(executor.status_messages)
    print("native classic dropped caption, decoded reference image tensor and caption tensor, retained identical model-cache entry; zero sampling")
try:
    loop.run_until_complete(verify())
finally:
    scratch.cleanup()
