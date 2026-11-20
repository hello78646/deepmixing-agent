import {ease,TAU} from '../lingke-motion-v3/motion.mjs';

export const PHRASES=[
  {action:'cue',expression:'smile',title:'耳机监听 · 右手调 EQ',detail:'左手扶耳罩监听，右手小幅旋转均衡器。'},
  {action:'blend',expression:'angry',title:'搓碟接入 · 推子交接',detail:'左手控制碟盘，右手推动横向推子。'},
  {action:'drop',expression:'wow',title:'重拍切入 · 单手点拍',detail:'左手抬掌强调落点，右手继续控制碟盘。'},
  {action:'tone',expression:'cheeky',title:'回到监听 · 收整音色',detail:'左手回扶耳罩，右手调 EQ；准备下个乐句。'},
];
export const PALETTES=[
  ['#bfa2ff','#60dfeb'],['#4adee0','#a398ff'],['#ed82c5','#febc77'],['#ffc684','#9b9fff'],
  ['#76d8ff','#ee8de1'],['#f2a4c7','#85e3d4'],['#a4b2ff','#f0b97e'],['#b6a2fa','#71dcd0'],
];
export const ROLES={cue:['ear','eq'],blend:['scratch','fader'],drop:['raise','scratch'],tone:['ear','eq']};
export function performanceAt(value){
  const absolute=Number.isFinite(value)?Math.max(0,value):0,beat=absolute%32;
  const bar=Math.floor(beat/4),phrase=Math.floor(beat/8),previous=(phrase+3)%4;
  const local=beat%1,withinBar=beat%4,withinPhrase=beat%8;
  const kick=Math.exp(-local*10),heavy=Math.exp(-withinBar*8);
  const movement={jog:Math.sin(beat*TAU)*.24,fader:Math.sin(beat*TAU/8)*.30,eq:Math.sin(beat*TAU/4)*.48};
  return {beat,bar,phrase,local,kick,heavy,energy:kick*.65+heavy*.35,
    shape:bar%4,previousShape:(bar+3)%4,shapeMix:ease(withinBar/.85),
    palette:bar,previousPalette:(bar+7)%8,paletteMix:ease(withinBar/.60),
    action:PHRASES[phrase].action,previousAction:PHRASES[previous].action,actionMix:ease(withinPhrase/1.25),
    expression:PHRASES[phrase].expression,previousExpression:PHRASES[previous].expression,expressionMix:ease(withinPhrase/.85),
    title:PHRASES[phrase].title,detail:PHRASES[phrase].detail,movement};
}

export function deviceWeights(state){
  const result={deckA:0,deckB:0,fader:0,eq:0};
  for(const [action,weight] of [[state.previousAction,1-state.actionMix],[state.action,state.actionMix]]){
    ROLES[action].forEach((role,side)=>{
      const key=role==='scratch'?(side===0?'deckA':'deckB'):role;
      if(key in result)result[key]+=weight;
    });
  }
  return result;
}

// A visual clock, independent of audio measurements: standby is alive, never a fake beat.
const clamp=n=>Math.max(0,Math.min(1,n));
const ambientSmooth=n=>{const t=clamp(n);return t*t*(3-2*t);};
export function cameraVisitAt(age){
  return {
    amount:ambientSmooth(age/1.05)*(1-ambientSmooth((age-4.1)/1.5)),
    bow:ambientSmooth((age-.2)/.8)*(1-ambientSmooth((age-3.5)/.65)),
    glassesDown:ambientSmooth((age-.35)/.6)*(1-ambientSmooth((age-2.8)/.75)),
    handReach:ambientSmooth((age-2.15)/.5)*(1-ambientSmooth((age-3.55)/.65)),
    expression:'smile',roll:0,
  };
}
// Keep the face in the visible frustum during real Z travel. The ring may
// reach the frame edge; the face/earphones must never cross the near plane.
export function screenApproach(amount,{distance=10,aspect=1.5,fov=40,review=false}={}){
  if(review||!Number.isFinite(distance)||distance<=0)return 0;
  const fit=1.95/(Math.tan(fov*Math.PI/360)*Math.min(.79,Math.max(.1,aspect)*.86));
  return clamp(amount)*Math.max(0,Math.min(.58,1-fit/distance));
}
export function createAmbientMotion(){
  let last=null,time=0,nextVisit=7,start=null,index=0,cancel=null,requested=false,pose=cameraVisitAt(-1);
  function sample(now,{hidden=false,reduced=false,blocked=false,view='wide',playing=false,flight=false}={}){
    const dt=last===null?0:Math.max(0,Math.min(.1,(now-last)/1000));last=now;
    if(!hidden&&!reduced)time+=dt;
    const allowed=!hidden&&!reduced&&!blocked&&view==='wide'&&!flight;
    if(!allowed&&start!==null&&cancel===null)cancel={at:time,from:{...pose}};
    if(start===null){
      if(!allowed)nextVisit=Math.max(nextVisit,time+4);
      else if(requested||time>=nextVisit){start=time;index++;requested=false;}
    }
    if(start!==null){
      const age=time-start;
      pose=cameraVisitAt(age);
      if(cancel){
        const fade=1-ambientSmooth((time-cancel.at)/.6);
        for(const key of ['amount','bow','glassesDown','handReach'])pose[key]=cancel.from[key]*fade;
      }
      if(age>=5.6||cancel&&time-cancel.at>=.6||reduced){
        start=null;cancel=null;pose=cameraVisitAt(-1);nextVisit=time+[26,34,29,38][index%4];
      }
    }
    return {time,idle:!playing,bob:Math.sin(time*.85)*.065,sway:Math.sin(time*.38)*.045,
      cameo:{...pose}};
  }
  function visit(){
    if(start!==null)return false;
    requested=true;return true;
  }
  return {sample,visit};
}
