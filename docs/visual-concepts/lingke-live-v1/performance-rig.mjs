// Stable surface point identities are supplied by v3 createHandCloud.
// The ear pose is character-local; stage.mjs applies the full character matrix.
export function poseHeadphone(out,cloud){
  const shape=cloud.scratch;
  for(let i=0;i<out.length;i+=3){
    out[i]=Math.min(-2.09,-2.17-shape[i+1]*.6);
    out[i+1]=-.44+shape[i+2]*.90;
    out[i+2]=-.07+shape[i]*.88;
  }
  return out;
}

export function poseRaised(out,cloud){
  const shape=cloud.open;
  for(let i=0;i<out.length;i+=3){
    out[i]=-2.55+shape[i]*1.05;out[i+1]=.45+shape[i+2]*.95;out[i+2]=1.15-shape[i+1];
  }
  return out;
}

// Pull forward before lowering: a vertical translation clips the fitted lenses
// against the planet's wider equator. Both the frame and hand share this path.
export function glassesPoseAt(down,baseY=0,baseZ=0){
  const t=Math.max(0,Math.min(1,Number(down)||0));
  return {y:baseY-.30*t,z:.34*t*(2-t),pitch:.020*t,roll:baseZ};
}

export function poseGlassesPush(out,cloud,down,pose=glassesPoseAt(down)){
  const shape=cloud.fader,scale=.72;
  const cx=Math.cos(pose.pitch),sx=Math.sin(pose.pitch),cz=Math.cos(pose.roll),sz=Math.sin(pose.roll);
  for(let i=0;i<out.length;i+=3){
    const x=1.02-(shape[i]+.29)*scale,y=1.02+(shape[i+2]-.73)*scale,z=1.55-(shape[i+1]+.34)*scale;
    // XYZ Euler order, matching the glasses rig's rigid transform exactly.
    const rx=x*cz-y*sz,ry=x*sz+y*cz;
    out[i]=rx;out[i+1]=ry*cx-z*sx+pose.y;out[i+2]=ry*sx+z*cx+pose.z;
  }
  return out;
}

export function poseConsole(out,cloud,pose,side,movement){
  const shape=cloud[pose],mirror=side===0?1:-1,scale=1.12,tilt=.18;
  const spin=pose==='scratch'?movement.jog:pose==='eq'?movement.eq:0;
  const floor=pose==='scratch'?2.575:pose==='fader'?2.475:2.49;
  const target=pose==='scratch'?[side===0?-3.15:3.15,floor,.65]:pose==='fader'?[-.12+movement.fader,floor,-.64]:[.26,floor,1.41];
  const ref=pose==='eq'?[-.27,-.35,.60]:pose==='fader'?[-.29,-.34,.73]:[-.095,-.1,.98];
  const ct=Math.cos(tilt),st=Math.sin(tilt),cs=Math.cos(spin),ss=Math.sin(spin);
  const cx=ref[0]*scale*mirror,cz=(ref[1]*st+ref[2]*ct)*scale;
  const ox=target[0]-(cx*cs+cz*ss),oz=target[2]-(-cx*ss+cz*cs);
  let bottom=Infinity;
  for(let i=0;i<out.length;i+=3){
    const x=shape[i]*scale*mirror,y=shape[i+1]*scale,z=shape[i+2]*scale;
    const ty=y*ct-z*st,tz=y*st+z*ct;
    out[i]=ox+x*cs+tz*ss;out[i+1]=ty;out[i+2]=oz-x*ss+tz*cs;
    bottom=Math.min(bottom,ty);
  }
  for(let i=1;i<out.length;i+=3)out[i]+=floor+.009-bottom;
  return out;
}
