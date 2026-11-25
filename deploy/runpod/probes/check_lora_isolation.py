import sys,os
sys.argv=[sys.argv[0],'--cpu']
sys.path.insert(0,'/opt/ComfyUI')
import comfy.options
comfy.options.enable_args_parsing()
import torch
from comfy.model_patcher import ModelPatcher
from comfy.lora import load_lora
cpu=torch.device('cpu')
module=torch.nn.Sequential(torch.nn.Linear(2,2,bias=False))
with torch.no_grad():module[0].weight.zero_()
base=ModelPatcher(module,load_device=cpu,offload_device=cpu)
for scale in (1.,2.):
 state={'layer.lora_up.weight':torch.tensor([[scale],[0.]]),'layer.lora_down.weight':torch.tensor([[1.,0.]])}
 patches=load_lora(state,{'layer':'0.weight'})
 copy=base.clone()
 assert copy.add_patches(patches,1.) and not base.patches
 copy.patch_model(device_to=cpu,force_patch_weights=True)
 assert torch.equal(module[0].weight.detach(),torch.tensor([[scale,0.],[0.,0.]]))
 copy.unpatch_model(device_to=cpu)
 assert torch.count_nonzero(module[0].weight)==0
print('Native LoRA adapters A -> base -> B -> base: exact CPU tensor equality passed')
