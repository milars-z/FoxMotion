import json,urllib.request
from pathlib import Path
from backend.config import Settings
from backend.models.jobs import Request,validate_request
from backend.inference.workflow import WorkflowAdapter
cfg=Settings()
obj=json.load(urllib.request.urlopen('http://127.0.0.1:8188/object_info'))
assert "FoxMotionUNETLoader" in obj
out=Path(__import__('sys').argv[1])
out.mkdir(parents=True, exist_ok=True)
for mode,profile in [('text','workflow_default'),('image','workflow_default'),('reference','workflow_default'),('reference','ref2va_turbo')]:
 a=WorkflowAdapter(cfg,mode,obj,profile)
 for count in ([1,2,3] if mode=='reference' else [0]):
  media={}
  if mode=='image':
   media={role:[f'phase3-synthetic/image{i}.png'] for i,role in enumerate(('first_frame','last_frame'),1)}
  if mode=='reference':
   for role,kind,ext,n in [('ref_image','image','png',9),('ref_video','video','mp4',3),('ref_audio','audio','wav',3)]:
    media[role]=[f'phase3-synthetic/{kind}{j%2+1}.{ext}' for j in range(n if count==3 else count)]
  o=a.cap.options;roles=[k for k,vs in media.items() for _ in vs]
  req=Request(mode=mode,prompt='<Picture 1> <Video 1> <Audio 1>' if mode=='reference' else 'Orange sphere rolls on a blue floor.',roles=roles,
     parameters=dict(duration=o.durations[0],resolution=o.resolutions[0],steps=o.steps[0],preset=o.presets[0],seed=12345,model_profile_id=profile))
  validate_request(req,a.cap); g,_=a.render(req,media,'phase3-check/example')
  (out/f'{mode}-{profile}-{count}.json').write_text(json.dumps(g))
  print(mode,profile,count,len(g))
  if mode=='reference':
   req.prompt='<Picture 100>'
   try: validate_request(req,a.cap)
   except ValueError: pass
   else: raise AssertionError('out-of-range tag accepted')
