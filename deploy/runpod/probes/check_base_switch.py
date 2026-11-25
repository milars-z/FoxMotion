import sys,asyncio,importlib.util
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,'/opt/ComfyUI');sys.argv=['check','--cpu']
import comfy.options;comfy.options.enable_args_parsing()
import server
loop=asyncio.new_event_loop();server.PromptServer(loop)
spec=importlib.util.spec_from_file_location('foxmotion_guard_check','/opt/projects/FoxMotion/deploy/runpod/foxmotion_runtime/__init__.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def entry(kind,name):
 model=SimpleNamespace(model=type(kind,(),{})(),cached_patcher_init=(None,('/weights/'+name,)),loaded_size=lambda:1024)
 return SimpleNamespace(model=model,device='cuda:0')
for old,new,expected in [('fl2va','fl2va',False),('fl2va','ref2va',True),('ref2va','fl2va',True)]:
 base=entry('MiniMaxH3',old); clip=entry('MiniMaxH3TEModel_','clip');vae=entry('MiniMaxH3VideoVAE','vae')
 with patch.object(m.mm,'current_loaded_models',[base,clip,vae]), patch.object(m.mm,'free_memory') as free, patch.object(m.folder_paths,'get_full_path_or_raise',return_value='/weights/'+new), patch.object(m.nodes.UNETLoader,'load_unet',return_value=('native',)):
  assert m.FoxMotionUNETLoader().load_unet(new,'default')==('native',)
  assert bool(free.call_count)==expected
  if expected: assert free.call_args.kwargs['keep_loaded']==[clip,vae]
 print(old,'->',new,'selective release',expected)
