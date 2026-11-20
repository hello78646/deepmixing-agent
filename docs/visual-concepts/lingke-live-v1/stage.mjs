import * as THREE from '../lingke-motion-v3/vendor/three.module.js';
import {TAU,SHAPES,seeded,ease,phaseAt,shapePoints,transitionPoints,actionState,beginAction,beginReturn,returnPoints} from '../lingke-motion-v3/motion.mjs';
import {createHandCloud,poseHand,HAND_COUNT,deviceMotion,returnDeviceMotion} from '../lingke-motion-v3/hand-rig.mjs';
import {automaticTrackPositions,applyShapeSelection} from '../lingke-motion-v3/shape-control.mjs';
import {deformFace,SATELLITES,satellitePose} from '../lingke-motion-v4/personality.mjs';
import {createHeadphones} from '../lingke-motion-v4/headphones.mjs';
import {createPerformanceScene} from './performance-scene.mjs';
import {createOutfitRig} from './outfit-rig.mjs';
import {PHRASES,PALETTES,createAmbientMotion,cameraVisitAt} from './performance.mjs';
import {createAudioDrive} from './audio-drive.mjs';

const $=id=>document.getElementById(id), host=$('viewport');
// A direct, local art-review link; normal and embedded workbenches keep their UI.
const eyeReview=window.parent===window&&new URLSearchParams(location.search).get('eye-preview')==='b';
let reviewExpression='smile',reviewGlasses=false,reviewStarted=null;
const settings={mode:'stage',paused:false,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,speed:1,brightness:.9,quality:'high',shape:'auto'};
const show={enabled:true,beat:0,bpm:124,intensity:1,autoExpression:true};
const audioDrive=createAudioDrive();
const ambientMotion=createAmbientMotion();
settings.quality='low';
let stageVisible=true,theatreMode=null,interactionBlocked=false,dialogue={phase:'idle',level:0};
let safeArea={dialogueOpen:false,modalOpen:false,keyboardOpen:false},visitRequest=0;
window.addEventListener('message',event=>{
  if(event.source!==window.parent||event.origin!==location.origin||event.data?.type!=='deepmixing-audio-frame')return;
  audioDrive.ingest(event.data,performance.now());
  if(typeof event.data.visible==='boolean'&&event.data.visible!==stageVisible){stageVisible=event.data.visible;last=0;syncStageFrame();}
  if(typeof event.data.reduced==='boolean')settings.reduced=event.data.reduced;
  interactionBlocked=event.data.interactionBlocked===true;
  const incoming=event.data.dialogue;
  if(incoming&&['idle','listening','transcribing','thinking','preparing','speaking'].includes(incoming.phase)){
    dialogue={phase:incoming.phase,level:Number.isFinite(incoming.level)?Math.max(0,Math.min(1,incoming.level)):0};
  }
  const safe=event.data.safeArea;
  if(safe){
    const next={dialogueOpen:safe.dialogueOpen===true,modalOpen:safe.modalOpen===true,keyboardOpen:safe.keyboardOpen===true};
    if(JSON.stringify(next)!==JSON.stringify(safeArea)){safeArea=next;resize(true);}
    if(Number.isSafeInteger(safe.visit)&&safe.visit>visitRequest){visitRequest=safe.visit;if(!settings.reduced)ambientMotion.visit();}
  }
  const nextMode=event.data.presentation==='theatre'?(event.data.workspaceMode==='pro'?'pro':'smart'):null;
  if(nextMode!==theatreMode){theatreMode=nextMode;document.body.dataset.workspaceMode=nextMode||'panel';resize();}
  if(['wide','character','face','console'].includes(event.data.view)&&event.data.view!==view)selectView(event.data.view);
  if(['neutral','hiphop','chinese','edm','rock','jazz','chill'].includes(event.data.outfit))outfitRig.setStyle(event.data.outfit);
});
$('reduced').checked=settings.reduced;
let renderer;
try{
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));
  renderer.setPixelRatio(1);
  renderer.setClearColor(0x060710,0);
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.12;
  host.appendChild(renderer.domElement);
}catch(error){$('load-status').textContent='图形加速不可用';$('fallback').hidden=false;throw error;}

const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(44,1,.1,150);
const modelRoot=new THREE.Group(),spaceRoot=new THREE.Group(),characterRoot=new THREE.Group();
const planetHome=new THREE.Vector3(0,4.5,-1.15);
characterRoot.position.copy(planetHome);characterRoot.rotation.x=-.10;
let boothComputer=null;
scene.add(modelRoot,spaceRoot,characterRoot);
const rng=seeded(58709),particleMaterials=[],tracks=[],bgGroups=[];
const white=new THREE.Color(1,1,1);
const faceParts=[],glassesRig=new THREE.Group();characterRoot.add(glassesRig);
const headphones=createHeadphones();characterRoot.add(headphones);
let expression='smile',expressionAmount=1;
const deviceRigs=new Map(),devicePivots={'stage.deckA':[-3.15,2.575,.65],'stage.deckB':[3.15,2.575,.65],'stage.crossfader':[-.12,2.427,-.64],'stage.eq':[.26,2.45,1.41]};
const particleVertex=`
attribute float aSize; attribute float aSeed; attribute vec3 aColor;
uniform float uTime; uniform float uPulse; uniform float uBreath; uniform float uLight; uniform float uOpacity; uniform vec3 uTint; uniform float uColorMix; uniform float uMotion; uniform vec2 uDrift;
varying vec3 vColor; varying float vAlpha;
void main(){
  vec3 p=position;
  float wave=sin(uTime*.65+aSeed*24.+p.y*5.);
  p*=1.+(wave*.012+uPulse*.085+uBreath*(.012+aSeed*.018))*uMotion;
  p+=vec3(sin(aSeed*83.+uTime*37.),cos(aSeed*61.+uTime*32.),sin(aSeed*49.-uTime*29.))*uPulse*.035*uMotion;
  float driftFade=1.;
  if(uDrift.x!=0.){
    // A small depth-dependent variation gives every layer the same heading.
    vec2 span=vec2(68.,38.);
    p.xy=mod(p.xy+span*.5+uDrift*uTime*(.72+aSeed*.56),span)-span*.5;
    vec2 edge=span*.5-abs(p.xy);
    driftFade=smoothstep(0.,2.5,min(edge.x,edge.y));
    p.y+=4.;
  }
  vec4 view=modelViewMatrix*vec4(p,1.);
  gl_Position=projectionMatrix*view;
  gl_PointSize=clamp(aSize*(320./-view.z),.7,7.);
  vColor=mix(aColor*uTint,uTint,uColorMix)*uLight;
  vAlpha=(.72+.28*sin(aSeed*40.+uTime*1.15))*(1.+uBreath*.28)*uOpacity*driftFade;
}`;
const particleFragment=`
varying vec3 vColor; varying float vAlpha;
void main(){
  float d=length(gl_PointCoord-.5)*2.;if(d>1.)discard;
  gl_FragColor=vec4(vColor,(exp(-d*d*5.)-.0067)*vAlpha);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

function points(count,sample,{parent=spaceRoot,light=1.35,name='Particle layer',random=rng}={}){
  const p=new Float32Array(count*3),c=new Float32Array(count*3),sizes=new Float32Array(count),seeds=new Float32Array(count);
  for(let i=0;i<count;i++){
    const v=sample(i);p.set(v.p,i*3);v.color.toArray(c,i*3);sizes[i]=v.size;seeds[i]=random();
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.BufferAttribute(p,3));
  geometry.setAttribute('aColor',new THREE.BufferAttribute(c,3));
  geometry.setAttribute('aSize',new THREE.BufferAttribute(sizes,1));
  geometry.setAttribute('aSeed',new THREE.BufferAttribute(seeds,1));
  const material=new THREE.ShaderMaterial({vertexShader:particleVertex,fragmentShader:particleFragment,
    uniforms:{uTime:{value:0},uPulse:{value:0},uBreath:{value:.08},uLight:{value:light},uOpacity:{value:1},uTint:{value:white.clone()},uColorMix:{value:0},uMotion:{value:1},uDrift:{value:new THREE.Vector2()}},
    transparent:true,depthWrite:false,depthTest:true,blending:THREE.AdditiveBlending});
  const object=new THREE.Points(geometry,material);object.name=name;object.frustumCulled=false;parent.add(object);
  particleMaterials.push(material);return object;
}
const colorBlend=(a,b,t)=>new THREE.Color().lerpColors(new THREE.Color(a),new THREE.Color(b),t);
const core=points(22000,()=>{
  const h=rng()*2-1,a=rng()*TAU,r=.95+rng()*.08,q=Math.sqrt(1-h*h);
  const c=colorBlend('#ff663c','#ffe3a2',(Math.sin(a*3+h*7)+1)/2*.78);
  if(h<-.35)c.lerp(new THREE.Color('#ed4cb5'),(-h-.35)*.5);
  return{p:[Math.cos(a)*q*r,h*r,Math.sin(a)*q*r],color:c,size:.037+rng()*.065};
},{name:'Reference warm particle sphere'});
const orbitSpecs=[
  {r:1.5,width:.22,color:'#fe718d',x:1.1,z:.24,n:2600},
  {r:1.92,width:.28,color:'#9c86ff',x:1.25,z:.84,n:3000},
  {r:2.34,width:.2,color:'#70d8f6',x:.92,z:1.68,n:2400},
  {r:2.76,width:.3,color:'#edb877',x:1.24,z:2.38,n:2600},
];
const orbits=orbitSpecs.map((spec,index)=>{
  const object=points(spec.n,()=>{
    const a=rng()*TAU,r=spec.r+(rng()-.5)*spec.width;
    return{p:[Math.cos(a)*r,(rng()-.5)*.025,Math.sin(a)*r],color:new THREE.Color(spec.color).multiplyScalar(.65+rng()*.65),size:.028+rng()*.065};
  },{name:'Reference orbit '+index});
  object.rotation.set(spec.x,0,spec.z);return{object,speed:.013+index*.008,count:spec.n};
});
const arms=points(6200,i=>{
  const u=rng(),a=u*4.8+i%3*TAU/3,r=1.15+u*3.4,j=(.08+u*.32)*(rng()-.5);
  return{p:[Math.cos(a)*(r+j),Math.sin(a)*(r+j)*.52,-.9+Math.sin(a*2)*.3+(rng()-.5)*.5],color:colorBlend('#ffbd7a','#81d9ec',u),size:.035+rng()**2*.11};
},{name:'Reference three dust arms'});
const halo=points(3200,i=>{
  const a=rng()*TAU,k=i%4,r=2.4+Math.sin(a*3+k)*.18+k*.065;
  return{p:[Math.cos(a)*r,Math.sin(a)*r*.74,Math.sin(a*2+k)*.45],color:new THREE.Color(k%2?'#ffe1aa':'#8ddbf4'),size:.04+rng()*.04};
},{name:'Reference interleaved halo'});halo.rotation.z=-.42;
const stars=points(5000,()=>({p:[(rng()-.5)*34,(rng()-.5)*24,-18-rng()*25],color:new THREE.Color(rng()>.8?'#ffc5a2':'#bbc8ee'),size:.025+rng()**5*.14}),{parent:scene,name:'Distant stars',light:1.2});
// A sparse second layer drifts across the dark wings, behind the existing stage.
const dustRandom=seeded(41705);
const wingDust=points(1250,()=>({p:[(dustRandom()-.5)*68,(dustRandom()-.5)*38,-23-dustRandom()*13],
  color:new THREE.Color(dustRandom()>.28?'#a8a0e4':'#a9c7ed'),size:.045+dustRandom()**2.8*.15}),
  {parent:scene,name:'Workbench / drifting wing dust',random:dustRandom});
wingDust.material.uniforms.uMotion.value=0;
wingDust.material.uniforms.uDrift.value.set(.42,.18);
wingDust.visible=false;

const outerWorldSpecs=[
  {x:.157,y:.28,depth:32,r:.63,tilt:.46,color:'#8274be',accent:'#d1b5ff',ring:true},
  {x:.872,y:.438,depth:33,r:.56,tilt:-.35,color:'#576ea4',accent:'#a3c9fa',ring:true,compact:[.82,.16]},
  {x:.068,y:.409,depth:39,r:.33,tilt:0,color:'#66578f',accent:'#bba3e3',ring:false},
];
const outerWorlds=outerWorldSpecs.map((spec,index)=>{
  const group=new THREE.Group(),random=seeded(7289+index*71);
  group.name='Workbench / distant world '+index;group.visible=false;scene.add(group);
  const shell=points(index===2?540:1250,()=>{
    const y=random()*2-1,a=random()*TAU,q=Math.sqrt(1-y*y),r=spec.r*(.93+random()*.07);
    const x=Math.cos(a)*q,z=Math.sin(a)*q;
    const light=Math.max(0,-x*.53+y*.63+z*.34);
    return {p:[x*r,y*r,z*r],color:colorBlend(spec.color,spec.accent,light*.85),size:.064+random()**2*.105};
  },{parent:group,name:'Distant planet shell',random});
  const orbit=spec.ring?points(370,()=>{
    const a=random()*TAU,r=spec.r*(1.48+random()*.34);
    return {p:[Math.cos(a)*r,Math.sin(a)*r,(random()-.5)*.025],
      color:colorBlend(spec.color,spec.accent,random()*.52),size:.054+random()*.048};
  },{parent:group,name:'Distant planet ring',random}):null;
  if(orbit)orbit.rotation.set(.98,.19,spec.tilt);
  return {group,shell,orbit,spec};
});
const outerWorldAnchor=new THREE.Vector3();
function updateOuterSpace(t,isReference){
  const wide=!isReference&&theatreMode==='smart'&&view==='wide';
  const compact=camera.aspect<1;
  wingDust.visible=wide;
  wingDust.material.uniforms.uLight.value=.84*settings.brightness;
  for(const {group,shell,orbit,spec} of outerWorlds){
    group.visible=wide&&$('satellites').checked&&(!compact||Boolean(spec.compact));
    if(!group.visible)continue;
    // Anchor in the visible wings but render at far depth, behind the DJ equipment.
    const [x,y]=compact?spec.compact:[spec.x,spec.y];
    outerWorldAnchor.set(x*2-1,1-y*2,0).applyMatrix4(camera.projectionMatrixInverse);
    group.position.copy(outerWorldAnchor.multiplyScalar(-spec.depth/outerWorldAnchor.z)
      .applyQuaternion(camera.quaternion).add(camera.position));
    group.quaternion.copy(camera.quaternion);
    group.scale.setScalar(compact?.63:1);
    shell.rotation.y=t*.045;
    shell.material.uniforms.uLight.value=(spec.ring?.92:.65)*settings.brightness;
    if(orbit)orbit.material.uniforms.uLight.value=.73*settings.brightness;
  }
}
const galaxySpecs=[[-3.4,1.3,-3.2,.75,.65,'#a5d5ff'],[3.5,1.7,-4.5,.95,-.5,'#ffc78d'],[-2.9,-1.7,-4,.55,-.8,'#c1a9ef'],[3,-1.3,-2.8,.48,.35,'#8fe4d8'],[.8,3.6,-7,.65,.9,'#f1cfad']];
const galaxies=galaxySpecs.map(([x,y,z,scale,tilt,color],index)=>{
  const group=new THREE.Group();group.position.set(x,y,z);group.scale.setScalar(scale);group.rotation.set(tilt,.25,index*.8);spaceRoot.add(group);
  return points(6500,i=>{
    const r=rng()**.7*1.5,a=i%4*TAU/4+r*3.5+(rng()-.5)*(.2+r*.5),isCore=i%5===0,radius=isCore?r*.2:r,angle=isCore?rng()*TAU:a;
    return{p:[Math.cos(angle)*radius,Math.sin(angle)*radius,(rng()-.5)*(.07+.18*Math.exp(-radius*4))],color:colorBlend('#fff0d1',color,Math.min(1,r)),size:.028+rng()**3*.085};
  },{parent:group,name:'Distant galaxy '+index});
});
// Unlike the reference cluster, these small worlds occupy their own world-space
// anchors, including near left and right wings. They do not orbit behind the IP.
const satellites=SATELLITES.map((spec,index)=>{
  const group=new THREE.Group();group.name='Color world '+index;scene.add(group);
  const random=seeded(60610+index*97),count=3600;
  const shell=points(count,i=>{
    const h=random()*2-1,a=random()*TAU,q=Math.sqrt(1-h*h);
    const r=spec.radius*(.94+random()*.06);
    return {p:[Math.cos(a)*q*r,h*r,Math.sin(a)*q*r],color:colorBlend(spec.color,spec.accent,random()**3*.3),size:.07+random()*.065};
  },{parent:group,name:'Colored particle planet '+index,light:1.5});
  const orbit=points(950,()=>{
    const a=random()*TAU,r=spec.radius*(1.47+(random()-.5)*.13);
    return {p:[Math.cos(a)*r,(random()-.5)*.025,Math.sin(a)*r],color:colorBlend(spec.color,spec.accent,random()*.4),size:.06+random()*.045};
  },{parent:group,name:'Colored satellite orbit '+index,light:1.4});
  orbit.rotation.set(.65,0,spec.tilt);
  shell.material.blending=THREE.NormalBlending;orbit.material.blending=THREE.NormalBlending;
  return {group,shell,orbit,index};
});
const comets=Array.from({length:4},(_,index)=>{
  const object=points(650,i=>{const t=i/650,width=.006+t*.055;return{p:[-t*1.8,t*t*.35+(rng()-.5)*width,(rng()-.5)*width],color:new THREE.Color(index%2?'#ffd5a0':'#a1e6ff').multiplyScalar((1-t)**1.5),size:i<5?.17:.025+(1-t)*.055};},{name:'Comet '+index});
  object.rotation.z=-.4-index*.25;return object;
});
// World-space dust ribbon: only the performer leaves a trail; the star field stays anchored.
const visitTrail=points(900,i=>({p:[0,0,0],color:colorBlend('#a486ff','#a4f4ff',i/900),size:.035+(i%17)/17*.045}),{parent:scene,name:'Close encounter / stardust wake',light:1.2});
const visitHistory=[],wakeAnchor=new THREE.Vector3();
function updateVisitTrail(amount){
  visitTrail.visible=amount>.005&&!settings.reduced;
  if(!visitTrail.visible){visitHistory.length=0;return;}
  wakeAnchor.set(-1.7,-.35,-.45).applyMatrix4(characterRoot.matrixWorld);
  visitHistory.unshift(wakeAnchor.clone());if(visitHistory.length>28)visitHistory.pop();
  const out=visitTrail.geometry.attributes.position;
  for(let i=0;i<out.count;i++){
    const fraction=i/out.count,p=visitHistory[Math.min(visitHistory.length-1,Math.floor(fraction*28))];
    const a=i*2.39996+age*1.6,width=.04+fraction*.22;
    out.setXYZ(i,p.x+Math.cos(a)*width,p.y+Math.sin(a)*width,p.z-fraction*.3);
  }
  out.needsUpdate=true;visitTrail.geometry.setDrawRange(0,settings.quality==='low'?480:out.count);
  visitTrail.material.uniforms.uOpacity.value=amount*.7;
  visitTrail.material.uniforms.uLight.value=1.35*settings.brightness;
}

// Tracks keep deterministic identities during scatter/gather; no random frame noise.
[core,...orbits.map(o=>o.object),arms,halo,...galaxies].forEach((object,index)=>{
  const attribute=object.geometry.getAttribute('position'),initial=attribute.array.slice();
  let radius=0;for(let i=0;i<initial.length;i+=3)radius=Math.max(radius,Math.hypot(initial[i],initial[i+1],initial[i+2]));
  radius=Math.min(radius,index===0?1.05:4.8);
  tracks.push({object,attribute,initial,targets:SHAPES.map((s,k)=>shapePoints(s,attribute.count,radius,7049+index*97+k)),cloud:shapePoints('scatter',attribute.count,radius,3107+index*101),index});
});

// IP ring is a separate three-dimensional particle band, not a torus mesh.
const ipRoot=new THREE.Group();ipRoot.position.set(0,4.5,-1.15);scene.add(ipRoot);
const ringRig=new THREE.Group();ringRig.rotation.set(.32,-.14,.34);ipRoot.add(ringRig);
const ringRandom=seeded(52104),ringSpecs=Array.from({length:6500},()=>({a:ringRandom()*TAU,r:2.34+(ringRandom()-.5)*.20,y:(ringRandom()-.5)*.065,s:ringRandom()}));
const ipRing=points(ringSpecs.length,i=>{
  const s=ringSpecs[i];return{p:[Math.cos(s.a)*s.r,s.y,Math.sin(s.a)*s.r],color:colorBlend('#d7a54b','#fff3ce',s.s),size:.037+s.s*.048};
},{parent:ringRig,name:'LINGKE / gold particle orbit',light:1.35});
const starEmblems=[];
for(const [position,size,tilt] of [[[-2.12,.65,.40],.27,-.18],[[1.96,-1.2,.45],.23,.18]]){
  const object=points(600,i=>{
    const u=i%3?(.85+ringRandom()*.15):Math.sqrt(ringRandom()),edge=Math.floor(ringRandom()*10),angle=Math.PI/2+edge*TAU/10,angle2=angle+TAU/10;
    const r1=edge%2?.46:1,r2=edge%2?1:.46;
    const t=ringRandom(),x=(Math.cos(angle)*r1*(1-t)+Math.cos(angle2)*r2*t)*u*size,y=(Math.sin(angle)*r1*(1-t)+Math.sin(angle2)*r2*t)*u*size;
    return{p:[x,y,(ringRandom()-.5)*.035],color:new THREE.Color('#ffe4a3'),size:.052};
  },{parent:ipRoot,name:'LINGKE / particle star emblem',light:1.6});object.position.set(...position);object.rotation.z=tilt;starEmblems.push(object);
}

const handCloud=createHandCloud(HAND_COUNT,94011),hands=[];
for(let side=0;side<2;side++){
  const object=points(HAND_COUNT,i=>({p:[0,0,0],color:colorBlend('#b2daff','#fff0d3',(i%29)/28),size:.048+(i%13)/13*.037}),{parent:scene,name:'Particle hand '+side,light:3});
  object.material.uniforms.uMotion.value=0;
  hands.push({object,side,orbit:new Float32Array(HAND_COUNT*3),posed:new Float32Array(HAND_COUNT*3)});
}
let action=null,lastActionType='scratch',age=0,last=0,frameCount=0,ready=false;
let pointer=null,yaw=-.2,pitch=.12,cameraDistance=1,view=eyeReview?'face':'wide',cameraTween=null;
const temp=new THREE.Vector3(),orbitEuler=new THREE.Euler(.32,-.14,.34);
const director=createPerformanceScene({characterRoot,ipRoot,ipRing,tracks,satellites,hands,handCloud,deviceRigs,devicePivots,particleMaterials,faceParts,glassesRig,expressionStatus:$('expression-status'),quality:()=>settings.quality});
const outfitRig=createOutfitRig(characterRoot);

function updateHands(t){
  if(action&&(action.cancel?t-action.cancel.start>=1.6:actionState(t-action.start).done)){action=null;syncActionControls();}
  const state=action?.cancel?{phase:'return',amount:0,travel:0,operate:0}:action?actionState(t-action.start):{phase:'orbit',amount:0,travel:0,operate:0};
  const movement=action?.cancel?returnDeviceMotion(action.cancel.device,(t-action.cancel.start)/1.6):action?deviceMotion(t-action.start):{jog:0,fader:0,eq:0};
  for(const [key,rig] of deviceRigs){
    rig.position.set(...devicePivots[key]);rig.rotation.y=0;
    if(!action)continue;
    if(key==='stage.deckA'&&action.type==='scratch'||key==='stage.deckB'&&action.type!=='scratch')rig.rotation.y=movement.jog;
    if(key==='stage.crossfader'&&action.type!=='eq')rig.position.x+=movement.fader;
    if(key==='stage.eq'&&action.type==='eq')rig.rotation.y=movement.eq;
  }
  for(const {object,side,orbit,posed} of hands){
    object.visible=settings.mode==='stage'&&Boolean(action);
    object.material.uniforms.uLight.value=3.1*settings.brightness;
    object.material.uniforms.uOpacity.value=action?.cancel?(action.cancel.opacity?.[side]??1)*(1-ease((t-action.cancel.start)/1.6)):.10+.90*Math.sqrt(state.amount);
    const a=object.geometry.attributes.position.array;
    if(action&&!action.cancel)poseHand(posed,handCloud,action.type,side,t-action.start);
    for(let i=0;i<a.length/3;i++){
      const k=i*3,s=ringSpecs[(i+(side?3200:0))%ringSpecs.length];
      const theta=s.a+t*.13;
      temp.set(Math.cos(theta)*s.r,s.y,Math.sin(theta)*s.r).applyEuler(orbitEuler).add(ipRoot.position);
      if(action?.cancel){temp.toArray(orbit,k);continue;}
      a[k]=temp.x+(posed[k]-temp.x)*state.amount;
      a[k+1]=temp.y+(posed[k+1]-temp.y)*state.amount;
      a[k+2]=temp.z+(posed[k+2]-temp.z)*state.amount;
    }
    if(action?.cancel)returnPoints(a,action.cancel.from[side],orbit,(t-action.cancel.start)/1.6);
    object.geometry.attributes.position.needsUpdate=true;
    object.geometry.setDrawRange(0,settings.quality==='low'?2200:HAND_COUNT);
  }
  const labels={orbit:'粒子环绕待机',gather:'星尘汇聚 · 掌体成形',approach:'手掌接近设备',operate:'DJ动作预演中',retreat:'手掌撤离设备',scatter:'粒子散回轨道',return:'从当前姿态散回轨道'};
  const label=labels[state.phase]||labels.orbit;
  if($('gesture').textContent!==label)$('gesture').textContent=label;
  if(action&&!action.cancel&&!settings.paused&&frameCount%6===0){
    const at=Math.min(7,t-action.start);$('action-frame').value=String(at);$('action-time').textContent=at.toFixed(1)+'秒';
  }
}

function updateSpace(t,dt){
  const phase=phaseAt(t);
  for(const track of tracks){
    if(settings.shape!=='auto'){
      const target=track.targets[SHAPES.indexOf(settings.shape)],alpha=track.switchFrom?1:1-Math.exp(-dt*2.8);
      for(let k=0;k<target.length;k++)track.attribute.array[k]+=(target[k]-track.attribute.array[k])*alpha;
      track.attribute.needsUpdate=true;
    }else{
      automaticTrackPositions(track,t,track.attribute.array);track.attribute.needsUpdate=true;
    }
    if(track.switchFrom){
      const blend=ease((t-track.switchAt)/1.4),out=track.attribute.array;
      for(let k=0;k<out.length;k++)out[k]=track.switchFrom[k]+(out[k]-track.switchFrom[k])*blend;
      if(blend===1)track.switchFrom=null;
    }
  }
  const isReference=settings.mode==='reference';
  // The maximum scatter radius also stays behind the opaque character.
  spaceRoot.position.set(0,isReference?.18:4.5,isReference?0:-18.4);
  const blend=dt>0?1-Math.exp(-dt*4.35):0;
  spaceRoot.rotation.x+=(pitch-spaceRoot.rotation.x)*blend;
  spaceRoot.rotation.y+=(yaw+t*.06-spaceRoot.rotation.y)*blend;
  spaceRoot.scale.setScalar(isReference?1:1.45);
  core.visible=isReference;core.rotation.y=t*.06;
  orbits.forEach(({object,speed,count})=>{object.rotation.y=t*speed;object.geometry.setDrawRange(0,settings.quality==='low'?Math.round(count*.45):count);});
  core.geometry.setDrawRange(0,settings.quality==='low'?6500:22000);
  arms.rotation.z=t*.018;halo.rotation.y=Math.sin(t*.09)*.15;
  arms.geometry.setDrawRange(0,settings.quality==='low'?2200:6200);halo.geometry.setDrawRange(0,settings.quality==='low'?1200:3200);
  galaxies.forEach((g,i)=>{g.rotation.z=t*(.018+i*.004);g.geometry.setDrawRange(0,settings.quality==='low'?1800:6500);});
  satellites.forEach(({group,shell,orbit,index})=>{
    group.visible=!isReference&&$('satellites').checked;
    group.position.set(...satellitePose(index,t));
    shell.rotation.set(t*.025,index*.8+t*(.05+index*.006),0);
    orbit.rotation.y=t*(.09+index*.013);
    const p=phaseAt(t+index*4.2),breath=p.stage==='hold'?0:p.stage==='scatter'?ease(p.progress):1-ease(p.progress);
    shell.scale.setScalar(1+breath*.13);
    shell.geometry.setDrawRange(0,settings.quality==='low'?1500:3600);
    orbit.geometry.setDrawRange(0,settings.quality==='low'?400:950);
  });
  comets.forEach((c,i)=>{
    const p=(t*.045+i*.27)%1;c.visible=!settings.reduced&&p<.72&&(settings.quality!=='low'||i<2);
    c.position.set(-6+p*17,3.3-p*7+i*.45,-2-i);
    c.material.uniforms.uLight.value=1.6*Math.min(1,p*15,Math.max(0,(.72-p)*15))*settings.brightness;
  });
  for(const m of particleMaterials){
    m.uniforms.uTime.value=t;m.uniforms.uBreath.value=settings.reduced?0:.08;
    if(!comets.some(c=>c.material===m))m.uniforms.uLight.value=(isReference?1.35:.48)*settings.brightness;
  }
  satellites.forEach(({shell,orbit})=>{
    shell.material.uniforms.uLight.value=1.65*settings.brightness;
    orbit.material.uniforms.uLight.value=1.5*settings.brightness;
  });
  updateOuterSpace(t,isReference);
  // The characteristic gold ring remains a ring even when other space layers morph.
  const positions=ipRing.geometry.attributes.position.array;
  for(let i=0;i<ringSpecs.length;i++){
    const s=ringSpecs[i],a=s.a+t*.13,r=s.r*(1+.009*Math.sin(t*.65+s.s*24));
    positions.set([Math.cos(a)*r,s.y,Math.sin(a)*r],i*3);
  }
  ipRing.geometry.attributes.position.needsUpdate=true;
  ipRing.material.uniforms.uLight.value=1.30*settings.brightness;
  starEmblems.forEach(star=>star.material.uniforms.uLight.value=1.8*settings.brightness);
  ipRing.geometry.setDrawRange(0,settings.quality==='low'?2800:ringSpecs.length);
  ipRoot.visible=!isReference;modelRoot.visible=!isReference;characterRoot.visible=!isReference;
  if(frameCount%12===0){
    const names={hold:'形态保持',scatter:'有序散开',gather:'重新聚集'};
    $('phase').textContent=`${(t%36).toFixed(1)} / 36秒 · ${settings.shape==='auto'?names[phase.stage]:'手选形态'}`;
  }
}

// Environment softboxes give the exported metal and glass consistent reflections.
const envScene=new THREE.Scene();envScene.background=new THREE.Color('#1a1627');
for(const [position,scale,color,intensity] of [
  [[-5,6,4],[5,4,.1],'#dce7ff',3],[[5,4,2],[3,5,.1],'#fff1d4',2],[[0,5,-5],[6,2,.1],'#9c73ee',2],
]){
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(...scale),new THREE.MeshBasicMaterial({color:new THREE.Color(color).multiplyScalar(intensity)}));
  mesh.position.set(...position);mesh.lookAt(0,2,0);envScene.add(mesh);
}
const pmrem=new THREE.PMREMGenerator(renderer),environment=pmrem.fromScene(envScene,.04,.1,60);
scene.environment=environment.texture;pmrem.dispose();
scene.add(new THREE.HemisphereLight('#d6c6ff','#3b2549',1.0));
for(const [pos,color,intensity] of [[[-5,9,7],'#dce8ff',3.2],[[5,7,4],'#ffe1b3',2.2],[[-3,8,-5],'#8e5cff',3.4],[[-2,1,7],'#e6caf3',.9]]){
  const light=new THREE.DirectionalLight(color,intensity);light.position.set(...pos);scene.add(light);
}

function placeBoothComputer(rig,aspect){
  // Keep the prop inside portrait framing without changing the stage camera.
  const compact=THREE.MathUtils.clamp((.98-aspect)/.26,0,1);
  // Give the desktop computer a stronger presence while keeping the right-side
  // silhouette clear of the planet. The display faces the planet directly.
  rig.position.set(2.58-.52*compact,2.39,-.68);
  rig.scale.setScalar(1.40-.20*compact);
  rig.rotation.y=Math.atan2(planetHome.x-rig.position.x,planetHome.z-rig.position.z);
}

// Space-gray all-in-one on the rear lip of the booth; clear of both decks and hands.
function addBoothComputer(){
  const rig=new THREE.Group();rig.name='DJ booth / space-gray desktop computer';
  placeBoothComputer(rig,camera.aspect);
  const grain=document.createElement('canvas');grain.width=128;grain.height=256;
  const brush=grain.getContext('2d'),noise=seeded(2841);
  const pixels=brush.createImageData(grain.width,grain.height);
  for(let y=0;y<grain.height;y++){
    const stroke=106+44*noise();
    for(let x=0;x<grain.width;x++){
      const i=(y*grain.width+x)*4,v=stroke+8*noise();
      pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=v;pixels.data[i+3]=255;
    }
  }
  brush.putImageData(pixels,0,0);
  const brushing=new THREE.CanvasTexture(grain);brushing.wrapS=brushing.wrapT=THREE.RepeatWrapping;
  brushing.anisotropy=Math.min(4,renderer.capabilities.getMaxAnisotropy());
  const metal=new THREE.MeshPhysicalMaterial({color:0x777f8c,metalness:.83,roughness:.37,
    clearcoat:.18,clearcoatRoughness:.34,bumpMap:brushing,bumpScale:.003,envMapIntensity:.86});
  const trim=new THREE.MeshStandardMaterial({color:0x505663,metalness:.81,roughness:.28,envMapIntensity:.76});
  const recess=new THREE.MeshStandardMaterial({color:0x151721,metalness:.24,roughness:.54});
  const bezel=new THREE.MeshPhysicalMaterial({color:0x0e121b,metalness:.22,roughness:.26,clearcoat:.44});
  const rounded=(parent,material,w,h,d,r,b,x,y,z)=>{
    const a=w/2-b,c=h/2-b,q=r-b;
    const shape=new THREE.Shape();
    shape.moveTo(-a+q,-c);shape.lineTo(a-q,-c);shape.quadraticCurveTo(a,-c,a,-c+q);
    shape.lineTo(a,c-q);shape.quadraticCurveTo(a,c,a-q,c);
    shape.lineTo(-a+q,c);shape.quadraticCurveTo(-a,c,-a,c-q);
    shape.lineTo(-a,-c+q);shape.quadraticCurveTo(-a,-c,-a+q,-c);
    shape.closePath();
    const geometry=new THREE.ExtrudeGeometry(shape,{depth:d-2*b,bevelSize:b,bevelThickness:b,bevelSegments:3,curveSegments:6});
    geometry.translate(0,0,b-d/2);geometry.computeVertexNormals();
    const mesh=new THREE.Mesh(geometry,material);
    mesh.position.set(x,y,z);parent.add(mesh);return mesh;
  };
  const foot=rounded(rig,metal,.65,.44,.043,.075,.013,0,.022,.035);foot.rotation.x=-Math.PI/2;
  const pad=rounded(rig,recess,.57,.38,.012,.06,.003,0,.007,.035);pad.rotation.x=-Math.PI/2;
  const stand=rounded(rig,metal,.224,.49,.071,.050,.017,0,.278,-.053);stand.rotation.x=-.17;
  const display=new THREE.Group();display.position.set(0,.79,.02);display.rotation.x=-.08;rig.add(display);
  rounded(display,metal,1.50,1.02,.104,.061,.023,0,0,0);
  rounded(display,trim,1.467,.987,.022,.049,.007,0,0,.038);
  rounded(display,bezel,1.429,.880,.026,.04,.008,0,.045,.049);
  rounded(display,metal,.286,.201,.092,.072,.016,0,-.264,-.073);
  // Inset I/O and small underside vents make the visible back read as hardware.
  for(const x of [.29,.373,.456]){
    rounded(display,trim,.069,.028,.008,.012,.003,x,-.301,-.052);
    rounded(display,recess,.049,.015,.005,.007,.002,x,-.301,-.057);
  }
  for(let n=0;n<13;n++){
    const vent=new THREE.Mesh(new THREE.PlaneGeometry(.040,.006),recess);
    vent.position.set((n-6)*.052,-.426,-.0528);vent.rotation.y=Math.PI;display.add(vent);
  }
  // A low-gloss, bitten-apple mark printed on the rear housing, not on the display.
  const badge=document.createElement('canvas');badge.width=badge.height=128;
  const ink=badge.getContext('2d');ink.scale(1.28,1.28);
  ink.fillStyle='#343641';
  ink.fill(new Path2D('M54 24C53 13 61 5 73 3C74 13 65 24 54 24Z M51 31C57 31 64 26 73 26C83 26 90 30 95 37C86 42 82 50 82 58C82 68 88 75 96 78C92 88 84 98 76 98C69 98 64 93 55 93C46 93 42 98 35 98C23 98 7 74 7 55C7 37 18 26 32 26C41 26 46 31 51 31Z'));
  const badgeMap=new THREE.CanvasTexture(badge);badgeMap.colorSpace=THREE.SRGBColorSpace;
  const apple=new THREE.Mesh(new THREE.PlaneGeometry(.35,.35),new THREE.MeshStandardMaterial({
    map:badgeMap,alphaTest:.35,metalness:.38,roughness:.33,envMapIntensity:.48}));
  apple.name='Apple logo / rear print';apple.rotation.y=Math.PI;apple.position.set(0,.021,-.0532);display.add(apple);
  const screen=document.createElement('canvas');screen.width=384;screen.height=224;
  const ctx=screen.getContext('2d');
  ctx.fillStyle='#101526';ctx.fillRect(0,0,384,224);
  ctx.fillStyle='#22293c';ctx.fillRect(0,0,384,34);ctx.fillRect(0,154,384,70);
  ctx.fillStyle='#dce5fd';ctx.font='600 17px sans-serif';ctx.fillText('LINGKE / MIX MONITOR',16,23);
  for(let track=0;track<2;track++){
    const mid=67+track*55;ctx.fillStyle=track?'#a98aff':'#61d9e8';
    for(let x=14;x<369;x+=4){
      const level=3+Math.abs(Math.sin(x*.068+track)*Math.cos(x*.23+track*.6))*18;
      ctx.fillRect(x,mid-level,2,2*level);
    }
  }
  ctx.fillStyle='#dbe5ff';ctx.fillRect(191,39,2,109);
  for(let n=0;n<9;n++){
    const x=20+n*40;ctx.fillStyle='#3f4862';ctx.fillRect(x,166,25,44);
    ctx.fillStyle=n%2?'#a98aff':'#61d9e8';const h=8+(n*23)%33;ctx.fillRect(x+9,204-h,7,h);
  }
  const map=new THREE.CanvasTexture(screen);map.colorSpace=THREE.SRGBColorSpace;
  const image=new THREE.Mesh(new THREE.PlaneGeometry(1.34,.775),new THREE.MeshPhysicalMaterial({map,
    emissiveMap:map,emissive:0xffffff,emissiveIntensity:.74,roughness:.30,clearcoat:.27}));
  image.position.set(0,.048,.063);display.add(image);
  rounded(display,metal,1.423,.104,.024,.030,.006,0,-.432,.050);
  const eye=new THREE.Mesh(new THREE.CircleGeometry(.010,12),trim);
  eye.position.set(0,.458,.0625);display.add(eye);
  modelRoot.add(rig);boothComputer=rig;
}

async function loadModel(){
  const grain=await new THREE.TextureLoader().loadAsync('../lingke-motion-v3/mineral-grain.png');
  grain.wrapS=grain.wrapT=THREE.RepeatWrapping;
  grain.anisotropy=Math.min(4,renderer.capabilities.getMaxAnisotropy());
  const [jsonResponse,binResponse]=await Promise.all([fetch('../lingke-motion-v3/model.json'),fetch('../lingke-motion-v3/model.bin')]);
  if(!jsonResponse.ok||!binResponse.ok)throw new Error('本地模型文件读取失败');
  const descriptor=await jsonResponse.json(),binary=await binResponse.arrayBuffer();
  const mats=new Map();
  for(const [key,value] of Object.entries(descriptor.materials)){
    const m=new THREE.MeshPhysicalMaterial({color:new THREE.Color().setRGB(...value.color),emissive:new THREE.Color().setRGB(...value.emissive),metalness:value.metalness,roughness:value.roughness,vertexColors:true,side:THREE.DoubleSide});
    m.envMapIntensity=.7;
    if(key.startsWith('LINGKE')){m.color.setRGB(1,1,1);m.bumpMap=grain;m.bumpScale=.010;}
    if(key.startsWith('Glasses v3'))m.envMapIntensity=key.includes('reflection')?.10:.35;
    if(key.includes('charcoal glass')){m.specularIntensity=.30;m.envMapIntensity=.12;}
    mats.set(key,m);
  }
  for(const part of descriptor.meshes){
    const geometry=new THREE.BufferGeometry();
    for(const [source,name] of [['positions','position'],['normals','normal'],['colors','color']]){
      const attr=part[source];geometry.setAttribute(name,new THREE.BufferAttribute(new Float32Array(binary,attr.offset,attr.length),3));
    }
    if(part.uvs)geometry.setAttribute('uv',new THREE.BufferAttribute(new Float32Array(binary,part.uvs.offset,part.uvs.length),2));
    geometry.computeBoundingSphere();
    const mesh=new THREE.Mesh(geometry,mats.get(part.material));mesh.name=part.part+' / '+part.material;
    if(!part.part.startsWith('stage')){
      geometry.translate(0,-4.5,1.15);
      if(part.part==='glasses')glassesRig.add(mesh);else characterRoot.add(mesh);
      if(part.part==='face'){
        const kind=part.material.startsWith('Brow')?'brow':part.material.startsWith('Mouth')?'mouth':part.material.includes('porcelain')?'teeth':'tongue';
        faceParts.push({mesh,kind,source:geometry.attributes.position.array.slice(),normals:geometry.attributes.normal.array.slice()});
      }
    }
    else if(devicePivots[part.part]){
      const pivot=devicePivots[part.part];
      if(!deviceRigs.has(part.part)){const rig=new THREE.Group();rig.position.set(...pivot);modelRoot.add(rig);deviceRigs.set(part.part,rig);}
      geometry.translate(...pivot.map(v=>-v));deviceRigs.get(part.part).add(mesh);
    }else modelRoot.add(mesh);
  }
  addBoothComputer();
  ready=true;applyExpression();syncActionControls();$('load-status').textContent='星云待机就绪 · 播放后跟随真实音乐';
}

function applyExpression(){
  for(const {mesh,kind,source,normals} of faceParts){
    deformFace(mesh.geometry.attributes.position.array,source,kind,expression,expressionAmount);
    mesh.geometry.attributes.position.needsUpdate=true;
    if(expression==='smile'||expressionAmount===0){
      mesh.geometry.attributes.normal.array.set(normals);mesh.geometry.attributes.normal.needsUpdate=true;
    }else mesh.geometry.computeVertexNormals();
    const hideFeature=(['wow','melancholy'].includes(expression)&&(kind==='tongue'||kind==='teeth'))||(expression==='angry'&&kind==='tongue');
    const opacity=hideFeature?1-ease((expressionAmount-.15)/.85):1;
    if(mesh.material.transparent!==(opacity<1)){mesh.material.transparent=opacity<1;mesh.material.needsUpdate=true;}
    mesh.material.opacity=opacity;mesh.visible=opacity>0;
    if(kind==='tongue')mesh.material.color.setRGB(...(expression==='cheeky'?[
      .34+.27*expressionAmount,.035+.045*expressionAmount,.13+.08*expressionAmount
    ]:[.34,.035,.13]));
  }
  glassesRig.rotation.z=expression==='cheeky'?-.065*expressionAmount:0;
  glassesRig.position.y=expression==='angry'?-.07*expressionAmount:expression==='melancholy'?-.035*expressionAmount:0;
  document.querySelectorAll('[data-expression]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.expression===expression)));
  const names={smile:'招牌微笑',cheeky:'吐舌鬼脸',wow:'惊讶鬼脸',melancholy:'忧郁',angry:'愤怒'};
  $('expression-status').textContent=names[expression]+' · '+Math.round(expressionAmount*100)+'%';
}

function resize(animate=false){
  const from={position:camera.position.clone(),quaternion:camera.quaternion.clone(),fov:camera.fov};
  cameraTween=null;
  const w=innerWidth,h=innerHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();
  if(boothComputer)placeBoothComputer(boothComputer,camera.aspect);
  if(settings.mode==='reference'){camera.fov=44;camera.position.set(0,.15,7.6*cameraDistance);camera.lookAt(0,.15,0);}
  else{
    const scale=w<760?(theatreMode?(w<560?Math.max(1.75,1.12/camera.aspect):1.12):Math.max(1.18,.84/camera.aspect)):1;
    if(view==='face'){
      camera.fov=35;camera.position.set(0,5.5,11.2*cameraDistance*(w<600?1.30:1));camera.lookAt(0,4.55,-1.15);
    }else if(view==='character'){
      camera.fov=35;camera.position.set(3.1,6.1,11.2*cameraDistance*(w<600?1.30:1));camera.lookAt(0,4.35,-1.15);
    }else if(view==='console'){
      camera.fov=48;camera.position.set(3.0*scale,8.7*scale,12.7*scale*cameraDistance);camera.lookAt(0,2.35,.6);
    }else if(theatreMode==='smart'){
      const fit=Math.max(1,.72/camera.aspect),room=safeArea.modalOpen||safeArea.keyboardOpen?1.34:safeArea.dialogueOpen&&w<600?(h<650?1.65:1.4):w<600&&h<650?1.2:1;
      camera.fov=40;camera.position.set(0,5.35,9.0*fit*room*cameraDistance);camera.lookAt(0,4.35,-1.15);
    }else{camera.fov=44;camera.position.set(3.5*scale,7.0*scale,16.7*scale*cameraDistance);camera.lookAt(0,3.15,0);}
    // Reserve the lower part of the screen for controls without cropping the rig.
    const smart=theatreMode==='smart';
    const shift=smart?(safeArea.dialogueOpen&&w>=600?-.18:0):theatreMode?(w<560?-.21:-.29):0;
    const smartY=w<600?(safeArea.dialogueOpen?(h<650?.14:.17):(h<650?.04:.12)):.045;
    camera.setViewOffset(w,h,Math.round(w*shift),Math.round(h*(smart?smartY:theatreMode&&w<560?.18:.10)),w,h);
  }
  if(settings.mode==='reference')camera.clearViewOffset();
  camera.updateProjectionMatrix();
  if(animate&&!settings.reduced&&!settings.paused){
    cameraTween={from,to:{position:camera.position.clone(),quaternion:camera.quaternion.clone(),fov:camera.fov},elapsed:0};
    camera.position.copy(from.position);camera.quaternion.copy(from.quaternion);camera.fov=from.fov;camera.updateProjectionMatrix();
  }
}
addEventListener('resize',()=>resize());resize();

function syncActionControls(){
  const unavailable=!ready||settings.mode!=='stage'||settings.reduced||show.enabled;
  document.querySelectorAll('[data-action]').forEach(b=>{
    b.disabled=unavailable||Boolean(action);
    b.classList.toggle('active',Boolean(action)&&b.dataset.action===action.type);
  });
  $('dissolve').disabled=unavailable||!action||Boolean(action.cancel);
  $('action-frame').disabled=unavailable||Boolean(action?.cancel);
  document.querySelectorAll('[data-view]').forEach(b=>b.disabled=!ready||settings.mode!=='stage');
  document.querySelectorAll('[data-expression]').forEach(b=>b.disabled=!ready||settings.mode!=='stage'||show.enabled&&show.autoExpression);
  $('expression-strength').disabled=!ready||settings.mode!=='stage'||show.enabled&&show.autoExpression;
  $('inspect-face').disabled=!ready||settings.mode!=='stage';
  $('shape').disabled=show.enabled&&settings.mode==='stage';
  $('show-frame').disabled=!ready||settings.mode!=='stage';
  $('next-heavy').disabled=!ready||settings.mode!=='stage';
  document.querySelectorAll('[data-cue]').forEach(b=>b.disabled=!ready||settings.mode!=='stage');
}
function selectMode(mode){
  settings.mode=mode;cameraDistance=1;
  director.reset();applyExpression();
  $('stage-mode').setAttribute('aria-pressed',String(mode==='stage'));
  $('reference-mode').setAttribute('aria-pressed',String(mode==='reference'));
  action=null;$('action-frame').value='0';$('action-time').textContent='0.0秒';syncActionControls();resize();
}
$('stage-mode').addEventListener('click',()=>selectMode('stage'));
$('reference-mode').addEventListener('click',()=>selectMode('reference'));
function pauseShow(){
  settings.paused=true;$('pause').setAttribute('aria-pressed','true');$('pause').textContent='继续演出';
}
function setShowEnabled(enabled){
  show.enabled=enabled;$('show-enabled').checked=enabled;action=null;
  director.reset();applyExpression();syncActionControls();
}
$('show-enabled').addEventListener('change',()=>setShowEnabled($('show-enabled').checked));
$('auto-expression').addEventListener('change',()=>{show.autoExpression=$('auto-expression').checked;applyExpression();syncActionControls();});
$('bpm').addEventListener('input',()=>{show.bpm=Number($('bpm').value);$('bpm-output').textContent=show.bpm+' BPM';});
$('show-intensity').addEventListener('input',()=>{show.intensity=Number($('show-intensity').value);});
$('show-frame').addEventListener('input',()=>{setShowEnabled(true);show.beat=Number($('show-frame').value);pauseShow();});
$('next-heavy').addEventListener('click',()=>{setShowEnabled(true);show.beat=(Math.floor(show.beat/4)+1)*4;pauseShow();});
document.querySelectorAll('[data-cue]').forEach(button=>button.addEventListener('click',()=>{
  setShowEnabled(true);show.beat=Number(button.dataset.cue)*8+2.25;pauseShow();selectView('wide',false);
}));
function selectView(next,animate=true){
  view=next;cameraDistance=1;
  document.body.dataset.cameraView=next;
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));
  resize(animate);
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{
  selectView(button.dataset.view);
}));
document.querySelectorAll('[data-expression]').forEach(button=>button.addEventListener('click',()=>{
  expression=button.dataset.expression;applyExpression();
}));
$('expression-strength').addEventListener('input',()=>{
  expressionAmount=Number($('expression-strength').value)/100;applyExpression();
});
$('inspect-face').addEventListener('click',()=>{
  pauseShow();
  selectView('face',false);
});
$('headphones').addEventListener('change',()=>{headphones.visible=$('headphones').checked;});
$('pause').addEventListener('click',()=>{
  settings.paused=!settings.paused;$('pause').setAttribute('aria-pressed',String(settings.paused));$('pause').textContent=settings.paused?'继续演出':'暂停演出';
});
$('speed').addEventListener('input',()=>{settings.speed=Number($('speed').value);$('speed-output').textContent=settings.speed.toFixed(1)+'×';});
$('brightness').addEventListener('input',()=>{settings.brightness=Number($('brightness').value);});
$('shape').addEventListener('change',()=>{
  settings.shape=$('shape').value;
  tracks.forEach(track=>applyShapeSelection(track,settings.shape,age,settings.paused||settings.reduced));
});
$('quality').addEventListener('change',()=>{settings.quality=$('quality').value;renderer.setPixelRatio(settings.quality==='low'?1:Math.min(devicePixelRatio||1,1.5));resize();});
$('reduced').addEventListener('change',()=>{settings.reduced=$('reduced').checked;action=null;selectMode(settings.mode);});
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>{
  if(!ready||settings.reduced||settings.mode!=='stage'||action)return;
  if(settings.paused){settings.paused=false;$('pause').textContent='暂停动效';$('pause').setAttribute('aria-pressed','false');}
  action=beginAction(action,button.dataset.action,age);
  lastActionType=button.dataset.action;
  $('action-frame').value='0';$('action-time').textContent='0.0秒';
  syncActionControls();
}));
$('dissolve').addEventListener('click',()=>{
  const opacity=hands.map(h=>h.object.material.uniforms.uOpacity.value);
  const device={jog:deviceRigs.get(action?.type==='scratch'?'stage.deckA':'stage.deckB')?.rotation.y??0,
    fader:(deviceRigs.get('stage.crossfader')?.position.x??-.12)+.12,eq:deviceRigs.get('stage.eq')?.rotation.y??0};
  action=beginReturn(action,age,hands.map(h=>h.object.geometry.attributes.position.array));
  if(action?.cancel&&!action.cancel.opacity)action.cancel.opacity=opacity;
  if(action?.cancel&&!action.cancel.device)action.cancel.device=device;
  if(action&&settings.paused){settings.paused=false;$('pause').textContent='暂停动效';$('pause').setAttribute('aria-pressed','false');}
  syncActionControls();
});
$('action-frame').addEventListener('input',()=>{
  if(!ready||settings.reduced||settings.mode!=='stage'||action?.cancel)return;
  const at=Number($('action-frame').value);
  action={type:lastActionType,start:age-at};settings.paused=true;
  $('pause').setAttribute('aria-pressed','true');$('pause').textContent='继续动效';
  $('action-time').textContent=at.toFixed(1)+'秒';
  syncActionControls();
});
$('compare').addEventListener('click',()=>$('reference-dialog').showModal());
$('close-compare').addEventListener('click',()=>$('reference-dialog').close());
renderer.domElement.addEventListener('pointerdown',event=>{pointer={x:event.clientX,y:event.clientY,id:event.pointerId};renderer.domElement.setPointerCapture(event.pointerId);});
renderer.domElement.addEventListener('pointermove',event=>{
  if(!pointer)return;yaw+=(event.clientX-pointer.x)*.004;pitch=Math.max(-1.1,Math.min(1.1,pitch+(event.clientY-pointer.y)*.004));pointer.x=event.clientX;pointer.y=event.clientY;
});
renderer.domElement.addEventListener('pointerup',()=>{pointer=null;});
renderer.domElement.addEventListener('pointercancel',()=>{pointer=null;});
renderer.domElement.addEventListener('wheel',event=>{event.preventDefault();cameraDistance=Math.max(.78,Math.min(1.45,cameraDistance+event.deltaY*.0004));resize();},{passive:false});
renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();settings.paused=true;$('load-status').textContent='图形上下文中断，请刷新重试';$('fallback').hidden=false;});
document.addEventListener('visibilitychange',()=>{last=0;syncStageFrame();});

if(eyeReview){
  document.title='灵客星球 · B 眼神试看';
  document.body.dataset.eyeReview='b';
  const panel=document.createElement('aside');panel.id='eye-review';panel.setAttribute('aria-label','B版眼神试看');
  panel.innerHTML='<strong>B · 狡黠凝视 / 眼神试看</strong><small>保留紫色星球、耳机、粒子环 · 点击切换表情，或播放摘镜与推镜动作</small>'+
    '<button data-review-expression="smile" aria-pressed="true">狡黠凝视</button><button data-review-expression="cheeky" aria-pressed="false">搞怪</button>'+
    '<button data-review-expression="angry" aria-pressed="false">愤怒</button><button data-review-expression="melancholy" aria-pressed="false">忧郁</button>'+
    '<button id="review-glasses" aria-pressed="false">戴回墨镜</button><button id="review-motion">播放摘镜动作</button><a href="/workbench">回工作台</a>';
  document.body.append(panel);
  panel.querySelectorAll('[data-review-expression]').forEach(button=>button.addEventListener('click',()=>{
    reviewExpression=button.dataset.reviewExpression;
    panel.querySelectorAll('[data-review-expression]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  }));
  $('review-glasses').addEventListener('click',()=>{
    reviewStarted=null;reviewGlasses=!reviewGlasses;
    $('review-glasses').setAttribute('aria-pressed',String(reviewGlasses));
    $('review-glasses').textContent=reviewGlasses?'露眼凝视':'戴回墨镜';
  });
  $('review-motion').addEventListener('click',()=>{reviewStarted=performance.now();});
}

let stageFrameId=null;
function syncStageFrame(){
  if(stageVisible&&!document.hidden){if(stageFrameId===null)stageFrameId=requestAnimationFrame(frame);}
  else if(stageFrameId!==null){cancelAnimationFrame(stageFrameId);stageFrameId=null;}
}
function frame(now){
  stageFrameId=null;syncStageFrame();
  if(document.hidden||!stageVisible||settings.reduced&&last&&now-last<1000/15)return;
  const dt=Math.max(0,Math.min(.1,(now-(last||now))/1000));last=now;
  const live=audioDrive.sample(now);
  const ambient=ambientMotion.sample(now,{hidden:document.hidden||!stageVisible||!ready,reduced:settings.reduced,
    blocked:interactionBlocked||safeArea.dialogueOpen||safeArea.keyboardOpen||dialogue.phase!=='idle'||Boolean(pointer)||Boolean(cameraTween)||settings.paused||settings.mode!=='stage',view,
    playing:live.playing,flight:live.flight.active});
  if(eyeReview){
    live.expression=reviewExpression;
    // Review the actual rig without synthetic music or changing the normal clock.
    const age=reviewStarted===null?null:(now-reviewStarted)/1000;
    if(age!==null&&age>=5.7){reviewStarted=null;reviewGlasses=true;}
    ambient.cameo=age===null||age>=5.7?{amount:reviewGlasses?0:1,bow:reviewGlasses?0:1,glassesDown:reviewGlasses?0:1,handReach:0,expression:reviewExpression,roll:0}:
      {...cameraVisitAt(age),expression:reviewExpression};
    $('review-glasses').setAttribute('aria-pressed',String(reviewGlasses));
    $('review-glasses').textContent=reviewGlasses?'露眼凝视':'戴回墨镜';
  }
  if(ready&&!settings.paused&&!settings.reduced){
    age+=dt*settings.speed;
  }
  show.beat=live.beat;
  if(cameraTween&&!settings.paused&&!settings.reduced){
    const tween=cameraTween;tween.elapsed+=dt;const blend=ease(tween.elapsed/.8);
    camera.position.lerpVectors(tween.from.position,tween.to.position,blend);
    camera.quaternion.slerpQuaternions(tween.from.quaternion,tween.to.quaternion,blend);
    camera.fov=tween.from.fov+(tween.to.fov-tween.from.fov)*blend;camera.updateProjectionMatrix();
    if(blend===1)cameraTween=null;
  }
  updateSpace(age,settings.paused||settings.reduced?0:dt);
  if(show.enabled&&settings.mode==='stage'&&ready){
    const state=director.update(show.beat,{brightness:settings.brightness,intensity:show.intensity,reduced:settings.reduced,autoExpression:show.autoExpression,drive:live,
      ambient,cameraPosition:camera.position,cameraAspect:camera.aspect,cameraFov:camera.fov,dialogue,compact:innerWidth<760,review:eyeReview});
    updateVisitTrail(ambient.cameo.amount);
    $('show-frame').value=String(state.beat);$('show-time').textContent=state.beat.toFixed(1)+' / 32 拍';
    $('show-cue').textContent=state.title;$('show-detail').textContent=state.detail;
    if(live.expression)$('expression-status').textContent=({smile:'招牌微笑',melancholy:'忧郁',angry:'愤怒'}[live.expression]||live.expression)+' · 用户心情';
    $('phase').textContent=(live.playing?'音频脉冲 '+live.count:'星云待机')+' · '+['球壳','旋臂','星环','簇团'][state.shape];
    $('gesture').textContent=ambient.cameo.amount>.1?(ambient.cameo.handReach>.4?'指尖推镜 · 恢复造型':'微低头 · 越过墨镜凝视'):live.playing?(state.heavy>.2?'重拍强调':'节拍律动'):'轻盈悬浮 · 星尘巡游';
      document.querySelectorAll('[data-led]').forEach((led,i)=>led.classList.toggle('lit',live.playing&&i===Math.floor(state.beat)%4));
    document.querySelectorAll('[data-cue]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.cue)===state.phrase)));
    document.body.style.setProperty('--beat-glow',String(state.energy*(settings.reduced?0:show.intensity)*.16));
    document.body.style.setProperty('--show-color',PALETTES[state.palette][0]);
  }else{
    updateHands(age);document.body.style.setProperty('--beat-glow','0');
    $('show-cue').textContent=settings.mode==='reference'?'星云规律对照':'手动动作检查';
    $('show-detail').textContent=settings.mode==='reference'?'36 秒聚散规律，可切回舞台继续演出。':'可在下方单独检查形态、手势与表情。';
    document.querySelectorAll('[data-led]').forEach(led=>led.classList.remove('lit'));
  }
  renderer.render(scene,camera);frameCount++;
}
syncActionControls();
loadModel().catch(error=>{$('load-status').textContent='场景加载失败：'+error.message;$('fallback').hidden=false;console.error(error);});
syncStageFrame();
