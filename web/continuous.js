'use strict';
let setPlan=null,setUndo=[],setRedo=[],setApproved=null,setPending=[];
const setSection=node('section',undefined,'continuous');setSection.id='continuous';
setSection.append(node('p','KEEP THE FEELING GOING','eyebrow'),node('h2','把灵感，接成一整场。'),node('p','2–8段连续串烧。先试听每个交接，再完整导出；与双轨混音分别保存工程。','hint'));
const setSetup=node('div',undefined,'set-setup'),setSource=node('select');setSource.id='set-source';setSource.setAttribute('aria-label','串烧素材');
const setAdd=node('button','加入一段','quiet');setAdd.id='set-add';
const setStyle=node('select');setStyle.id='set-style';setStyle.setAttribute('aria-label','串烧编排起点');
for(const [v,t] of [['journey','连贯旅程 · 顺滑交接'],['rising','能量递进 · 相对音量递增'],['echo','回声转场 · 前段末尾回声']])setStyle.add(new Option(t,v));setStyle.value='journey';
const setRecommend=node('button','给这组音乐一个编排起点 ↗','secondary');setRecommend.id='set-recommend';
const setFromPair=node('button','沿用当前A/B选段','quiet');setFromPair.id='set-from-pair';
setSetup.append(setSource,setAdd,setStyle,setRecommend,setFromPair);setSection.append(setSetup);
const setPendingNote=node('p','从上方已导入素材中加入片段，也可沿用当前A/B的选段和调音。','hint');setPendingNote.id='set-pending';setSection.append(setPendingNote);
const setHistory=node('div',undefined,'guide-actions'),su=node('button','↶ 撤销串烧','quiet'),sr=node('button','↷ 重做串烧','quiet');su.id='set-undo';sr.id='set-redo';
const restoreLabel=node('label','载入串烧工程','quiet import-label');restoreLabel.setAttribute('for','set-import');const restoreInput=node('input');restoreInput.id='set-import';restoreInput.type='file';restoreInput.accept='.json,application/json';restoreLabel.append(restoreInput);setHistory.append(su,sr,restoreLabel);setSection.append(setHistory);
const setEditor=node('div');setEditor.id='set-editor';setSection.append(setEditor);
const setOutput=node('div',undefined,'set-output'),setPreview=node('button','试听选中的交接','primary'),setExport=node('button','确认串烧 · 完整导出','secondary');setPreview.id='set-preview';setExport.id='set-export';
const setAudio=node('audio');setAudio.id='set-audio';setAudio.controls=true;setAudio.preload='none';setAudio.setAttribute('aria-label','连续串烧试听');
const setNote=node('p','规则编排不会自动理解全曲情绪、识别副歌或保证和声匹配。','hint');setNote.id='set-note';
const setDownloads=node('div',undefined,'download-links');setDownloads.id='set-downloads';
const setCaption=node('p',undefined,'subtitle-strip');setCaption.id='set-caption';setCaption.hidden=true;
const setChapters=node('details',undefined,'set-chapters');setChapters.id='set-chapters';setChapters.hidden=true;
setOutput.append(setPreview,setExport,setCaption,setAudio,setNote,setChapters,setDownloads);setSection.append(setOutput);$('editor').after(setSection);
const setCaptions=[setCaption,$('set-stage-caption')].filter(Boolean);
let setCaptionTimeline=null,setCaptionActive=false,setChapterTimer=null,setCurrentChapter=null;
function refreshSetCaption(){
  let current=null;
  if(setCaptionActive&&setCaptionTimeline&&!setAudio.ended){
    const {clips,offset}=setCaptionTimeline,time=setAudio.currentTime+offset;
    // During a crossfade, label the incoming song from its first audible segment.
    for(const clip of clips){if(clip.start>time)break;if(time<clip.end)current=clip;}
  }
  const title=current?.title??'';
  for(const caption of setCaptions){
    if(caption.textContent!==title)caption.textContent=title;
    caption.hidden=!title;
  }
  for(const clip of setCaptionTimeline?.clips??[]){
    if(clip===current&&setCurrentChapter!==clip){clip.control.setAttribute('aria-current','true');}
    else if(clip!==current&&setCurrentChapter===clip){clip.control.removeAttribute('aria-current');}
  }
  setCurrentChapter=current;
}
function stopSetChapterTimer(){
  if(setChapterTimer!==null){clearTimeout(setChapterTimer);setChapterTimer=null;}
}
function updateSetChapterClock(reschedule=false){
  refreshSetCaption();
  if(reschedule)stopSetChapterTimer();
  if(setChapterTimer!==null||!setCaptionActive||!setCaptionTimeline||setAudio.paused||setAudio.ended||setAudio.seeking||setAudio.readyState<3||!(setAudio.playbackRate>0))return;
  const {offset,duration,clips}=setCaptionTimeline,time=setAudio.currentTime+offset;
  const next=clips.find(clip=>clip.start>time)?.start;
  if(next===undefined||next>=offset+duration)return;
  // The media clock remains the source of truth after buffering or a rate change.
  setChapterTimer=setTimeout(()=>{setChapterTimer=null;updateSetChapterClock();},Math.max(16,(next-time)*1000/setAudio.playbackRate));
}
function setChapterTime(seconds){
  const hundredths=Math.round(seconds*100);
  return `${String(Math.floor(hundredths/6000)).padStart(2,'0')}:${String(Math.floor(hundredths/100)%60).padStart(2,'0')}.${String(hundredths%100).padStart(2,'0')}`;
}
function drawSetChapters(preview){
  setChapters.replaceChildren();setChapters.hidden=!setCaptionTimeline;
  if(!setCaptionTimeline)return;
  const {clips,offset,duration}=setCaptionTimeline;
  setChapters.append(node('summary',`曲目章节 · ${clips.length} 段`),node('p',preview?'下方时间以完整版为准。试听只可跳转到当前窗口内的章节。':'按入场时间列出曲目，点击时间跳转播放。','hint'));
  const list=node('ol');
  for(const clip of clips){
    const row=node('li'),label=`${setChapterTime(clip.start)} · ${clip.title}`;
    const available=clip.start<offset+duration&&clip.end>offset&&clip.playFrom<offset+duration;
    const control=node('button',label,'quiet');clip.control=control;control.type='button';control.disabled=!available;
    if(!available)control.dataset.unavailable='true';
    if(available)control.addEventListener('click',async()=>{
      try{
        // Setting currentTime before metadata loads sets the initial playback position.
        setAudio.currentTime=clip.playFrom-offset;
        await setAudio.play();
        updateSetChapterClock(true);
      }catch(e){if(e.name!=='AbortError')status('暂时无法播放这一章，请重试。',true);}
    });
    row.append(control);
    if(!available)row.append(node('small','不在当前试听'));
    else if(clip.start<offset)row.append(node('small','从试听起点播放'));
    list.append(row);
  }
  setChapters.append(list);
  if(!preview){
    const text=clips.map(clip=>`${setChapterTime(clip.start)}  ${clip.title.replace(/[\r\n]+/g,' ')}`).join('\r\n')+'\r\n';
    const link=node('a','下载曲目清单 TXT ↓');link.id='set-chapter-download';link.download='chapters.txt';
    link.href='data:text/plain;charset=utf-8,'+encodeURIComponent(text);setDownloads.append(link);
  }
}
function invalidateSetPlayback(){
  setApproved=null;setCaptionActive=false;stopSetChapterTimer();setCaptionTimeline=null;refreshSetCaption();drawSetChapters(false);
  setAudio.pause();setAudio.removeAttribute('src');setDownloads.replaceChildren();
}
setAudio.addEventListener('play',()=>{setCaptionActive=true;updateSetChapterClock(true);});
for(const event of ['timeupdate','playing','pause','waiting','ratechange','seeking','seeked','ended','error','emptied']){
  setAudio.addEventListener(event,()=>{
    if(event==='error'||event==='ended')setCaptionActive=false;
    if(['pause','waiting','seeking','ended','error','emptied'].includes(event)){stopSetChapterTimer();refreshSetCaption();return;}
    updateSetChapterClock(event!=='timeupdate');
  });
}
document.addEventListener('play',event=>{
  const audio=event.target;
  if(audio!==setAudio&&audio.tagName==='AUDIO'&&audio.dataset.dialogueVoice!=='true'){
    setCaptionActive=false;stopSetChapterTimer();refreshSetCaption();
  }
},true);
function setEdit(fn){
  if(busy)return;
  setUndo.push({plan:clone(setPlan),pending:[...setPending]});if(setUndo.length>50)setUndo.shift();setRedo.length=0;
  fn();invalidateSetPlayback();drawSet();
}
function refreshSetControls(){
  const chosen=setSource.value,ids=tracks.map(t=>t.id).join(',');
  if(setSource.dataset.ids!==ids){setSource.replaceChildren(new Option('选择本机素材',''));for(const t of tracks)setSource.add(new Option(t.name,t.id));setSource.value=tracks.some(t=>t.id===chosen)?chosen:'';setSource.dataset.ids=ids;}
  for(const e of setSection.querySelectorAll?.('input,select,button')||[])e.disabled=busy;
  for(const control of setChapters.querySelectorAll?.('button')||[])control.disabled=busy||control.dataset.unavailable==='true';
  setPreview.disabled=busy||!setPlan;setExport.disabled=busy||!setApproved;
  su.disabled=busy||!setUndo.length;sr.disabled=busy||!setRedo.length;setAdd.disabled=busy||!tracks.length||(setPlan?setPlan.clips.length:setPending.length)>=8;
  restoreInput.disabled=busy;
  setRecommend.disabled=busy||(setPlan?setPlan.clips.length:setPending.length)<2;setFromPair.disabled=busy||!project;
  if(typeof refreshWorkbenchShell==='function')refreshWorkbenchShell();
}
function setField(label,value,id,onchange,type='number'){
  const l=node('label',label),input=node('input');input.id=id;input.type=type;input.value=value??'';if(type==='number')input.step='.001';
  input.addEventListener('change',()=>{
    const v=type==='number'?(input.value===''?null:Number(input.value)):input.value;
    if(type==='number'&&v!==null&&!Number.isFinite(v)){status('请输入有限数字。',true);return;}
    setEdit(()=>onchange(v));
  });l.append(input);return l;
}
function setJoinReason(plan,i){
  const j=plan.joins[i],locked=plan.clips[i].preserve_original;
  const method=j.kind==='echo'?'前段末尾加入半拍回声，给下一段留出进入空间':j.kind==='cut'?'直接切入制造反差，边缘用5ms淡化减少爆音':'等功率交接，减少交接时的音量塌陷';
  const overlap=Number.isFinite(j.seconds)?j.seconds.toFixed(2)+'秒':'待确认';
  return `交接依据：${method}；重叠${overlap}。${locked?'前段原貌锁已保留，不加回声。':''}${plan.target_bpm?`沿用你设定的目标${plan.target_bpm} BPM。`:'保持各段原速。'}沿用当前移调；是否合拍、和声兼容与主唱冲突仍需试听。`;
}
function drawSet(){
  setEditor.replaceChildren();setPendingNote.textContent=setPlan?`当前 ${setPlan.clips.length} 段 · 顺序与每个交接均可调整`:`待编排 ${setPending.length} 段：${setPending.map(k=>tracks.find(t=>t.id===k)?.name||k).join(' → ')}`;
  if(!setPlan){refreshSetControls();return;}
  const top=node('div',undefined,'set-fields');top.append(setField('串烧名称',setPlan.title,'set-title',v=>setPlan.title=v,'text'),setField('整体目标BPM · 留空保持原速',setPlan.target_bpm,'set-target',v=>setPlan.target_bpm=v));setEditor.append(top);
  let total=0,valid=true;
  setPlan.clips.forEach((c,i)=>{
    const row=node('article',undefined,'set-clip'),name=tracks.find(t=>t.id===c.track_id)?.name||'缺少素材';row.append(node('h3',`${String(i+1).padStart(2,'0')} · ${name}`));
    const actions=node('div',undefined,'guide-actions');
    for(const [key,label,offset] of [['up','↑ 前移',-1],['down','↓ 后移',1],['remove','移除这一段',0]]){
      const b=node('button',label,'quiet');b.id=`set-clip-${i}-${key}`;b.addEventListener('click',()=>{
        if(offset&&(i+offset<0||i+offset>=setPlan.clips.length)||!offset&&setPlan.clips.length<=2)return;
        setEdit(()=>{if(offset)[setPlan.clips[i],setPlan.clips[i+offset]]=[setPlan.clips[i+offset],setPlan.clips[i]];else{setPlan.clips.splice(i,1);setPlan.joins.splice(Math.min(i,setPlan.joins.length-1),1);setPlan.preview_join=Math.min(setPlan.preview_join,setPlan.joins.length-1);}});
      });actions.append(b);
    }row.append(actions);
    const fields=node('div',undefined,'set-fields');
    for(const [key,label] of [['start','片段起点 / 秒'],['end','片段终点 / 秒'],['bpm','已确认源BPM'],['gain_db','音量 / dB'],['semitones','移调 / 半音']])fields.append(setField(label,c[key],`set-clip-${i}-${key}`,v=>setPlan.clips[i][key]=v));
    const lock=node('label',undefined,'lock'),box=node('input');box.type='checkbox';box.checked=c.preserve_original;box.id=`set-clip-${i}-lock`;box.addEventListener('change',()=>setEdit(()=>{setPlan.clips[i].preserve_original=box.checked}));lock.append(box,node('span','保持原貌 · 禁止变速、移调和本段回声'));row.append(fields,lock);
    const speed=setPlan.target_bpm&&c.bpm?setPlan.target_bpm/c.bpm:1,length=(c.end-c.start)/speed;
    if(!Number.isFinite(length)||length<=0)valid=false;else total+=length;
    setEditor.append(row);
    if(i<setPlan.joins.length){
      const j=setPlan.joins[i],join=node('div',undefined,'set-join'),select=node('select');select.id=`set-join-${i}-kind`;select.setAttribute('aria-label',`第${i+1}个交接方式`);
      if(!Number.isFinite(j.seconds)||j.seconds<0)valid=false;
      for(const [v,t] of [['smooth','顺滑交接'],['cut','直接切入'],['echo','尾段回声交接']])select.add(new Option(t,v));select.value=j.kind;
      select.addEventListener('change',()=>setEdit(()=>{j.kind=select.value;if(j.kind==='cut')j.seconds=0;}));
      const radio=node('button',setPlan.preview_join===i?'◉ 正在编辑的试听交接':'试听这一处','quiet');radio.id=`set-join-${i}-select`;radio.addEventListener('click',()=>setEdit(()=>{setPlan.preview_join=i}));
      join.append(node('strong',`交接 ${i+1} → ${i+2}`),select,setField('重叠时长 / 秒',j.seconds,`set-join-${i}-seconds`,v=>j.seconds=v),radio,node('p',setJoinReason(setPlan,i),'hint'));setEditor.append(join);total-=j.seconds;
    }
  });
  const summary=node('p',valid?`计划时长约 ${Math.max(0,total).toFixed(1)} 秒 · 所有选段都会进入完整导出。`:'请修正选段和BPM后生成试听。','hint');setEditor.append(summary);refreshSetControls();
}
setAdd.addEventListener('click',()=>{const t=tracks.find(t=>t.id===setSource.value);if(!t||busy)return;setEdit(()=>{
  if(setPlan){if(setPlan.clips.length>=8)return;setPlan.clips.push({track_id:t.id,role:'full_mix',start:0,end:t.duration,bpm:t.analysis?.bpm_estimate??null,gain_db:-7,bass_db:0,semitones:0,preserve_original:false});setPlan.joins.push({kind:'smooth',seconds:0});}
  else if(setPending.length<8)setPending.push(t.id);
});});
function adaptSetRecommendation(incoming,old){
  const q=clone(incoming);if(!old)return q;q.target_bpm=old.target_bpm;
  q.clips.forEach((c,i)=>{if(old.clips[i]?.track_id===c.track_id){const previous=old.clips[i];Object.assign(c,previous);if(!previous.preserve_original)c.gain_db=incoming.clips[i].gain_db;}});
  const lengths=q.clips.map(c=>(c.end-c.start)/(q.target_bpm&&c.bpm?q.target_bpm/c.bpm:1));
  q.joins.forEach((j,i)=>{if(q.clips[i].preserve_original&&j.kind==='echo')j.kind='smooth';j.seconds=Math.max(0,Math.min(j.seconds,lengths[i]*.2,lengths[i+1]*.2));});
  for(let i=1;i<lengths.length-1;i++){
    // Leave a microsecond margin so binary floating-point addition cannot
    // cross the backend's strict minimum-unconsumed-material boundary.
    const budget=Math.max(0,lengths[i]-.250001),sum=q.joins[i-1].seconds+q.joins[i].seconds;
    if(sum>budget){const ratio=budget/sum;q.joins[i-1].seconds*=ratio;q.joins[i].seconds*=ratio;}
  }
  for(const j of q.joins)if(j.seconds>0&&j.seconds<1/44100)j.seconds=0;
  return q;
}
setRecommend.addEventListener('click',async()=>{
  if(busy)return;const ids=setPlan?setPlan.clips.map(c=>c.track_id):setPending,old=clone(setPlan);setBusy(true);
  try{const result=await api('/api/sets/recommendations',{track_ids:ids,style:setStyle.value});setBusy(false);setEdit(()=>{
    setPlan=adaptSetRecommendation(result.project,old);
  });status('串烧起点已准备好。已保留现有选段、移调和目标速度；交接依据见每处说明，先试听再导出。');}
  catch(e){status(e.message,true);}finally{setBusy(false);}
});
setFromPair.addEventListener('click',()=>{if(!project||busy)return;setEdit(()=>{setPlan={kind:'continuous_set',version:1,title:project.title+' · 串烧',target_bpm:project.target_bpm,preview_join:0,clips:[clone(project.a),clone(project.b)],joins:[{kind:'smooth',seconds:project.mode==='transition'?project.transition_seconds:0}]};setPending=[];});status('已沿用A/B选段和调音；连续串烧按先后接歌，可再添加后续音乐。');});
for(const [b,from,to] of [[su,setUndo,setRedo],[sr,setRedo,setUndo]])b.addEventListener('click',()=>{if(busy||!from.length)return;to.push({plan:clone(setPlan),pending:[...setPending]});const prior=from.pop();setPlan=prior.plan;setPending=prior.pending;invalidateSetPlayback();drawSet();});
async function restoreSet(input){
  if(busy)return;const file=input.files[0];if(!file)return;
  try{
    if(file.size>256*1024)throw Error('串烧工程须在256KiB以内。');const bundle=JSON.parse(await file.text());
    if(bundle.format!=='deepmixing-set-v1'||!Array.isArray(bundle.project?.clips)||bundle.project.clips.length<2||bundle.project.clips.length>8||!Array.isArray(bundle.assets)||!Array.isArray(bundle.project.joins)||bundle.project.joins.length!==bundle.project.clips.length-1)throw Error('请选择deepmixing连续串烧工程。');
    const q=remapProjectAssets(bundle,tracks);
    setEdit(()=>{setPlan=q;setPending=[]});status('串烧工程已恢复。顺序和调音已保留，重新试听后可导出。');
  }catch(e){status(e.message,true);}finally{input.value='';}
}
restoreInput.addEventListener('change',()=>restoreSet(restoreInput));
async function renderSet(preview,requestId=undefined){
  if(busy||!setPlan)return {ok:false,error:'串烧未准备好或正在处理'};const sent=clone(setPlan),signature=JSON.stringify(sent);setBusy(true);status(preview?'正在渲染选中的串烧交接。':'正在完整导出所有串烧片段。');
  let task,job;
  try{
    task=await api('/api/sets/renders',{project:sent,preview,approved_preview_id:setApproved,request_id:requestId});const deadline=Date.now()+20*60*1000;
    while(Date.now()<deadline){job=await api('/api/jobs/'+task.id);if(job.status==='failed')throw Error(job.error);if(job.status==='succeeded')break;await new Promise(r=>setTimeout(r,700));}
    if(!job||job.status!=='succeeded')throw Error('等待超时，请检查任务状态后再试。');if(signature!==JSON.stringify(setPlan))throw Error('串烧工程已变化，请重新生成当前版本试听。');
    const offset=job.result.preview_start_seconds;
    const layout=job.result.layout?.clips;
    const duration=job.result.duration;
    const firstAtOffset=layout?.reduce((last,clip,i)=>clip.start<=offset&&offset<clip.end?i:last,-1);
    setCaptionTimeline=layout?.length===sent.clips.length&&Number.isFinite(offset)&&Number.isFinite(duration)?{
      offset,duration,clips:layout.map(({start,end},i)=>({start,end,
        playFrom:start>=offset?start:i===firstAtOffset?offset:Infinity,
        title:tracks.find(t=>t.id===sent.clips[i].track_id)?.name||`片段 ${i+1}`})),
    }:null;
    setCaptionActive=false;stopSetChapterTimer();
    setAudio.src='/outputs/'+task.id+'/audio.mp3';setAudio.dataset.stageOffset=String(offset);
    setAudio.dataset.outputKind=preview?'preview':'full';
    if(typeof workbenchDidRender==='function')workbenchDidRender('set-audio');
    setDownloads.replaceChildren();
    if(preview){setApproved=task.id;setNote.textContent=`交接${sent.preview_join+1}试听：从作品第${job.result.preview_start_seconds.toFixed(2)}秒开始，时长${job.result.duration.toFixed(2)}秒。确认后完整导出。`;}
    else{
      for(const [file,label] of [['audio.wav','下载串烧WAV'],['audio.mp3','下载串烧MP3'],['project.json','保存串烧工程']]){const a=node('a',label+' ↓');a.href='/outputs/'+task.id+'/'+file+'?download=1';a.download=file;setDownloads.append(a);}
      const q=job.result.quality;setNote.textContent=`完整 ${job.result.duration.toFixed(2)} 秒 · MP3 ${q.mp3.integrated_lufs??'未知'} LUFS / ${q.mp3.true_peak_dbtp??'未知'} dBTP · ${q.status==='machine_checks_passed'?'机器检查通过，仍需试听':'需要复核'}。工程可载入继续调整。`;
    }drawSetChapters(preview);refreshSetCaption();status(preview?'串烧交接试听已完成，可播放比较或选择其他交接。':'连续串烧完整导出成功，所有选段已包含在成品中。');
    return {ok:true,task_id:task.id,preview,result:job.result};
  }catch(e){status(e.message,true);return {ok:false,error:e.message,task_id:task?.id,task_failed:job?.status==='failed'};}finally{setBusy(false);}
}
setPreview.addEventListener('click',()=>renderSet(true));setExport.addEventListener('click',()=>renderSet(false));drawSet();
