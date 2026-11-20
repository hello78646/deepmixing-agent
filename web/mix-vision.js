(() => {
  'use strict';
  const panel=document.getElementById('mix-vision');
  if(!panel)return;
  const rules=DeepmixingVisionRules,gate=rules.thumbGate();
  const get=(selector)=>panel.querySelector(selector),action=(name)=>get(`[data-action=${name}]`);
  const video=get('video'),quality=get('.vision-quality'),idle=get('.vision-idle'),notice=get('#mix-vision-status');
  const camera=action('camera'),manual=action('manual'),draftBox=get('.vision-draft'),focus=get('.vision-focus select');
  const heading=get('h2'),entries=[...document.querySelectorAll('.vision-entry')];
  const storageKey='deepmixing.confirmed-vision-feedback.v1';
  const labels={mix_preview:'双轨试听',mix_full:'双轨成品',set_preview:'串烧试听',set_full:'串烧成品'};
  let opener,stream,worker,ready=false,requesting=false,initTimer=0,flightTimer=0,videoTimer=0,frameHandle;
  let generation=0,epoch=0,serial=0,flight=null,lastSent=-Infinity,lastVideoTime=-1;
  let frameLimit=1280,frameCost=0;
  const previewOnly='实时预览仍在，可手动标记或关闭摄像头后重试。';
  let audio=null,playing=false,draft=null,records=[],volatile=false,secondary=new Set(),lastResultAt=-Infinity;
  const status=(text)=>{if(notice.textContent!==text)notice.textContent=text;};
  const time=(n)=>{const ticks=Math.round(n*10);return `${Math.floor(ticks/600)}:${String(Math.floor(ticks/10)%60).padStart(2,'0')}.${ticks%10}`;};
  const span=(entry)=>`${time(entry.start_seconds)} – ${time(entry.end_seconds)}`;
  const sample=()=>rules.point(audio,location.origin);
  const live=()=> playing&&!secondary.size&&!document.hidden&&audio&&!audio.paused&&!audio.muted&&audio.volume>0 ? sample():null;
  const release=(media)=>media?.getTracks().forEach(t=>t.stop());
  const resetTimeline=()=>{++epoch;gate.reset();lastResultAt=-Infinity;};
  function setDraft(value){
    draft=value;draftBox.hidden=!value;focus.value='';
    if(value)get('.vision-draft-time').textContent=`${labels[value.kind]} · 作品 ${span(value)}`;
  }
  function renderPlayback(){
    const point=secondary.size?null:sample();
    const playback=get('#mix-vision-playback');
    const label=point?`${labels[point.kind]} · ${time(point.offset+point.sec)}${audio.paused?'（暂停）':''}`:'等待播放混音作品';
    if(playback.textContent!==label)playback.textContent=label;
    manual.disabled=!point||point.sec<.25;
  }
  function renderQuality(){
    if(!stream||video.readyState<2||!video.videoWidth||!video.videoHeight)return;
    const fps=stream.getVideoTracks()[0]?.getSettings?.().frameRate;
    quality.textContent=`${video.videoWidth} × ${video.videoHeight}${fps>0?' · '+Math.round(fps)+' fps':''}`;
    quality.hidden=false;video.hidden=false;idle.hidden=true;
  }
  function stopCamera(message='摄像头已关闭。仍可手动标记。'){
    ++generation;resetTimeline();requesting=false;ready=false;
    clearTimeout(initTimer);clearTimeout(flightTimer);clearTimeout(videoTimer);
    if(frameHandle!==undefined){
      if(video.cancelVideoFrameCallback)video.cancelVideoFrameCallback(frameHandle);else cancelAnimationFrame(frameHandle);
      frameHandle=undefined;
    }
    worker?.terminate();worker=null;flight=null;
    release(stream);stream=null;video.pause();video.srcObject=null;video.hidden=true;quality.hidden=true;idle.hidden=false;
    camera.textContent='开启摄像头';status(message);
  }
  function stopRecognition(token,message){
    if(token!==generation||!stream)return;
    ready=false;clearTimeout(initTimer);clearTimeout(flightTimer);
    worker?.terminate();worker=null;flight=null;resetTimeline();
    status(message+' '+previewOnly);
  }
  function schedule(token){
    if(token!==generation||!stream||!ready||frameHandle!==undefined||panel.hidden||document.hidden)return;
    const next=(now,meta)=>{
      frameHandle=undefined;if(token!==generation||!ready||!worker)return;
      schedule(token);
      const mediaTime=meta?.mediaTime??video.currentTime;
      if(mediaTime===lastVideoTime||video.readyState<2)return;
      lastVideoTime=mediaTime;
      const point=live();
      if(flight||now-lastSent<83||!point||!video.videoWidth)return;
      lastSent=now;
      const frame={id:++serial,point,epoch,at:performance.now()};
      flight=frame;
      const scale=Math.min(1,frameLimit/Math.max(video.videoWidth,video.videoHeight));
      const width=Math.max(1,Math.round(video.videoWidth*scale)),height=Math.max(1,Math.round(video.videoHeight*scale));
      flightTimer=setTimeout(()=>{if(token===generation&&flight===frame)stopRecognition(token,'手势处理超时。');},2200);
      createImageBitmap(video,{resizeWidth:width,resizeHeight:height,resizeQuality:'medium'}).then(bitmap=>{
        if(token!==generation||flight!==frame){bitmap.close();return;}
        if(!rules.fresh(frame,live(),performance.now(),epoch)){bitmap.close();flight=null;clearTimeout(flightTimer);return;}
        try{worker.postMessage({type:'frame',id:frame.id,at:frame.at,frame:bitmap},[bitmap]);}
        catch{bitmap.close();stopRecognition(token,'无法处理视频帧。');}
      }).catch(()=>{if(token===generation&&flight===frame)stopRecognition(token,'无法读取视频帧。');});
    };
    frameHandle=video.requestVideoFrameCallback?video.requestVideoFrameCallback(next):requestAnimationFrame(next);
  }
  async function startCamera(){
    if(panel.hidden||document.hidden)return;
    if(!isSecureContext||!navigator.mediaDevices?.getUserMedia){
      status('当前页面不能调用摄像头。请在 Chrome / Edge 中直接打开 HTTPS 或本机工作台。');return;
    }
    const policy=document.permissionsPolicy||document.featurePolicy;
    if(policy?.allowsFeature?.('camera')===false){
      status('嵌入的页面未获摄像头权限。请在 Chrome / Edge 中直接打开工作台链接。');return;
    }
    const token=++generation;
    requesting=true;camera.textContent='取消开启';status('等待摄像头授权…');
    let media;
    try{
      try{
        media=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'user'},width:{ideal:2560},height:{ideal:1440},frameRate:{ideal:30,max:30}}});
      }catch(error){
        if(token!==generation||document.hidden||panel.hidden)return;
        if(!['OverconstrainedError','NotReadableError','AbortError'].includes(error.name))throw error;
        media=await navigator.mediaDevices.getUserMedia({audio:false,video:true});
      }
      if(token!==generation||panel.hidden||document.hidden){release(media);return;}
      stream=media;
      const track=media.getVideoTracks()[0];
      if(track.readyState!=='live')throw Error('ended');
      const disconnect=()=>{if(token===generation)stopCamera('摄像头已断开或权限已撤回。');};
      track.addEventListener('ended',disconnect,{once:true});
      video.srcObject=media;
      videoTimer=setTimeout(()=>{if(token===generation)stopCamera('摄像头画面未就绪，已停止。');},8000);
      await video.play();
      if(token!==generation)return;
      clearTimeout(videoTimer);
      renderQuality();requesting=false;lastSent=-Infinity;lastVideoTime=-1;frameLimit=1280;frameCost=0;resetTimeline();
      camera.textContent='关闭摄像头';status('实时预览已开启，正在加载本机手势识别…');
      const fail=(detail='')=>stopRecognition(token,detail||'本机手势识别未能启动。');
      if(!window.Worker||!window.createImageBitmap||!window.OffscreenCanvas){
        fail('当前浏览器不支持手势识别。');return;
      }
      try{worker=new Worker('/mix-vision-worker.js');}
      catch{fail();return;}
      worker.onerror=()=>fail();worker.onmessageerror=()=>fail();
      initTimer=setTimeout(()=>fail('本机手势识别加载超时。'),30000);
      worker.onmessage=({data})=>{
        if(token!==generation)return;
        if(data.type==='error'){fail(data.code==='asset'?'缺少手势模型或模型文件无法读取。':data.code==='frame'?'手势识别不能处理当前画面。':'本机手势识别未能启动。');return;}
        if(data.type==='ready'){
          clearTimeout(initTimer);ready=true;schedule(token);
          status('本机手势识别已就绪。播放混音时，竖拇指直到提示出现。');return;
        }
        if(data.type!=='result'||flight?.id!==data.id)return;
        const sent=flight;flight=null;clearTimeout(flightTimer);
        const now=performance.now();
        // Keep the actual camera preview at native resolution; only adjust the
        // analysis bitmap if this device cannot keep up with incoming frames.
        frameCost=frameCost?frameCost*.8+(now-sent.at)*.2:now-sent.at;
        if(frameCost>140)frameLimit=Math.max(720,frameLimit-160);
        else if(frameCost<65)frameLimit=Math.min(1280,frameLimit+80);
        if(!rules.fresh(sent,live(),now,epoch))return;
        if(sent.at-lastResultAt>650)gate.reset();
        lastResultAt=sent.at;
        const match=gate.step(sent.point,sent.at,data.signal);
        if(match&&!draft){const value=rules.segment(match);if(value){setDraft(value);status('收到手势。请确认是否喜欢这段。');}}
      };
      worker.postMessage({type:'init'});
    }catch(error){
      if(token===generation&&stream&&video.readyState>=2&&!requesting){stopRecognition(token,'手势识别未能启动。');return;}
      if(token!==generation){release(media);return;}
      stopCamera({NotAllowedError:'摄像头权限未允许。可在浏览器设置里授权，或手动标记。',NotFoundError:'没有找到摄像头。仍可手动标记。',NotReadableError:'摄像头可能被其他应用占用。仍可手动标记。'}[error.name]||'摄像头无法开启。仍可手动标记。');
    }
  }
  video.addEventListener('resize',renderQuality);
  video.addEventListener('playing',renderQuality);
  function read(){
    try{
      const stored=JSON.parse(localStorage.getItem(storageKey)||'[]');
      records=Array.isArray(stored)?stored.filter(r=>rules.valid(r)).slice(0,120).map(clean):[];
    }catch{volatile=true;}
  }
  const clean=(r)=>({output_id:r.output_id,kind:r.kind,start_seconds:r.start_seconds,end_seconds:r.end_seconds,...(r.focus?{focus:r.focus}:{})});
  function persist(){try{localStorage.setItem(storageKey,JSON.stringify(records));volatile=false;}catch{volatile=true;}}
  const current=(r)=>{const p=sample();return Boolean(p&&r.output_id===p.output_id&&r.kind===p.kind&&r.start_seconds>=p.offset-.001&&r.end_seconds<=p.offset+p.duration+.001);};
  function renderRecords(){
    const list=get('ul');list.replaceChildren();get('.vision-count').textContent=String(records.length);
    action('export').disabled=action('clear').disabled=!records.length;
    const directions=get('.vision-directions');
    const counts=Object.entries(rules.focusLabels).map(([code,label])=>[label,records.filter(r=>r.focus===code).length]).filter(([,n])=>n).sort((a,b)=>b[1]-a[1]);
    directions.hidden=!counts.length;
    directions.textContent=counts.length?'复听方向（你手动选择的项目）：'+counts.map(([label,n])=>`${label} ${n} 段`).join(' · '):'';
    for(const row of records){
      const li=document.createElement('li'),text=document.createElement('span'),replay=document.createElement('button'),remove=document.createElement('button');
      text.textContent=`${labels[row.kind]} · ${span(row)} · ${row.output_id.slice(0,6)}${row.focus?' · '+rules.focusLabels[row.focus]:''}`;
      replay.type=remove.type='button';replay.textContent='回听起点';remove.textContent='删除';
      replay.dataset.replay='true';replay.disabled=!current(row);
      replay.title='从这段的起点继续播放（须加载同一版本）';remove.setAttribute('aria-label',`删除 ${text.textContent}`);
      replay.onclick=()=>{if(current(row)){audio.currentTime=Math.max(0,row.start_seconds-sample().offset);audio.play().catch(()=>status('请从播放器继续试听。'));}};
      remove.onclick=()=>{records=records.filter(r=>r!==row);persist();renderRecords();status(volatile?'已从本次会话移除；浏览器存储未更新。':'已删除标记。');};
      li.append(text,replay,remove);list.append(li);
    }
  }
  function updateReplays(){
    const buttons=get('ul').querySelectorAll('[data-replay]');
    buttons.forEach((b,i)=>{b.disabled=!current(records[i]);});
  }
  const validPlayer=(el)=>el instanceof HTMLAudioElement&&['preview-audio','full-audio','set-audio'].includes(el.id);
  function playbackEvent(e){
    const el=e.target;if(!(el instanceof HTMLAudioElement))return;
    const type=e.type;
    if(!validPlayer(el)){
      if(type==='playing'&&!el.paused){secondary.add(el);resetTimeline();}
      if(['pause','ended','emptied','error'].includes(type)&&secondary.delete(el))resetTimeline();
    }else if(type==='play'||type==='playing'){
      if(el!==audio){audio=el;playing=false;setDraft(null);resetTimeline();updateReplays();}
      if(type==='playing'&&!playing&&!el.paused){playing=true;resetTimeline();}
    }else if(el===audio){
      if(['pause','ended','seeking','loadstart','waiting','emptied','error'].includes(type)){playing=false;resetTimeline();}
      if(type==='seeked'&&!el.paused&&el.readyState>=3){playing=true;resetTimeline();}
      if(type==='volumechange')resetTimeline();
      if(['seeking','loadstart','emptied','error'].includes(type))setDraft(null);
      if(type!=='timeupdate')updateReplays();
    }
    if(!panel.hidden)renderPlayback();
  }
  for(const type of ['play','playing','pause','ended','seeking','seeked','loadstart','waiting','emptied','loadeddata','timeupdate','volumechange','error'])
    document.addEventListener(type,playbackEvent,true);
  function close(){
    panel.hidden=true;stopCamera();setDraft(null);
    entries.forEach(b=>b.setAttribute('aria-expanded','false'));
    if(opener?.getClientRects().length)opener.focus();
  }
  entries.forEach(b=>b.addEventListener('click',()=>{
    if(!panel.hidden){close();return;}
    panel.hidden=false;opener=b;entries.forEach(entry=>entry.setAttribute('aria-expanded','true'));
    secondary=new Set([...document.querySelectorAll('audio')].filter(el=>!validPlayer(el)&&!el.paused&&!el.ended));
    renderPlayback();renderRecords();heading.focus();
  }));
  action('close').onclick=close;
  panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();close();}});
  camera.onclick=()=>{if(requesting||stream)stopCamera();else void startCamera();};
  video.addEventListener('resize',renderQuality);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){if(stream||requesting)stopCamera('切换到后台，摄像头已关闭。');setDraft(null);}});
  window.addEventListener('pagehide',()=>{if(stream||requesting)stopCamera();setDraft(null);});
  window.addEventListener('storage',e=>{if(!volatile&&(e.key===storageKey||e.key===null)){read();renderRecords();}});
  action('manual').onclick=()=>{const point=secondary.size?null:sample();if(point){const value=rules.segment(point);if(value){setDraft(value);status('已选中刚才的片段，确认后才保存。');}}};
  action('dismiss').onclick=()=>{setDraft(null);status('已忽略这次提示。');};
  action('like').onclick=()=>{
    if(!draft)return;
    const selected=focus.value?{...draft,focus:focus.value}:draft;
    if(!rules.valid(selected))return;
    if(!volatile)read();
    if(records.some(r=>rules.sameSection(r,draft))){setDraft(null);status('附近的片段已经标记过了。');return;}
    if(records.length>=120){status('标记已满。请先导出并删除不再需要的记录。');return;}
    records.unshift(clean(selected));persist();setDraft(null);renderRecords();
    status(volatile?'仅保存在本次会话；可导出后再离开。':'已保存一条你确认的训练候选。');
  };
  action('clear').onclick=()=>{if(records.length&&window.confirm('删除这台设备上的全部已确认标记？')){records=[];persist();renderRecords();status(volatile?'已清空本次会话；浏览器存储未更新。':'已清空本机标记。');}};
  action('export').onclick=()=>{
    if(!records.length)return;
    const json={schema:'deepmixing.render_feedback.v1',timebase:'mix_timeline_seconds',label:'user_confirmed_like',segments:records.map(clean)};
    const url=URL.createObjectURL(new Blob([JSON.stringify(json,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='deepmixing-listening-feedback.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  };
  read();renderRecords();
})();
