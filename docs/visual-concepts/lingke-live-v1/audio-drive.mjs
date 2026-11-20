// Audio measurements drive the show. No free-running simulated beat clock.
const clamp=(n,max=1)=>Math.max(0,Math.min(max,n));
export function createAudioDrive(){
  let messageAt=-Infinity,pulseAt=-Infinity,flightTime=-Infinity,flightSeed=0,nextFlight=8,reduced=false,playing=false,count=0,seconds=0;
  let rms=0,bass=0,interval=500,frozenBeat=0,action=null,actionAt=-Infinity,mood='auto',workspaceMode=null;
  function sample(now){
    const connected=now-messageAt<500;
    const running=playing&&connected;
    const beat=running&&count ? count+Math.min(.99,Math.max(0,now-pulseAt)/interval) : frozenBeat;
    const pulse=running ? Math.exp(-Math.max(0,now-pulseAt)/110)*clamp(rms*5) : 0;
    const audioTime=seconds+(running?Math.min(.1,Math.max(0,now-messageAt)/1000):0);
    const flightAge=audioTime-flightTime,flightActive=!reduced&&flightAge>=0&&flightAge<1.8;
    const progress=flightActive?clamp(flightAge/1.8):0;
    const envelope=flightActive?Math.sin(progress*Math.PI)**2:0;
    const theta=progress*Math.PI*2+flightSeed*.83;
    return {connected,playing:running,seconds,count,beat,workspaceMode,energy:running?clamp(rms*4):0,
      pulse,heavy:pulse*clamp(bass*1.4),flight:{active:flightActive,progress,
        x:envelope?Math.sin(theta)*4.8*envelope:0,y:envelope?Math.cos(theta)*1.35*envelope:0,
        z:envelope?Math.sin(theta)*1.15*envelope:0,tilt:envelope?Math.sin(theta)*.22*envelope:0},
      action:now-actionAt<1400?action:null,expression:mood==='auto'?null:mood};
  }
  function ingest(data,now){
    if(!data||typeof data.playing!=='boolean'||!['time','rms','bass','onset'].every(k=>Number.isFinite(data[k])))return false;
    if(data.time<0||data.time>100000||['rms','bass','onset'].some(k=>data[k]<0||data[k]>1))return false;
    frozenBeat=sample(now).beat;
    if(data.seek||data.time<seconds-.2){count=0;frozenBeat=0;pulseAt=-Infinity;flightTime=-Infinity;nextFlight=8;}
    seconds=data.time;rms=data.rms;bass=data.bass;playing=data.playing;messageAt=now;
    if(typeof data.reduced==='boolean')reduced=data.reduced;
    if(data.presentation==='theatre'&&['smart','pro'].includes(data.workspaceMode))workspaceMode=data.workspaceMode;
    if(playing&&rms>.006&&data.onset>.15&&now-pulseAt>=220){
      const gap=now-pulseAt;if(gap>=250&&gap<=1200)interval=gap;
      pulseAt=now;count++;frozenBeat=count;
      if(!reduced&&count>=nextFlight&&rms>.04&&bass>.45&&data.onset>.4){
        flightTime=seconds;flightSeed=count;nextFlight=count+[20,32,24][Math.floor(count/8)%3];
      }
    }
    if(['eq','fader','scratch','drop'].includes(data.action)){
      action={eq:'cue',fader:'blend',scratch:'blend',drop:'drop'}[data.action];actionAt=now;
    }
    if(['auto','smile','melancholy','angry'].includes(data.mood))mood=data.mood;
    return true;
  }
  return {ingest,sample};
}
