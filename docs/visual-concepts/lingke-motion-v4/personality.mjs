// Character-local coordinates. The accepted v3 source is never mutated.
export function deformFace(out,source,part,expression,strength){
  const amount=Math.max(0,Math.min(1,Number(strength)||0));
  out.set(source);
  if(expression==='smile'||!amount)return out;
  for(let i=0;i<source.length;i+=3){
    const x=source[i],y=source[i+1],z=source[i+2];
    let nx=x,ny=y,nz=z;
    if(part==='brow'){
      if(expression==='melancholy')ny+=.12-.70*(Math.abs(x)-.28);
      else if(expression==='angry')ny+=-.24+.78*(Math.abs(x)-.28);
      else ny+=expression==='cheeky'?(x<0?-.10:.08)+x*.07:.12;
      if(expression==='melancholy'||expression==='angry'){
        const surfaceBefore=Math.sqrt(Math.max(.04,1.63**2-(x/1.06)**2-y**2));
        nz=Math.sqrt(Math.max(.04,1.63**2-(nx/1.06)**2-ny**2))+(z-surfaceBefore);
      }
    }else if(['mouth','teeth','tongue'].includes(part)){
      if(expression==='cheeky'){
        nx=x*1.12;ny=.26+(y-.26)*1.22+x*.12;
        if(part==='tongue'){nx=x*1.18+.015;ny=.12+(y-.2)*3.6;}
      }else if(expression==='wow'){
        nx=x*.68;ny=.10+(y-.26)*1.45;
      }else if(expression==='melancholy'){
        nx=x*.94;ny=.17-(y-.26)*.35-.08*(Math.abs(x)/.37)**2;
      }else if(expression==='angry'){
        nx=x*1.08;ny=.15-(y-.26)*.62;
        if(part==='teeth')ny=.16+(y-.34)*.8-.06*(Math.abs(x)/.37)**2;
      }
      const offset=part==='mouth'?.043:part==='teeth'?.069:.079;
      nz=Math.sqrt(Math.max(.04,1.63**2-(nx/1.06)**2-ny**2))+offset;
      if(part==='tongue'&&expression==='cheeky')nz+=.13+.10*Math.max(0,(.15-ny));
    }
    out[i]=x+(nx-x)*amount;out[i+1]=y+(ny-y)*amount;out[i+2]=z+(nz-z)*amount;
  }
  return out;
}

// Independent world-space anchors: foreground wings, middle sides and far upper sky.
// Slow bounded drift avoids the central character and the console.
export const SATELLITES=[
  {position:[-9.6,.6,.6],radius:.72,color:'#25d9bd',accent:'#b6fff1',tilt:.42},
  {position:[7.0,4.9,.2],radius:.87,color:'#ff673e',accent:'#ffe0a6',tilt:-.55},
  {position:[-5.5,2.7,-4.6],radius:.49,color:'#7e9eff',accent:'#d3ddff',tilt:-.25},
  {position:[8.8,1.5,-1],radius:.54,color:'#ec4bc7',accent:'#ffd3f1',tilt:.65},
  {position:[-3.5,6.8,-8.2],radius:.64,color:'#a35cf5',accent:'#e3d0ff',tilt:.8},
  {position:[6.2,7.0,-10.0],radius:.48,color:'#e8ad43',accent:'#fff2bd',tilt:-.4},
];
export function satellitePose(index,t){
  const p=SATELLITES[index].position;
  return [p[0]+Math.sin(t*.085+index*1.7)*.18,p[1]+Math.sin(t*.11+index)*.14,p[2]+Math.cos(t*.07+index)*.12];
}
