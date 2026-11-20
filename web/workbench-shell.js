'use strict';
// Presentation only: every tool is the original live DOM node, with its
// existing engine, validation, history and event handlers still attached.
let workbenchMode='smart',workbenchTool='mix',workbenchKind='pair';
let workbenchReady=false,workbenchOpener=null;
const workbenchLatestAudio={pair:null,set:null};
const workbenchTools={materials:'素材',separate:'人声与伴奏分离',mix:'调音台',guides:'拍网与调性',sets:'连续编排',delivery:'试听与交付',versions:'版本与工程',stage:'舞台与显示'};

function workbenchPreferredAudio(active){
  const valid=a=>Boolean(a?.getAttribute('src'));
  if(valid(active)&&!active.paused&&!active.ended)return active;
  const candidates=workbenchKind==='set'?[$('set-audio')]:[
    ...(!$('preview-audio').hidden?[$('preview-audio')]:[]),
    ...(!$('downloads').hidden?[$('full-audio')]:[]),$('source-a'),$('source-b')];
  const currentStem=workbenchKind==='pair'&&$('stem-results')?.contains(active);
  const latest=workbenchLatestAudio[workbenchKind];
  if(candidates.includes(latest)&&valid(latest))return latest;
  return (candidates.includes(active)||currentStem)&&valid(active)?active:candidates.find(valid)||null;
}
function workbenchDidRender(id){workbenchLatestAudio[id==='set-audio'?'set':'pair']=$(id);}
function workbenchAudioLabel(audio){
  if(!audio?.getAttribute('src'))return '尚无可播放音频';
  if(audio===$('source-a')||audio===$('source-b')){
    const slot=audio===$('source-a')?'a':'b',track=tracks.find(t=>t.id===$('track-'+slot).value);
    return `原素材 ${slot.toUpperCase()}${track?' · '+track.name:''}`;
  }
  if(audio===$('preview-audio'))return '双轨 · 当前试听';
  if(audio===$('full-audio'))return '双轨 · 完整成品';
  if(audio===$('set-audio'))return '串烧 · '+(audio.dataset.outputKind==='full'?'完整成品':'交接试听');
  return audio.getAttribute('aria-label')||'素材试听';
}
function refreshWorkbenchPlayback(audio){
  if(!workbenchReady)return;
  const state=!audio?'':audio.error?'播放失败 · ':audio.ended?'播放结束 · ':audio.paused?'待播放 · ':'正在播放 · ';
  const text=state+workbenchAudioLabel(audio),label=$('shell-player-label');
  if(label.textContent!==text){label.textContent=text;label.title=text;}
}
function setWorkbenchMode(mode){
  if(!['smart','pro'].includes(mode))return;
  workbenchMode=mode;
  if($('shell-drawer').open)$('shell-drawer').close();
  document.body.dataset.workbenchMode=mode;
  document.body.classList.toggle('shell-mode-smart',mode==='smart');
  document.body.classList.toggle('shell-mode-pro',mode==='pro');
  $('shell-smart').hidden=mode!=='smart';$('shell-pro').hidden=mode!=='pro';
  $('shell-mode-smart').setAttribute('aria-pressed',String(mode==='smart'));
  $('shell-mode-pro').setAttribute('aria-pressed',String(mode==='pro'));
  (mode==='pro'?$('shell-pro-host'):$('shell-drawer-body')).append($('shell-tool-host'));
  showWorkbenchPanel(workbenchTool);
  refreshWorkbenchShell();
}
function showWorkbenchPanel(tool){
  for(const key of Object.keys(workbenchTools))$('shell-panel-'+key).hidden=key!==tool;
  for(const b of document.querySelectorAll('[data-tool]')){
    b.classList.toggle('active',b.dataset.tool===tool);
    b.setAttribute('aria-pressed',String(b.dataset.tool===tool));
  }
  $('shell-drawer-title').textContent=workbenchTools[tool];
}
function openWorkbenchTool(tool,opener=null){
  if(!(tool in workbenchTools))return;
  workbenchTool=tool;workbenchOpener=opener;
  if(tool==='sets')workbenchKind='set';else if(['mix','guides','separate'].includes(tool))workbenchKind='pair';
  showWorkbenchPanel(tool);
  if(workbenchMode==='smart'){
    $('shell-drawer-body').append($('shell-tool-host'));
    if(!$('shell-drawer').open)$('shell-drawer').showModal();
  }else $('shell-pro-host').append($('shell-tool-host'));
  refreshWorkbenchShell();
}
function closeWorkbenchTool(){
  if($('shell-drawer').open)$('shell-drawer').close();
  if(workbenchOpener&&!workbenchOpener.disabled)workbenchOpener.focus();
}
function refreshWorkbenchShell(){
  if(!workbenchReady)return;
  const isSet=workbenchKind==='set',plan=isSet?setPlan:project;
  $('shell-work').value=workbenchKind;
  $('shell-project-name').textContent=plan?.title||'未命名的灵感';
  const approved=isSet?setApproved:approvedPreview;
  const full=isSet?$('set-audio').dataset.outputKind==='full'&&Boolean($('set-audio').getAttribute('src')):!$('downloads').hidden&&Boolean($('full-audio').getAttribute('src'));
  const version=`${isSet?'串烧':'双轨'} · ${!plan?'尚未建立工程':!approved?'待生成试听':full?'完整成品已生成':'试听就绪 · 可导出'}${busy?' · 处理中':''}`;
  $('shell-version-status').textContent=version;$('shell-version-status').title=version;
  $('shell-version-status').classList.toggle('needs-preview',Boolean(plan&&!approved));
  // The live transport knows which actual player is active, including one
  // still playing while the user edits a different work type.
  if(typeof refreshLiveControls==='function')refreshLiveControls();
  $('shell-preview').disabled=busy||!(isSet?setPlan:project);
  $('shell-export').disabled=busy||!(isSet?setApproved:approvedPreview);
  $('shell-pair-history').textContent=`可撤销 ${undoStack.length} 步 · 可重做 ${redoStack.length} 步${project?' · 当前作品：'+project.title:' · 尚无双轨工程'}`;
  $('shell-set-history').textContent=`可撤销 ${setUndo.length} 步 · 可重做 ${setRedo.length} 步${setPlan?' · 当前作品：'+setPlan.title:' · 尚无串烧工程'}`;
  $('shell-guides-empty').hidden=Boolean(project);
  $('music-guides').hidden=!project;
  $('shell-delivery-empty').hidden=Boolean(project);
  $('shell-engine').textContent=$('connection').textContent||'连接本地引擎…';
  $('shell-task-message').textContent=$('status').textContent;
  $('shell-task-message').classList.toggle('is-error',document.querySelector('.statusbar').classList?.contains?.('error')||false);
  $('shell-task-line')?.classList?.toggle('busy',busy);
  for(const slot of ['a','b']){
    const label=$('shell-stem-source-'+slot);
    if(label){const track=tracks.find(t=>t.id===$('track-'+slot).value);label.textContent=`${slot.toUpperCase()} · ${track?.name||'先在素材中选择音乐'}`;}
  }
}

(()=>{
  const mount=(id,...nodes)=>{for(const n of nodes)if(n)$(id).append(n);};
  for(const [key,label] of Object.entries(workbenchTools)){
    const panel=$('shell-panel-'+key);panel.tabIndex=0;panel.setAttribute('aria-label',label);
  }
  mount('shell-panel-materials',document.querySelector('.import-hub'),$('legacy-sources'));
  mount('shell-panel-separate',document.querySelector('.stem-workshop'));
  for(const button of document.querySelectorAll('.stem-start')){
    const card=node('div',undefined,'shell-stem-source'),label=node('p');
    label.id='shell-stem-source-'+button.dataset.stemSlot;
    card.append(label,button);mount('shell-panel-separate',card);
  }
  mount('shell-panel-mix',$('legacy-inspiration'),$('recommendation-section'),$('editor'));
  mount('shell-panel-guides',$('music-guides'));
  mount('shell-panel-sets',$('continuous'));
  mount('shell-panel-delivery',$('finish'));
  mount('shell-pair-history-mount',document.querySelector('#editor .history'));
  mount('shell-set-history-mount',document.querySelector('#continuous .guide-actions'));
  mount('shell-stage-mount',$('live-stage'));
  mount('shell-transport-mount',document.querySelector('.live-transport'));
  mount('shell-stage-settings',$('live-stage-toggle'),document.querySelector('.live-rail'),document.querySelector('.live-note'));
  mount('shell-avatar-mount',$('avatar-entry'));
  for(const b of document.querySelectorAll('[data-tool]'))b.addEventListener('click',()=>openWorkbenchTool(b.dataset.tool,b));
  $('shell-mode-smart').addEventListener('click',()=>setWorkbenchMode('smart'));
  $('shell-mode-pro').addEventListener('click',()=>setWorkbenchMode('pro'));
  $('shell-home').addEventListener('click',()=>{if(workbenchMode==='smart')closeWorkbenchTool();else openWorkbenchTool('mix',$('shell-home'));});
  $('shell-chat-return').addEventListener('click',()=>setWorkbenchMode('smart'));
  $('shell-drawer-close').addEventListener('click',closeWorkbenchTool);
  $('shell-drawer').addEventListener('cancel',event=>{event.preventDefault();closeWorkbenchTool();});
  $('shell-drawer').addEventListener('close',()=>{if(workbenchOpener&&!workbenchOpener.disabled)workbenchOpener.focus();});
  $('shell-work').addEventListener('change',()=>{workbenchKind=$('shell-work').value==='set'?'set':'pair';refreshWorkbenchShell();});
  $('shell-preview').addEventListener('click',()=>$(workbenchKind==='set'?'set-preview':'preview').click());
  $('shell-export').addEventListener('click',()=>{
    $(workbenchKind==='set'?'set-export':'export').click();
    openWorkbenchTool(workbenchKind==='set'?'sets':'delivery',$('shell-export'));
  });
  const draft=$('shell-draft');
  try{draft.value=sessionStorage.getItem('deepmixing-layout-draft')||'';}catch{}
  draft.addEventListener('input',()=>{
    try{sessionStorage.setItem('deepmixing-layout-draft',draft.value);$('shell-draft-status').textContent='灵感已暂存 · 尚未发送';}
    catch{$('shell-draft-status').textContent='浏览器未允许暂存，请保留当前页面';}
  });
  for(const b of document.querySelectorAll('[data-idea]'))b.addEventListener('click',()=>{
    draft.value=b.dataset.idea;draft.dispatchEvent(new Event('input'));draft.focus();
  });
  $('shell-use-draft').addEventListener('click',()=>{
    const text=draft.value.trim();
    if(text){$('prompt').value=text;if(project)updateProject(p=>{p.prompt=text});}
    openWorkbenchTool('mix',$('shell-use-draft'));
  });
  // Observe actual operation progress, not generated chat responses. No model
  // calls, fake typing indicators or automatic parameter edits in this stage.
  if(typeof MutationObserver!=='undefined'){
    const observer=new MutationObserver(refreshWorkbenchShell);
    for(const id of ['status','connection'])observer.observe($(id),{childList:true,characterData:true,subtree:true});
    const candidates=new MutationObserver(()=>{
      if(!$('recommendation-section').hidden&&workbenchMode==='smart'&&workbenchTool==='mix')showWorkbenchPanel('mix');
    });candidates.observe($('recommendation-section'),{attributes:true,attributeFilter:['hidden']});
  }
  workbenchReady=true;$('shell-send').disabled=true;
  setWorkbenchMode('smart');
})();
