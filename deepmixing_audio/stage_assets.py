"""Only publish the files needed by the approved character and live stage."""
from .runtime import ROOT

_GROUPS={
    'lingke-live-v1': ('index.html','stage.mjs','audio-drive.mjs','style.css','refinement.css',
        'performance.css','embedded.css','performance.mjs','performance-rig.mjs','performance-scene.mjs','outfit-rig.mjs'),
    'lingke-motion-v3': ('vendor/three.module.js','vendor/three.core.js','motion.mjs','hand-rig.mjs',
        'shape-control.mjs','model.json','model.bin','mineral-grain.png','reference-character.png','reference.jpg'),
    'lingke-motion-v4': ('personality.mjs','headphones.mjs'),
}
STAGE_ASSETS={f'/stage/{group}/{name}':ROOT/'docs/visual-concepts'/group/name
              for group,names in _GROUPS.items() for name in names}
MIME={'.html':'text/html; charset=utf-8','.mjs':'text/javascript; charset=utf-8',
      '.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8',
      '.json':'application/json; charset=utf-8','.png':'image/png','.jpg':'image/jpeg'}
