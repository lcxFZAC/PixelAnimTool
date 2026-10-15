"""Bounded subprocess entry point for the vendored MIT upstream API."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'vendor'))
from pixelfixer.api import process
source,target=map(Path,sys.argv[1:3])
step=int(sys.argv[3]) if len(sys.argv)>3 else 0
result=process(source.read_bytes(),mode='full',force_step=step or None)
target.write_bytes(result.pop('png'))
result.update(tool='Retro-Diffusion/pixel-art-fixer',mode='full',forced=bool(step),forceStep=step or None,commit='ef376e57e1c272633ca2dbf5f29ec3fcf6596465')
target.with_suffix('.json').write_text(json.dumps(result),encoding='utf-8')
