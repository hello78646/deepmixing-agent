'use strict';
let dialogueVoiceState={phase:'idle',level:0,language:'auto'};
function getDialogueVoiceState(){return {...dialogueVoiceState};}
let agentVoiceStop=()=>{},agentVoiceReply=()=>{},agentVoicePrepareReply=()=>null,agentVoiceBeginTurn=()=>{},agentVoiceEndTurn=()=>{};
function detectDialogueLanguage(text,fallback='auto'){
  if(/[\u3040-\u30ff]/.test(text))return 'ja';
  if(/[\uac00-\ud7af]/.test(text))return 'ko';
  if(/[\u3400-\u9fff]/.test(text))return /[係嘅唔喺咩啲冇佢哋嚟咁乜]/.test(text)?'yue':fallback==='yue'?'yue':'zh';
  if(/[\u0400-\u04ff]/.test(text))return 'ru';
  if(/[\u0600-\u06ff]/.test(text))return 'ar';
  const words=text.toLowerCase().match(/[a-zà-ž]+/g)||[];
  const dictionaries={en:'hello the your you is are would like want with and what mixing companion tell me',de:'hallo ich nicht musik und deine gerne dein bin für möchtest mischen mit das ist sie wie',fr:'bonjour je vous votre avec une est musique suis comment',es:'hola quiero mezclar música soy para gracias cómo',it:'ciao sono voglio musica grazie come',pt:'olá sou quero você misturar obrigado'};
  let best='',score=0;
  for(const [language,terms] of Object.entries(dictionaries)){
    const known=new Set(terms.split(' ')),count=words.filter(word=>known.has(word)).length;
    if(count>score){score=count;best=language;}
  }
  if(score>=2||score&&fallback==='auto')return best;
  return fallback==='auto'?'en':fallback;
}
(()=>{
  const mic=$('shell-mic'),hold=$('shell-hold-to-talk'),draft=$('shell-draft'),send=$('shell-send');
  const status=$('agent-voice-status'),auto=$('agent-voice-auto');
  const language=$('shell-voice-language'),languageRow=$('shell-voice-language-row');
  if(!mic||!hold||!language||!languageRow)return;
  const validLanguages=new Set(['zh','en','ja','ko','auto']);
  const selectedLanguage=()=>validLanguages.has(language.value)?language.value:'';
  const languageKey='deepmixing-recording-language';
  try{language.value=localStorage.getItem(languageKey)||'';}catch{}
  const idleHint='按住说话 · 松开发送 · 移出取消';
  const labels={idle:'零氪星球',connecting:'正在打开麦克风…',listening:'正在录音',transcribing:'正在识别…',thinking:'零氪正在想…',preparing:'正在准备播报…',speaking:'零氪正在回答'};
  const audio=document.createElement('audio');audio.preload='auto';audio.hidden=true;audio.dataset.dialogueVoice='true';audio.setAttribute('aria-label','零氪星球语音回答');document.body.append(audio);
  let capabilities,context,outputMeter,session=null,speechEpoch=0,speechSession=null,turnLanguage='auto',voiceMode=false;
  let silentSource,typedVoicePreference;
  function primeReplyAudio(){
    // Some browsers grant playback to the element only during the user's gesture.
    // Play 100 ms of zero PCM on the same element, not muted (muted autoplay grants nothing).
    try{
      if(!silentSource){
        const header=Uint8Array.from(atob('UklGRmQGAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YUAGAAA='),c=>c.charCodeAt(0));
        silentSource=URL.createObjectURL(new Blob([header,new Uint8Array(1600)],{type:'audio/wav'}));
      }
      audio.src=silentSource;
      if(context?.state==='suspended')context.resume().catch(()=>{});
      return audio.play().then(()=>true,()=>false);
    }catch{return Promise.resolve(false);}
  }
  const rms=(analyser,samples)=>{
    analyser.getByteTimeDomainData(samples);
    let power=0;for(const n of samples)power+=((n-128)/128)**2;
    return Math.sqrt(power/samples.length);
  };
  function phase(value,hint=''){
    dialogueVoiceState.phase=value;dialogueVoiceState.level=0;
    document.body.dataset.voicePhase=value;
    $('planet-stop-speaking').hidden=!['speaking','preparing'].includes(value);
    $('planet-presence').textContent=labels[value];
    const action={connecting:'正在打开麦克风…',listening:'松开发送',transcribing:'正在识别…',thinking:'正在回复…'};
    hold.disabled=!selectedLanguage();
    language.disabled=['connecting','listening','transcribing','thinking'].includes(value);
    hold.textContent=action[value]||(hold.disabled?'请先选择录音语言':'按住说话');
    hold.dataset.recording=String(value==='listening');hold.dataset.cancel='false';
    status.textContent=hint||(voiceMode&&value==='idle'?(selectedLanguage()?idleHint:'请先选择录音语言，再按住说话'):'');
    status.hidden=!status.textContent;
  }
  function pauseMusic(){for(const a of document.querySelectorAll('audio'))if(a!==audio&&!a.paused)a.pause();}
  function clearSpeech(){
    speechEpoch++;speechSession=null;audio.pause();audio.removeAttribute('src');audio.load();
  }
  function releaseMic(s){
    clearTimeout(s.limit);s.input?.disconnect();s.input=null;s.analyser=null;
    s.stream?.getTracks().forEach(track=>track.stop());s.stream=null;
  }
  function cancelCapture(hint=''){
    const s=session;session=null;
    if(s){
      clearTimeout(s.timeout);s.request?.abort();
      if(s.recorder?.state==='recording')s.recorder.stop();
      releaseMic(s);
    }
    clearSpeech();phase('idle',hint);
  }
  function ensureContext(){
    const AudioContext=window.AudioContext||window.webkitAudioContext;
    if(!context&&AudioContext)context=new AudioContext();
    return context;
  }
  function setVoiceMode(on){
    voiceMode=on;languageRow.hidden=hold.hidden=!on;draft.hidden=send.hidden=on;
    mic.textContent=on?'⌨':'🎙';
    const label=on?'切换到键盘输入':'切换到语音输入';
    mic.setAttribute('aria-pressed',String(on));mic.setAttribute('aria-label',label);mic.title=label;
    if(!on||dialogueVoiceState.phase==='idle')cancelCapture(on&&capabilities?.asr_ready===false?'本机语音识别未就绪 · 可继续打字':'');
  }
  agentVoiceStop=()=>cancelCapture();
  function stopReply(s,hint){
    if(!s||session===s)cancelCapture(hint);
  }
  agentVoiceBeginTurn=(text,voiceSession)=>{
    if(!voiceSession&&session)cancelCapture();
    clearSpeech();turnLanguage=detectDialogueLanguage(text||'',voiceSession?.language||'auto');
    if(!voiceSession&&auto.checked)primeReplyAudio();
    phase('thinking',voiceSession?'已发送 · 零氪正在回复…':'');
  };
  agentVoiceEndTurn=voiceSession=>{
    if(dialogueVoiceState.phase==='thinking'&&(!voiceSession||session===voiceSession))cancelCapture();
  };
  function speechText(text,s){
    const clean=text.replace(/https?:\/\/\S+/g,'链接见文字').replace(/[`*_#]/g,'').trim();
    const limit=s?180:680;
    return clean.length>limit?clean.slice(0,limit-15).replace(/[，。！；？、\s]+$/,'')+'。完整内容请看文字。':clean;
  }
  agentVoicePrepareReply=(text,s)=>{
    if(!s||session!==s)return null;
    const spoken=speechText(text,s);
    if(!spoken)return null;
    const language=detectDialogueLanguage(text,s.language||turnLanguage);
    // Prepare in parallel with workbench actions, but only play a confirmed reply.
    return {epoch:speechEpoch,spoken,language,result:synthesize(spoken,language).then(url=>({url}),error=>({error}))};
  };
  async function speak(text,language,s=null,prepared=null,manual=false){
    if(s&&session!==s)return;
    if(!s){if(session)cancelCapture();pauseMusic();}
    if(s&&s.recorder?.state==='recording')return;
    const spoken=speechText(text,s);
    if(!spoken){if(s)cancelCapture();return;}
    const pending=prepared?.epoch===speechEpoch&&prepared.spoken===spoken&&prepared.language===language?prepared.result:null;
    clearSpeech();speechSession=s;
    const epoch=speechEpoch;
    if(manual)primeReplyAudio();
    phase('preparing',s?'正在准备语音 · 会自动播报':labels.preparing);
    try{
      // One clip per reply keeps natural punctuation without a gap for another synthesis.
      const result=pending?await pending:{url:await synthesize(spoken,language)};
      if(epoch!==speechEpoch)return;
      if(result.error)throw result.error;
      // Do not wait for AudioContext permission merely to play a media URL.
      // The optional waveform attaches only when the context is running.
      try{
        if(context?.state==='running'&&!outputMeter){
          const analyser=context.createAnalyser();analyser.fftSize=256;
          analyser.connect(context.destination);context.createMediaElementSource(audio).connect(analyser);
          outputMeter=analyser;
        }
      }catch{}
      audio.src=result.url;
      await audio.play();
      if(epoch===speechEpoch)phase('speaking',labels.speaking);
    }catch(error){
      if(epoch!==speechEpoch)return;
      const reason=error.name==='NotAllowedError'?'浏览器阻止了自动播放':error.http_status===400?error.message:'这次没能播出语音';
      stopReply(s,reason+' · 文字已保留，可重播');
    }
  }
  async function synthesize(text,language){
    const data=await api('/api/voice/speech',{text,language});
    if(typeof data.url!=='string'||!/^\/voice-audio\/[a-f0-9]{32}\.mp3$/.test(data.url))throw Error('没有取得有效语音');
    return data.url;
  }
  agentVoiceReply=(text,article,voiceSession=null,prepared=null)=>{
    const language=detectDialogueLanguage(text,voiceSession?.language||turnLanguage);
    const replay=node('button',voiceSession?'重播语音':'听这条回复','voice-replay');replay.type='button';
    replay.addEventListener('click',()=>{
      try{ensureContext()?.resume().catch(()=>{});}catch{}
      speak(text,language,null,null,true);
    });article.append(replay);
    if(voiceSession){if(session===voiceSession)speak(text,language,voiceSession,prepared);}
    else if(!session&&auto.checked)speak(text,language);
  };
  audio.addEventListener('ended',()=>{if(audio.ended&&audio.src!==silentSource)stopReply(speechSession);});
  audio.addEventListener('error',()=>{if(audio.error&&audio.getAttribute('src')&&audio.src!==silentSource)stopReply(speechSession,'播放没成功 · 文字已保留');});
  const outputSamples=new Uint8Array(256);
  function meter(){
    if(dialogueVoiceState.phase==='speaking'&&!audio.paused&&outputMeter)dialogueVoiceState.level=Math.min(1,rms(outputMeter,outputSamples)*5);
    if(session?.analyser&&dialogueVoiceState.phase==='listening')dialogueVoiceState.level=Math.min(1,rms(session.analyser,session.samples)*6);
    requestAnimationFrame(meter);
  }requestAnimationFrame(meter);
  async function transcribe(s,blob){
    if(session!==s)return;
    const controller=new AbortController();s.request=controller;
    // Include local model warm-up and decoding, as well as the worker's inference timeout.
    s.timeout=setTimeout(()=>{if(session===s)cancelCapture('识别超时，本轮未发送 · 请缩短录音再试');},55000);
    try{
      if(!blob.size||blob.size>8*1024*1024)throw Error('录音无效，请再试一次');
      const token=document.querySelector('meta[name="deepmixing-token"]').content;
      const response=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Deepmixing-Token':token,'X-Deepmixing-ASR-Language':s.inputLanguage},body:blob,signal:controller.signal});
      const data=await response.json().catch(()=>null);
      if(session!==s)return;
      clearTimeout(s.timeout);s.request=null;
      if(!response.ok||!data)throw Error(data?.error||'语音识别暂不可用，请重试');
      const text=typeof data.text==='string'?data.text.trim():'';
      if(!/[\p{L}\p{N}]/u.test(text))throw Error('没有听清，请再说一次');
      s.language=s.inputLanguage==='auto'?(data.language||'auto'):s.inputLanguage;dialogueVoiceState.language=s.language;
      const accepted=await sendAgentMessage({voiceText:text,voiceSession:s});
      if(accepted===false&&session===s){
        agentMessage('system','听到：'+text+'。未发送，请检查连接或等上一轮处理完。');
        if(typeof planetToggleDialogue==='function')planetToggleDialogue(true);
        cancelCapture('文字已保留在对话中 · 本轮未发送');
      }
    }catch(error){
      if(session===s)cancelCapture((error.name==='TypeError'?'语音识别连接中断':error.message||'语音识别失败')+' · 本轮未发送');
    }
  }
  function readyForHold(){
    if(!voiceMode||!selectedLanguage())return false;
    if(session&&!['preparing','speaking'].includes(dialogueVoiceState.phase))return false;
    if((typeof agentPending!=='undefined'&&agentPending)||(typeof busy!=='undefined'&&busy)){
      status.textContent='上一轮还没处理完，请稍等';status.hidden=false;return false;
    }
    return true;
  }
  async function startHold(key){
    if(!readyForHold())return;
    clearSpeech();pauseMusic();
    const s={key,inputLanguage:selectedLanguage(),released:false,cancel:false};session=s;phase('connecting','等显示「松开发送」后再说');
    // Mouse-down and key-down may be the only playback gesture on a long hold.
    primeReplyAudio().then(ok=>{s.audioPrimed=ok;});
    try{
      if(!window.MediaRecorder||!navigator.mediaDevices?.getUserMedia)throw Error('当前浏览器不支持录音，可继续打字');
      // Obtain capture before any network request. Metering is optional and never gates capture.
      const pending=navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      try{ensureContext()?.resume().catch(()=>{});}catch{}
      const stream=await pending;
      if(session!==s){stream.getTracks().forEach(track=>track.stop());return;}
      s.stream=stream;
      const mime=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(type=>MediaRecorder.isTypeSupported(type));
      const r=new MediaRecorder(stream,mime?{mimeType:mime}:undefined),chunks=[];s.recorder=r;
      r.ondataavailable=event=>{if(session===s&&event.data.size)chunks.push(event.data);};
      r.onstop=()=>{
        if(session!==s)return;
        if(!s.released){cancelCapture('录音意外停止，请重试');return;}
        transcribe(s,new Blob(chunks,{type:r.mimeType||'audio/webm'}));
      };
      r.onerror=()=>{if(session===s)cancelCapture('录音中断，请重试');};
      stream.getAudioTracks().forEach(track=>track.addEventListener('ended',()=>{if(session===s&&!s.released)cancelCapture('麦克风已断开');}));
      r.start();s.started=performance.now();phase('listening','移出按钮后松手可取消');
      try{if(context){s.input=context.createMediaStreamSource(stream);s.analyser=context.createAnalyser();s.analyser.fftSize=512;s.samples=new Uint8Array(s.analyser.fftSize);s.input.connect(s.analyser);}}catch{}
      s.limit=setTimeout(()=>{if(session===s&&!s.released)cancelCapture('录音超过 44 秒，未发送 · 请分段说');},44000);
    }catch(error){if(session===s)cancelCapture('无法录音：'+(error.name==='NotAllowedError'?'未获得麦克风权限':error.message));}
  }
  function finishHold(key,cancel=false){
    const s=session;
    if(!s||s.key!==key||s.released)return;
    if(cancel){cancelCapture('已取消 · 没有发送');return;}
    if(!s.recorder||performance.now()-s.started<300){cancelCapture(s.recorder?'按住久一点再说':'麦克风还没打开 · 请再按住说话');return;}
    s.released=true;phase('transcribing','录音已结束 · 正在识别，听清后会自动发送');
    if(!s.audioPrimed)primeReplyAudio();
    s.recorder.stop();releaseMic(s);
  }
  function outside(e){
    const r=hold.getBoundingClientRect(),pad=10;
    return e.clientX<r.left-pad||e.clientX>r.right+pad||e.clientY<r.top-pad||e.clientY>r.bottom+pad;
  }
  mic.addEventListener('click',()=>{setVoiceMode(!voiceMode);(voiceMode?(selectedLanguage()?hold:language):draft).focus();});
  language.addEventListener('change',()=>{
    try{localStorage.setItem(languageKey,selectedLanguage());}catch{}
    if(dialogueVoiceState.phase==='idle')phase('idle');
    else hold.disabled=!selectedLanguage();
  });
  hold.addEventListener('pointerdown',e=>{
    if(e.button!==0||e.isPrimary===false||!readyForHold())return;
    e.preventDefault();hold.focus({preventScroll:true});
    try{hold.setPointerCapture(e.pointerId);}catch{}
    startHold(e.pointerId);
  });
  hold.addEventListener('pointermove',e=>{
    if(session?.key!==e.pointerId||session.released)return;
    session.cancel=outside(e);hold.dataset.cancel=String(session.cancel);
    if(session.recorder)hold.textContent=session.cancel?'松开取消':'松开发送';
  });
  window.addEventListener('pointerup',e=>finishHold(e.pointerId,outside(e)));
  for(const name of ['pointercancel','lostpointercapture'])hold.addEventListener(name,e=>finishHold(e.pointerId,true));
  hold.addEventListener('contextmenu',e=>e.preventDefault());
  hold.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if(session)cancelCapture('已取消 · 没有发送');return;}
    if(![' ','Enter'].includes(e.key))return;
    e.preventDefault();if(!e.repeat)startHold(e.key);
  });
  window.addEventListener('keyup',e=>{if(session?.key===e.key){e.preventDefault();finishHold(e.key);}});
  const interrupt=()=>{if(['speaking','preparing'].includes(dialogueVoiceState.phase))stopReply(speechSession);};
  $('planet-stop-speaking').addEventListener('click',interrupt);$('agent-voice-stop').addEventListener('click',interrupt);
  $('agent-voice-preview').addEventListener('click',()=>speak('我是零氪。把你的灵感交给我，咱们把这段音乐，玩出一点新花样。','zh',null,null,true));
  auto.addEventListener('change',()=>{
    typedVoicePreference=String(auto.checked);
    if(!auto.checked&&!session)interrupt();
    try{localStorage.setItem('deepmixing-voice-approved-auto',typedVoicePreference);}catch{}
  });
  try{typedVoicePreference=localStorage.getItem('deepmixing-voice-approved-auto');}catch{}
  auto.checked=typedVoicePreference==='true';
  window.addEventListener('blur',()=>{if(session&&!session.released)cancelCapture('窗口失焦 · 录音已取消');});
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden&&session&&!session.released)cancelCapture('页面切到后台 · 录音已取消');
  });
  window.addEventListener('pagehide',()=>{cancelCapture();context?.close();});
  document.addEventListener('play',event=>{
    if(!(event.target instanceof HTMLMediaElement)||event.target===audio||event.target.muted||event.target.volume===0)return;
    if(session&&!session.released)cancelCapture('开始播放了 · 录音已取消');
    else if(dialogueVoiceState.phase==='speaking')interrupt();
  },true);
  const modeObserver=new MutationObserver(()=>{if(document.body.dataset.workbenchMode==='pro'&&voiceMode)setVoiceMode(false);});
  modeObserver.observe(document.body,{attributes:true,attributeFilter:['data-workbench-mode']});
  api('/api/voice/status').then(info=>{
    capabilities=info;
    if(typedVoicePreference==null&&info.tts_ready&&info.tts_provider==='gpt-sovits')auto.checked=true;
    if(info.voice_label)$('voice-title').textContent=info.voice_label;
    if(info.voice_note)$('agent-voice-note').textContent=info.voice_note+(info.tts_ready?'':' 语音服务尚未就绪，文字回复可继续使用。');
    if(!info.asr_ready&&voiceMode&&!session){status.textContent='本机语音识别未就绪 · 可继续打字';status.hidden=false;}
  }).catch(()=>{$('agent-voice-note').textContent='语音服务暂不可用，文字回复可以继续使用。';});
})();
