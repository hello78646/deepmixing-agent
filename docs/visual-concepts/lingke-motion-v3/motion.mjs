// Independent motion model. Reference timings were inspected in the installed
// Nebula static visual runtime; see REVIEW.md. No camera or microphone access.
export const TAU=Math.PI*2;
export const SHAPES=['sphere','galaxy','ring','cluster'];
export function seeded(seed){return()=>((seed=(Math.imul(seed,1664525)+1013904223)>>>0)/4294967296);}
export function ease(value){const x=Math.max(0,Math.min(1,value));return x*x*x*(x*(x*6-15)+10);}
export function phaseAt(seconds){
  const t=Math.max(0,seconds)%36;
  return {cycle:Math.floor(Math.max(0,seconds)/36),stage:t<22?'hold':t<32?'scatter':'gather',progress:t<22?0:t<32?(t-22)/10:(t-32)/4};
}
export function shapePoints(shape,count,radius,seed){
  const data=new Float32Array(count*3),rng=seeded(seed);
  for(let i=0;i<count;i++){
    const u=rng(),v=rng(),w=rng(),a=v*TAU;
    let x,y,z;
    if(shape==='sphere'||shape==='cluster'||shape==='scatter'){
      const h=2*u-1,r=shape==='sphere'?.96+.08*w:shape==='cluster'?Math.cbrt(w):.65+Math.cbrt(w)*.9;
      const q=Math.sqrt(1-h*h)*r;
      x=Math.cos(a)*q;y=h*r;z=Math.sin(a)*q;
    }else if(shape==='galaxy'){
      const r=Math.sqrt(u),theta=(i%3)*TAU/3+r*5+(v-.5)*.5;
      x=Math.cos(theta)*r;y=(w-.5)*(.06+.25*(1-r)**3);z=Math.sin(theta)*r;
    }else if(shape==='ring'){
      const r=.8+.2*u;x=Math.cos(a)*r;y=(w-.5)*.05;z=Math.sin(a)*r;
    }else throw new Error('Unknown particle shape');
    data.set([x*radius,y*radius,z*radius],i*3);
  }
  return data;
}
export function transitionPoints(out,from,cloud,to,phase){
  if(phase.stage==='hold'){out.set(from);return;}
  for(let i=0;i<out.length/3;i++){
    const delay=(i%8)/7*.12,blend=ease((phase.progress-delay)/(1-delay));
    const source=phase.stage==='scatter'?from:cloud,target=phase.stage==='scatter'?cloud:to;
    for(let axis=0;axis<3;axis++){const k=i*3+axis;out[k]=source[k]+(target[k]-source[k])*blend;}
  }
}
// A particle palm has volume, five fingers, and a tapered wrist. Coordinates
// are in a local palm-down rig: X left/right, Y thickness, Z finger direction.
export function handPoints(count,seed){
  const rng=seeded(seed),out=new Float32Array(count*3);
  const fingers=[[-.33,-.03,.69,.067],[-.11,.02,.85,.070],[.12,.01,.78,.067],[.34,-.04,.63,.060]];
  for(let i=0;i<count;i++){
    let x,y,z;
    const part=rng();
    if(part<.40){
      const a=rng()*TAU,r=Math.sqrt(rng());
      x=Math.cos(a)*r*.43;z=Math.sin(a)*r*.40-.08;y=(rng()-.5)*.18*(1-r*.3);
    }else if(part<.86){
      const f=fingers[Math.floor(rng()*4)],t=rng(),a=rng()*TAU,r=Math.sqrt(rng())*f[3]*(1-t*.25);
      x=f[0]+Math.cos(a)*r;z=.18+t*f[2];y=f[1]+Math.sin(a)*r-.10*Math.sin(t*Math.PI);
    }else if(part<.96){
      const t=rng(),a=rng()*TAU,r=Math.sqrt(rng())*.092;
      x=-.31-t*.40+Math.cos(a)*r;z=-.22+t*.46;y=Math.sin(a)*r-.02;
    }else{x=(rng()-.5)*.46;y=(rng()-.5)*.15;z=-.45-rng()*.30;}
    out.set([x,y,z],i*3);
  }
  return out;
}
export function actionState(seconds){
  const t=Math.max(0,seconds);
  if(t<1.2)return{phase:'gather',amount:ease(t/1.2),travel:0,operate:0,done:false};
  if(t<2)return{phase:'approach',amount:1,travel:ease((t-1.2)/.8),operate:0,done:false};
  if(t<5)return{phase:'operate',amount:1,travel:1,operate:(t-2)/3,done:false};
  if(t<5.8)return{phase:'retreat',amount:1,travel:1-ease((t-5)/.8),operate:1,done:false};
  if(t<7)return{phase:'scatter',amount:1-ease((t-5.8)/1.2),travel:0,operate:1,done:false};
  return{phase:'orbit',amount:0,travel:0,operate:1,done:true};
}
export function beginAction(current,type,start){return current||{type,start};}
export function beginReturn(current,start,positions){
  if(!current||current.cancel)return current;
  return {...current,cancel:{start,from:positions.map(p=>p.slice())}};
}
export function returnPoints(out,from,target,progress){
  const blend=ease(progress);
  for(let i=0;i<out.length;i++)out[i]=from[i]+(target[i]-from[i])*blend;
}
