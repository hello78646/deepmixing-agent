'use strict';
let refreshLiveControls=()=>{};
(()=>{
  const section=node('section',undefined,'live-stage');section.id='live-stage';
  const heading=node('div',undefined,'live-head'),titles=node('div');
  titles.append(node('p','LINGKE · YOUR MUSIC, IN MOTION','live-kicker'),node('h2','你的灵客星球，跟着这版音乐上场。'));
  const toggle=node('button','收起舞台');toggle.id='live-stage-toggle';toggle.type='button';toggle.setAttribute('aria-expanded','true');
  heading.append(titles,toggle);section.append(heading);
  const layout=node('div',undefined,'live-layout'),frame=node('iframe',undefined,'live-frame');
  layout.id='live-stage-layout';toggle.setAttribute('aria-controls',layout.id);
  frame.title='灵客星球真实音频舞台';frame.loading='lazy';
  const opening=document.querySelector('#workbench-opening[open]:not(.is-leaving)');
  const loadStage=()=>{frame.src='/stage/lingke-live-v1/index.html';};
  // Give the two opening pulses priority over WebGL initialization.
  if(opening?.open)opening.addEventListener('workbench-opening-reveal',loadStage,{once:true});
  else loadStage();
  const rail=node('div',undefined,'live-rail'),nowPlaying=node('p','星球正在星尘间巡游，导入音乐，一起开场。');
  const transport=node('div',undefined,'live-transport'),play=node('button','播放当前试听'),progress=node('input'),clock=node('p','等待播放','live-time');
  play.type='button';progress.type='range';progress.min=0;progress.max=1;progress.step=.05;progress.value=0;progress.setAttribute('aria-label','当前音频播放进度');
  transport.append(play,progress,clock);rail.append(nowPlaying,transport);
  function selectControl(labelText,options){
    const label=node('label',labelText),select=node('select');
    for(const [value,text] of options){const option=node('option',text);option.value=value;select.append(option);}
    label.append(select);rail.append(label);return select;
  }
  const mood=selectControl('这次星球的心情',[['auto','随演出变化'],['smile','轻松 · 招牌微笑'],['melancholy','忧郁'],['angry','愤怒']]);
  const outfit=selectControl('星球穿搭',[['auto','跟随歌曲风格'],['hiphop','嘻哈 · 红帽银牙'],['chinese','国风 · 云纹玉饰'],['edm','电子 · 镜面荧光'],['rock','摇滚 · 深色夹克'],['jazz','爵士 · 小礼帽'],['chill','Lo-fi · 针织卫衣']]);
  const view=selectControl('舞台机位',[['wide','全景'],['character','人物特写'],['face','表情特写'],['console','DJ台特写']]);
  const motionPreference=matchMedia('(prefers-reduced-motion: reduce)');
  const reduceLabel=node('label',undefined,'live-check'),reduced=node('input');reduced.type='checkbox';reduced.checked=motionPreference.matches;
  let manualMotionPreference=false;
  function syncMotionPreference(){
    document.body.classList.toggle('reduced-motion',reduced.checked);
    document.body.classList.toggle('motion-preference-ready',true);
  }
  syncMotionPreference();
  reduceLabel.append(reduced,node('span','减少动态'));rail.append(reduceLabel);
  layout.append(frame,rail);section.append(layout);
  const note=node('p','没歌时也有星云巡游和偶尔的镜头互动；播放后跟随真实节奏。“减少动态”可关闭大幅动作。','live-note');section.append(note);
  document.querySelector('.intro').after(section);
  let active=null,context=null,visible=true,lastTime=0,lastBass=0,lastRms=0,lastTick=0,pendingAction=null,seek=false;
  const routed=new WeakMap();
  function available(audio){return audio?.src && (audio!==$('preview-audio') || !audio.hidden) && (audio!==$('full-audio') || !$('downloads').hidden);}
  function preferred(){
    if(typeof workbenchPreferredAudio==='function')return workbenchPreferredAudio(active);
    return available(active)?active : ($('set-audio')?.src ? $('set-audio') : !$('preview-audio').hidden && $('preview-audio').src ? $('preview-audio') : !$('downloads').hidden && $('full-audio').src ? $('full-audio') : $('source-a').src ? $('source-a') : null);
  }
  refreshLiveControls=()=>{
    const audio=preferred();play.disabled=!audio;
    const label=typeof workbenchAudioLabel==='function'?workbenchAudioLabel(audio):'当前试听';
    const action=audio&&!audio.paused?'暂停播放':audio&&label.startsWith('原素材')?'播放原素材':audio&&label.includes('完整成品')?'播放完整成品':'播放当前试听';
    if(play.textContent!==action)play.textContent=action;
    play.title=audio?label:'尚无可播放音频';
    play.setAttribute('aria-label',action+(audio?' · '+label:''));
    if(typeof refreshWorkbenchPlayback==='function')refreshWorkbenchPlayback(audio);
  };
  async function route(audio){
    const AudioContext=window.AudioContext || window.webkitAudioContext;
    if(!AudioContext)throw new Error('当前浏览器不支持音频驱动，仍可正常试听。');
    context??=new AudioContext();
    if(!routed.has(audio)){
      const source=context.createMediaElementSource(audio),split=context.createChannelSplitter(2);
      // Direct stereo output at unity gain. Analysis branches add no EQ, effects
      // or level changes; using separate channels avoids anti-phase cancellation.
      source.connect(context.destination);source.connect(split);
      const channels=[0,1].map(index=>{
        const analyser=context.createAnalyser();analyser.fftSize=2048;analyser.smoothingTimeConstant=.35;
        split.connect(analyser,index);
        return {analyser,time:new Uint8Array(analyser.fftSize),freq:new Uint8Array(analyser.frequencyBinCount)};
      });
      routed.set(audio,channels);
    }
    await context.resume();
  }
  function send(playing,rms=0,bass=0,onset=0){
    const audio=active,offset=Number(audio?.dataset.stageOffset || 0);
    const time=audio?audio.currentTime+offset:0;
    const focused=document.activeElement;
    const interactionBlocked=document.hidden||Boolean(document.querySelector('dialog[open]'))||
      Boolean(focused&&(focused.isContentEditable||['INPUT','TEXTAREA','SELECT','BUTTON'].includes(focused.tagName)));
    const dialogue=typeof getDialogueVoiceState==='function'?getDialogueVoiceState():{phase:'idle',level:0};
    const safeArea=typeof planetSafeArea==='function'?planetSafeArea():{};
    frame.contentWindow?.postMessage({type:'deepmixing-audio-frame',playing,time,rms,bass,onset,
      mood:mood.value,outfit:outfit.value,view:view.value,reduced:reduced.checked,visible,interactionBlocked,action:pendingAction,seek,
      dialogue,safeArea,
      presentation:document.body?.classList?.contains('shell-page')?'theatre':'panel',
      workspaceMode:typeof workbenchMode==='undefined'?'smart':workbenchMode},location.origin);
    pendingAction=null;seek=false;
  }
  document.addEventListener('play',async event=>{
    const audio=event.target;if(!(audio instanceof HTMLAudioElement))return;
    if(audio.dataset.dialogueVoice==='true'||audio.dataset.openingVoice==='true')return;
    for(const other of document.querySelectorAll('audio'))if(other!==audio)other.pause();
    active=audio;lastTime=audio.currentTime;lastBass=lastRms=0;seek=true;
    try{await route(audio);note.textContent='跟随当前播放器的真实能量与瞬态；心情由你选择。调音参数修改后，重新生成试听才会改变声音。';}
    catch(error){note.textContent=error.message;}
    refreshLiveControls();
  },true);
  document.addEventListener('pause',event=>{if(event.target===active){send(false);refreshLiveControls();}},true);
  document.addEventListener('seeking',event=>{if(event.target===active){seek=true;lastTime=active.currentTime;lastBass=lastRms=0;send(!active.paused);}},true);
  document.addEventListener('ended',event=>{if(event.target===active){send(false);refreshLiveControls();}},true);
  document.addEventListener('change',event=>{
    const path=event.target.dataset?.path;if(!path)return;
    pendingAction=path.includes('gain_db')||path==='b_offset_seconds'?'fader':'eq';send(Boolean(active&&!active.paused));
  });
  play.addEventListener('click',()=>{const audio=preferred();if(!audio)return;if(audio.paused)audio.play().catch(error=>{note.textContent=error.message;});else audio.pause();});
  progress.addEventListener('input',()=>{const audio=preferred();if(audio&&Number.isFinite(audio.duration))audio.currentTime=Number(progress.value);});
  toggle.addEventListener('click',()=>{
    visible=!visible;layout.hidden=!visible;toggle.textContent=visible?'收起舞台':'展开舞台';toggle.setAttribute('aria-expanded',String(visible));
    lastTime=active?.currentTime||0;lastBass=lastRms=0;seek=true;
    send(Boolean(active&&!active.paused));syncLiveTick();updateTransport();
  });
  for(const input of [mood,outfit,view])input.addEventListener('change',()=>send(Boolean(active&&!active.paused)));
  reduced.addEventListener('change',()=>{
    manualMotionPreference=true;syncMotionPreference();send(Boolean(active&&!active.paused));
  });
  motionPreference.addEventListener?.('change',event=>{
    if(manualMotionPreference)return;
    reduced.checked=event.matches;syncMotionPreference();send(Boolean(active&&!active.paused));
  });
  for(const type of ['focusin','focusout'])document.addEventListener(type,()=>send(Boolean(active&&!active.paused)));
  frame.addEventListener('load',()=>send(Boolean(active&&!active.paused)));
  for(const type of ['timeupdate','loadedmetadata','durationchange','emptied'])document.addEventListener(type,event=>{
    if(!visible&&event.target instanceof HTMLAudioElement&&event.target===preferred())updateTransport();
  },true);
  let liveTick=null;
  function syncLiveTick(){
    if(visible&&!document.hidden){if(liveTick===null)liveTick=requestAnimationFrame(tick);}
    else if(liveTick!==null){cancelAnimationFrame(liveTick);liveTick=null;}
  }
  document.addEventListener('visibilitychange',()=>{send(Boolean(active&&!active.paused));syncLiveTick();if(!document.hidden)updateTransport();});
  function tick(now){
    liveTick=null;syncLiveTick();
    if(!visible||document.hidden||now-lastTick<50)return;lastTick=now;
    let rms=0,bass=0;
    const playing=Boolean(active&&!active.paused&&!active.ended);
    if(playing){
      for(const channel of routed.get(active)||[]){
        channel.analyser.getByteTimeDomainData(channel.time);channel.analyser.getByteFrequencyData(channel.freq);
        let power=0;for(const value of channel.time)power+=((value-128)/128)**2;
        rms=Math.max(rms,Math.sqrt(power/channel.time.length));
        const low=Math.max(1,Math.floor(55/context.sampleRate*2048)),high=Math.ceil(180/context.sampleRate*2048);
        let sum=0;for(let i=low;i<=high;i++)sum+=channel.freq[i]/255;
        bass=Math.max(bass,sum/(high-low+1));
      }
      const offset=Number(active.dataset.stageOffset||0),current=active.currentTime+offset;
      const previous=lastTime+offset;
      if(project?.mode==='mashup'&&active.dataset.stageMix==='true'&&previous<project.b_offset_seconds&&current>=project.b_offset_seconds&&current-previous<.4&&!seek)pendingAction='drop';
      lastTime=active.currentTime;
    }
    const onset=Math.min(1,Math.max(0,bass-lastBass)*4+Math.max(0,rms-lastRms)*4);lastBass=bass;lastRms=rms;
    send(playing,Math.min(1,rms),Math.min(1,bass),onset);
    updateTransport();
  }
  function updateTransport(){
    const controlled=preferred();
    const duration=controlled&&Number.isFinite(controlled.duration)?controlled.duration:0;
    progress.disabled=!duration;progress.max=duration||1;progress.value=controlled?.currentTime||0;
    clock.textContent=controlled?`${controlled.currentTime.toFixed(1)} / ${duration.toFixed(1)} 秒`:'等待播放';
    nowPlaying.textContent=controlled?(!controlled.paused?'正在播放 · ':'已暂停 · ')+(controlled.getAttribute('aria-label')||'音频试听'):'星球正在星尘间巡游，导入音乐，一起开场。';
    refreshLiveControls();
  }
  syncLiveTick();refreshLiveControls();
})();
