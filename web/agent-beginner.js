'use strict';
// Beginner interactions use the same inventory and execution layer as the studio.
let agentSelectedTrackIds=[];
let agentGuideOpener=null,agentMaterialsOpener=null;
function agentLocalCommand(text){
  const clean=text.trim().replace(/[。！？!?\s]+$/,'');
  if(/^(?:帮助|使用指引|新手指南|怎么用|我该怎么开始|如何开始|怎么开始)$/.test(clean))return 'help';
  // Only a request beginning with an explicit automation command runs locally.
  // Questions, negations and ordinary conversation still go to the normal flow.
  const automatic=/^(?:(?:请)?帮我|请|开始|我要|我想要|我想|直接)?\s*(全自动混音|自动混音|全自动串烧|自动串烧)(?=$|[，,:：\s吧把将这选并])/u.exec(clean);
  if(automatic&&!/(?:是什么|什么意思|怎么|如何|[？?])/.test(text))return automatic[1].includes('串烧')?'autoset':'automix';
  return ({'播放':'play','播放试听':'play','暂停':'pause','停止播放':'pause','撤销':'undo','撤销上一版':'undo','重做':'redo'})[clean]||null;
}
function agentRememberOpener(value,key){
  if(!(value&&typeof value.focus==='function'&&value.isConnected))return;
  if(key==='agentGuideOpener')agentGuideOpener=value;else agentMaterialsOpener=value;
}
function agentRestoreFocus(key){
  const opener=key==='agentGuideOpener'?agentGuideOpener:agentMaterialsOpener;
  if(opener&&opener.isConnected&&!opener.disabled)requestAnimationFrame(()=>opener.focus());
  if(key==='agentGuideOpener')agentGuideOpener=null;else agentMaterialsOpener=null;
}
function agentOpenGuide(opener){
  agentRememberOpener(opener,'agentGuideOpener');
  const guide=$('agent-guide');if(!guide.open)guide.showModal();
}
function agentFillIdea(text){
  const draft=$('shell-draft');draft.value=text;draft.dispatchEvent(new Event('input'));draft.focus();
}
function agentRefreshInventory(){
  const root=$('agent-material-list');if(!root)return;root.replaceChildren();
  agentSelectedTrackIds=agentSelectedTrackIds.filter(id=>tracks.some(t=>t.id===id));
  $('agent-material-count').textContent=agentSelectedTrackIds.length?`已选 ${agentSelectedTrackIds.length} 份用于本轮灵感`:`素材库 ${tracks.length} 份 · 可以先选本轮想混的音乐`;
  for(const t of [...tracks].reverse()){
    const row=node('label',undefined,'agent-material-row'),box=node('input');box.type='checkbox';box.checked=agentSelectedTrackIds.includes(t.id);
    box.setAttribute('aria-label','本轮使用 '+t.name);
    box.addEventListener('change',()=>{
      if(box.checked){if(agentSelectedTrackIds.length>=8){box.checked=false;$('agent-import-status').textContent='每轮最多选8份；可取消旧选择。';return;}agentSelectedTrackIds.push(t.id);}
      else agentSelectedTrackIds=agentSelectedTrackIds.filter(id=>id!==t.id);
      agentRefreshInventory();
    });
    const role=t.stem_role==='vocal'?'独立人声':t.stem_role==='instrumental'?'独立伴奏':'原音轨';
    row.append(box,node('span',t.name),node('small',`${role} · ${Math.round(t.duration)}秒`));root.append(row);
  }
  $('agent-material-summary').textContent=agentSelectedTrackIds.length?`本轮 ${agentSelectedTrackIds.length} 份素材`:'添加音乐，聊聊你的想法';
}
function agentOpenMaterials(){
  agentRefreshInventory();const dialog=$('agent-materials');if(!dialog.open)dialog.showModal();
}
async function agentImportFiles(files){
  if(busy||(typeof agentPending!=='undefined'&&agentPending))return;
  const items=Array.from(files||[]);if(!items.length)return;
  setBusy(true);const successes=[],failures=[];
  try{
    for(let i=0;i<items.length;i++){
      const file=items[i];$('agent-import-status').textContent=`导入 ${i+1}/${items.length}：${file.name}`;
      try{
        if(file.size>80*1024*1024)throw Error('超过80 MiB');
        const t=await api('/api/tracks?name='+encodeURIComponent(file.name),file,true);successes.push(t);
      }catch(e){failures.push(file.name+'：'+e.message);}
    }
    if(successes.length){
      const inventory=await api('/api/tracks');tracks=inventory;fillSources();
      agentSelectedTrackIds=[...new Set([...agentSelectedTrackIds,...successes.map(t=>t.id)])].slice(-8);
    }
    agentRefreshInventory();
    const summary=`已导入 ${successes.length} 份${failures.length?`，${failures.length} 份未成功`:''}。工程与调音保留。`;
    $('agent-import-status').textContent=summary+(failures.length?' '+failures.join('；'):'');
    if(typeof agentMessage==='function')agentMessage('system',summary+(successes.length?' 选好 2–8 份素材后，发送“全自动混音”或“自动串烧”，即可在本地自动完成并导出。':''));
  }catch(e){$('agent-import-status').textContent='素材可能已保存，但读取列表未完成：'+e.message+'。请重新打开素材确认，不必重复上传。';}
  finally{$('agent-files').value='';setBusy(false);}
}
function agentStemProgress(run,task){
  if(!run.article){run.article=agentMessage('system','正在准备人声和伴奏，原曲保留。');run.progress=node('p',undefined,'agent-outcome');run.article.append(run.progress);
    const cancel=node('button','取消后续分轨','quiet');cancel.addEventListener('click',async()=>{
      if(run.status!=='running')return;run.status='cancelled';cancel.disabled=true;
      try{if(run.current)await api('/api/separations/'+run.current+'/cancel',{});}
      catch(e){agentMessage('system','取消请求未确认：'+e.message+'。当前任务结果仍可在分轨面板查看。');}
    });run.article.append(cancel);run.cancel=cancel;
  }
  const stage=typeof stemStages!=='undefined'?stemStages[task?.stage]||task?.stage:'';
  run.progress.textContent=`${Math.min(run.tasks.length+1,run.ids.length)}/${run.ids.length} · ${task?.source_name||'音频'} · ${stage||'处理中'}${Number.isFinite(task?.progress)?' '+Math.round(task.progress*100)+'%':''}`;
}
function agentStemComplete(run,error){
  if(run.cancel)run.cancel.disabled=true;
  const article=run.article||agentMessage('system','分轨任务结果');
  if(run.progress)run.progress.textContent=run.status==='cancelled'?'已取消接续；原曲和已完成的分轨保留。':error?'任务已停止：'+error.message:run.status==='stale'?'分轨已保存；工程有更新，请基于当前版本继续。':'人声与伴奏已就绪。试听后可以接着完成原来的灵感。';
  for(const task of run.tasks){
    const detail=node('details');detail.append(node('summary',task.source_name+' · 人声与伴奏'));
    for(const [role,label] of [['vocal','人声'],['instrumental','伴奏']]){
      const track=task.stems?.[role];if(!track)continue;
      detail.append(node('p',label));const audio=node('audio');audio.controls=true;audio.preload='none';audio.src='/media/'+track.id;audio.setAttribute('aria-label',task.source_name+' '+label);detail.append(audio);
      const download=node('a','下载'+label+' ↓');download.href='/media/'+track.id+'?download=1';download.download=track.name;detail.append(download);
    }article.append(detail);
  }
  agentRefreshInventory();
  if(run.status==='succeeded'&&run.intent){
    const button=node('button','继续完成刚才的灵感 ↗','agent-continue');button.addEventListener('click',()=>{
      if(run.signature!==agentEngineeringSignature())return agentMessage('system','工程有新修改，已停止旧任务接续。请告诉我当前想怎么混。');
      if(agentPending||busy)return;
      agentFillIdea('继续刚才的灵感：'+run.intent+'。分轨已经真实完成，请读取现有独立人声和伴奏，继续编排。');
      button.disabled=true;sendAgentMessage();
    });article.append(button);
  }
  if(typeof agentHistory!=='undefined')agentHistory.push({role:'assistant',content:run.status==='cancelled'?'用户已取消分轨接续，不自动继续。':error?'分轨任务未完成：'+error.message:'分轨任务真实完成，已有独立人声和伴奏。尚未自动建立叠搭或试听，需继续用户原意。'});
}
for(const id of ['agent-guide-open','agent-guide-nav'])$(id)?.addEventListener('click',event=>agentOpenGuide(event.currentTarget));
$('agent-guide-close')?.addEventListener('click',()=>$('agent-guide').close());
$('agent-material-close')?.addEventListener('click',()=>$('agent-materials').close());
$('agent-guide')?.addEventListener('close',()=>agentRestoreFocus('agentGuideOpener'));
$('agent-materials')?.addEventListener('close',()=>agentRestoreFocus('agentMaterialsOpener'));
$('shell-add')?.addEventListener('click',event=>{agentRememberOpener(event.currentTarget,'agentMaterialsOpener');agentOpenMaterials();});
$('shell-materials-button')?.addEventListener('click',event=>typeof workbenchMode!=='undefined'&&workbenchMode==='pro'?openWorkbenchTool('materials',event.currentTarget):(agentRememberOpener(event.currentTarget,'agentMaterialsOpener'),agentOpenMaterials()));
$('agent-files')?.addEventListener('change',event=>agentImportFiles(event.target.files));
$('agent-import-advanced')?.addEventListener('click',()=>{$('agent-materials').close();openWorkbenchTool('materials',$('shell-add'));});
for(const b of document.querySelectorAll('[data-guide-idea]'))b.addEventListener('click',()=>{$('agent-guide').close();agentFillIdea(b.dataset.guideIdea);});
