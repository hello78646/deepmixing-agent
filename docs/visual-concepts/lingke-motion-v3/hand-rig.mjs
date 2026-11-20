import {seeded,TAU,ease,actionState} from './motion.mjs';
export const HAND_COUNT=4600;
const lengths=[.70,.84,.78,.62],bases=[-.29,-.095,.12,.30];
const mix=(a,b,t)=>a+(b-a)*t;
function fingerPoint(index,t,pose){
  const angles=pose==='open'?[-.06,.04,.12]:pose==='scratch'?[.10,.13,.23]:
    pose==='fader'?(index===0?[.25,.65,1.0]:[.7,1.5,2.4]):(index===0?[.05,.80,1.7]:[.7,1.4,2.1]);
  let y=0,z=.16,left=t;
  for(let j=0;j<3;j++){
    const fraction=[.43,.34,.23][j],part=Math.min(left,fraction)*lengths[index];
    if(part>0){y-=Math.sin(angles[j])*part;z+=Math.cos(angles[j])*part;left-=fraction;}
  }
  const splay=pose==='eq'&&index===0?-.06:bases[index]*(pose==='open'?.20:.06);
  return [bases[index]+splay*t,y,z];
}
function thumbPoint(t,pose){
  const vertices=pose==='eq'?[[-.36,0,-.12],[-.48,-.12,.23],[-.19,-.36,.60]]:
    pose==='fader'?[[-.36,0,-.12],[-.48,-.18,.08],[-.27,-.32,.23]]:
    [[-.36,0,-.12],[-.58,-.06,.08],[-.69,-.10,.29]];
  const k=t<.5?0:1,u=(t-k*.5)*2;
  return vertices[k].map((value,axis)=>mix(value,vertices[k+1][axis],u));
}
function sectionPoint(center,next,radius,angle){
  const d=next.map((v,i)=>v-center[i]),length=Math.hypot(...d)||1;
  for(let i=0;i<3;i++)d[i]/=length;
  let u=[d[2],0,-d[0]],ul=Math.hypot(...u)||1;u=u.map(v=>v/ul);
  const v=[d[1]*u[2]-d[2]*u[1],d[2]*u[0]-d[0]*u[2],d[0]*u[1]-d[1]*u[0]];
  return center.map((c,i)=>c+radius*(Math.cos(angle)*u[i]+Math.sin(angle)*v[i]));
}
export function createHandCloud(count,seed){
  const cloud={};
  for(const pose of ['open','scratch','fader','eq']){
    const rng=seeded(seed),points=new Float32Array(count*3);
    for(let i=0;i<count;i++){
      const kind=rng(),u=rng(),v=rng(),w=rng(),a=v*TAU;
      const surface=w<.76?1:Math.cbrt(w),r=surface;
      let p;
      if(kind<.34){
        const h=u*2-1,q=Math.sqrt(1-h*h);
        p=[Math.cos(a)*q*.41*r,h*.105*r,Math.sin(a)*q*.36*r-.10];
      }else if(kind<.89){
        const f=Math.min(3,Math.floor((kind-.34)/.55*4)),t=u;
        const center=fingerPoint(f,t,pose),next=fingerPoint(f,Math.min(1.001,t+.002),pose);
        p=sectionPoint(center,next,(.071-f*.003)*(1-.32*t)*r,a);
      }else if(kind<.97){
        const center=thumbPoint(u,pose),next=thumbPoint(Math.min(1.001,u+.002),pose);
        p=sectionPoint(center,next,.079*(1-u*.28)*r,a);
      }else p=[Math.cos(a)*.22*r,Math.sin(a)*.077*r,-.42-u*.31];
      points.set(p,i*3);
    }
    cloud[pose]=points;
  }
  return cloud;
}
function rotate(x,y,z,mirror,tilt,spin){
  x*=mirror;
  const ty=y*Math.cos(tilt)-z*Math.sin(tilt),tz=y*Math.sin(tilt)+z*Math.cos(tilt);
  return [x*Math.cos(spin)+tz*Math.sin(spin),ty,-x*Math.sin(spin)+tz*Math.cos(spin)];
}
export function deviceMotion(seconds){
  const state=actionState(seconds);
  if(state.phase!=='operate')return{jog:0,fader:0,eq:0};
  const p=state.operate,envelope=Math.sin(p*Math.PI)**2;
  const wave=Math.sin(p*TAU)*envelope,scratch=Math.sin(p*TAU*3)*envelope;
  return{jog:scratch*.25,fader:wave*.30,eq:wave*.48};
}
export function returnDeviceMotion(from,progress){
  const blend=ease(progress);
  if(blend===1)return{jog:0,fader:0,eq:0};
  return{jog:from.jog*(1-blend),fader:from.fader*(1-blend),eq:from.eq*(1-blend)};
}
export function poseHand(out,cloud,type,side,seconds){
  const state=actionState(seconds),travel=state.travel;
  const pose=side===1?(type==='scratch'?'fader':'scratch'):type;
  const floor=pose==='scratch'?2.575:pose==='fader'?2.475:2.49;
  const target=pose==='scratch'?[side===0?-3.15:3.15,floor,.65]:pose==='fader'?[-.12,floor,-.64]:[.26,floor,1.41];
  const movement=deviceMotion(seconds),spin=pose==='scratch'?movement.jog:pose==='eq'?movement.eq:0;
  if(pose==='fader')target[0]+=movement.fader;
  const ref=pose==='eq'?[-.27,-.35,.60]:fingerPoint(pose==='fader'?0:1,.99,pose);
  const tilt=mix(1.05,.18,travel),mirror=side===0?1:-1,scale=1.12;
  const contact=rotate(...ref,mirror,tilt,spin).map(v=>v*scale);
  const anchor=[side===0?-2.8:2.8,4.15,1.85];
  const origin=anchor.map((v,i)=>mix(v,target[i]-contact[i],travel));
  const shape=cloud[pose],plane=ease((travel-.80)/.20);
  const ct=Math.cos(tilt),st=Math.sin(tilt),cs=Math.cos(spin),ss=Math.sin(spin);
  for(let i=0;i<out.length;i+=3){
    const x=mix(cloud.open[i],shape[i],travel)*scale*mirror;
    const y=mix(cloud.open[i+1],shape[i+1],travel)*scale;
    const z=mix(cloud.open[i+2],shape[i+2],travel)*scale;
    const ty=y*ct-z*st,tz=y*st+z*ct;
    out[i]=origin[0]+x*cs+tz*ss;out[i+1]=origin[1]+ty;out[i+2]=origin[2]-x*ss+tz*cs;
    out[i+1]=mix(out[i+1],Math.max(floor+.008,out[i+1]),plane);
  }
  return out;
}
