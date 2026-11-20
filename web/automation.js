'use strict';
// Loaded after agent-chat.js. Automation uses the local engine and the existing
// shared project/history layer; no model configuration or model API is involved.
const agentAutomationStorageKey='deepmixing-automation-task';
let agentAutomationRun=null;
function agentAutomationRequest(text,command){
  let ids=typeof agentSelectedTrackIds==='undefined'?[]:[...agentSelectedTrackIds];
  const target=command==='autoset'?'set':'pair';
  if(!ids.length){
    const current=target==='set'?setPlan:project;
    if(current)ids=target==='set'?current.clips.map(c=>c.track_id):[current.a.track_id,current.b.track_id];
    else if(target==='set'&&setPending.length)ids=[...setPending];
    else ids=[$('track-a').value,$('track-b').value].filter(Boolean);
  }
  // Existing sets may deliberately reuse a source clip; preserve that sequence.
  if(!(sourceSequenceMatches(ids,target==='set'?setPlan:project)))ids=[...new Set(ids)];
  if(ids.length<2||ids.length>8)throw Error('请先在素材中选择 2–8 份音乐，再发送“全自动混音”或“自动串烧”');
  if(ids.some(id=>!tracks.some(t=>t.id===id)))throw Error('选中的素材已变化，请重新打开素材列表选择');
  const kind=target==='set'||ids.length>2?'set':'pair';
  let source=kind==='set'?setPlan:project;
  const active=agentCurrentProject();
  if(active&&active!==source&&(active.clips||[active.a,active.b]).some(c=>c?.preserve_original))throw Error('当前作品有原貌锁，请先在工作台确认切换作品类型，再发起另一种自动编排');
  const roles=ids.map(id=>tracks.find(t=>t.id===id).stem_role);
  const stemPair=ids.length===2&&roles.includes('vocal')&&roles.includes('instrumental');
  const transition=/接歌|转场|接起来|衔接|前后|顺序/.test(text);
  const wantsOverlay=/叠搭|叠加|混搭|mashup|换鼓|鼓组换装|redrum|tone\s*play|旋律预告|逐音|人声.*伴奏|伴奏.*人声/i.test(text)&&!/(?:不要|不需要|别|不想).{0,8}(?:叠搭|叠加|混搭|mashup|换鼓|鼓组换装|redrum|tone\s*play|旋律预告|逐音|人声)/i.test(text);
  const goal=kind==='set'?'continuous':!transition&&(wantsOverlay||stemPair)?'mashup':'transition';
  if(goal==='mashup'&&stemPair)ids.sort((a,b)=>(tracks.find(t=>t.id===a).stem_role==='vocal'?0:1)-(tracks.find(t=>t.id===b).stem_role==='vocal'?0:1));
  if(source){
    const slots=kind==='set'?source.clips:[source.a,source.b];
    const sourceIds=slots.map(c=>c.track_id);
    const differentSources=JSON.stringify(sourceIds)!==JSON.stringify(ids);
    const differentGoal=kind==='pair'&&source.mode!==goal;
    if(differentSources||differentGoal){
      if(slots.some(c=>c.preserve_original))throw Error('当前工程含原貌锁，不能自动更换锁定素材或混音目标。请在工作台手动切换素材或目标，并确认保留原貌锁后再发起');
      source=null;
    }
  }
  return {request_id:crypto.randomUUID().replaceAll('-',''),kind,project:clone(source),track_ids:ids,
    goal,
    styles:['open_format'],features:[],policy:'creative',prompt:text,export:true};
}
function agentSaveAutomation(run){
  try{sessionStorage.setItem(agentAutomationStorageKey,JSON.stringify(run.record));}
  catch(_){if(!run.storageWarning){run.storageWarning=true;run.article.append(node('p','浏览器未能保存任务号，刷新前请记下任务 '+(run.record.id||run.record.request_id),'agent-outcome'));}}
}
function agentAutomationCard(record){
  const article=agentMessage('system','本地全自动混音 · 自动编排、检查和完整导出。');
  const state=node('p','正在提交…','agent-outcome');state.setAttribute('role','status');state.setAttribute('aria-live','polite');state.setAttribute('aria-atomic','true');
  const meter=node('progress');meter.max=1;meter.value=0;meter.setAttribute('aria-label','全自动混音任务进度');
  const cancel=node('button','取消任务','quiet');cancel.disabled=!record.id;
  const recover=node('button','继续查看任务','quiet');recover.hidden=true;
  const outputs=node('div',undefined,'agent-version');
  article.append(state,meter,cancel,recover,outputs);
  const run={record,article,state,meter,cancel,recover,outputs,terminal:false,polling:false};
  cancel.addEventListener('click',async()=>{
    if(run.terminal||cancel.disabled||!record.id)return;cancel.disabled=true;
    try{
      const task=await api('/api/automation/'+record.id+'/cancel',{});
      await agentUpdateAutomation(run,task);
      if(!run.terminal){
        state.textContent='已发送取消请求，正在等待引擎确认…';
        agentScheduleAutomation(run);
      }
    }catch(error){state.textContent='取消请求尚未确认：'+error.message+'。可继续查看任务状态。';cancel.disabled=false;}
  });
  recover.addEventListener('click',async()=>{
    if(run.polling||recover.disabled)return;recover.disabled=true;
    try{if(record.id)await agentPollAutomation(run);else await agentSubmitAutomation(run);}
    finally{recover.disabled=false;}
  });
  return run;
}
async function agentStartAutomation(text,command){
  if(agentAutomationRun&&!agentAutomationRun.terminal)throw Error('已有全自动混音任务，请先查看结果或取消当前任务');
  const payload=agentAutomationRequest(text,command);
  const record={request_id:payload.request_id,payload,snapshot:agentSnapshot(),id:null,applied:false};
  const run=agentAutomationCard(record);agentAutomationRun=run;agentSaveAutomation(run);
  await agentSubmitAutomation(run);
  return run;
}
function agentObserveAutomation(taskId,snapshot){
  if(typeof taskId!=='string'||!/^[-\w]+$/.test(taskId))throw Error('自动任务号无效，未接续任务');
  if(agentAutomationRun&&!agentAutomationRun.terminal){
    if(agentAutomationRun.record.id===taskId)return agentAutomationRun;
    throw Error('聊天中仍有另一个自动任务，请先查看或取消');
  }
  const record={request_id:taskId,payload:null,snapshot,id:taskId,applied:false};
  const run=agentAutomationCard(record);agentAutomationRun=run;agentSaveAutomation(run);
  run.state.textContent='已接续本地全自动任务，正在等待进度… · 任务 '+taskId;
  // Finish the current action/history transaction before a cached result can
  // update the project; observing an existing task never repeats its POST.
  agentScheduleAutomation(run);return run;
}
async function agentSubmitAutomation(run){
  run.recover.hidden=true;run.state.textContent='正在提交本地任务…';
  try{
    const task=await api('/api/automation',run.record.payload);
    if(typeof task.id!=='string'||!/^[-\w]+$/.test(task.id))throw Error('没有收到有效任务号；请取回本次提交，避免重复创建');
    run.record.id=task.id;run.record.submission_uncertain=false;agentSaveAutomation(run);run.cancel.disabled=false;
    await agentUpdateAutomation(run,task);
    if(!run.terminal)agentScheduleAutomation(run);
  }catch(error){
    if([400,401,403,404,413,422].includes(error.http_status)&&!run.record.submission_uncertain){
      run.terminal=true;run.record.status='failed';run.cancel.disabled=true;
      run.state.textContent='任务未提交：'+error.message+'。请调整素材后重新发起，未自动重发。';
      agentSaveAutomation(run);agentAutomationRetry(run);return;
    }
    run.record.submission_uncertain=true;agentSaveAutomation(run);
    const reason=String(error.message).replace(/[。.!！？?]+$/u,'');
    const refresh=[401,403].includes(error.http_status)?'请刷新工作台后取回，继续沿用同一请求号。':'';
    run.state.textContent='任务提交尚未确认：'+reason+'。'+refresh+'未自动重发。';
    run.recover.textContent='取回本次提交（沿用同一请求号）';run.recover.hidden=false;
  }
}
function agentScheduleAutomation(run){
  clearTimeout(run.timer);
  run.timer=setTimeout(()=>agentPollAutomation(run),1500);
}
async function agentPollAutomation(run){
  if(run.polling||run.terminal||!run.record.id)return;
  clearTimeout(run.timer);run.polling=true;run.recover.hidden=true;
  try{
    const task=await api('/api/automation/'+run.record.id);
    await agentUpdateAutomation(run,task);
    if(!run.terminal)agentScheduleAutomation(run);
  }catch(error){
    run.state.textContent='任务状态暂时无法读取：'+error.message+'。任务可能仍在本地继续，未重新提交。';
    run.recover.textContent='继续查看任务';run.recover.hidden=false;
  }finally{run.polling=false;}
}
function agentAutomationURL(value){
  return typeof value==='string'&&/^\/automation-outputs\/[-\w]+\/[\w./%-]+$/.test(value)&&!value.split('/').includes('..')?value:null;
}
function agentAutomationOutputs(run,task){
  const root=run.outputs;root.replaceChildren();
  const output=task.outputs||{};
  const playable=agentAutomationURL(output.mp3)||agentAutomationURL(output.wav);
  if(playable){const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=playable;audio.setAttribute('aria-label','全自动混音成品试听');root.append(audio);}
  const downloads=node('div',undefined,'agent-downloads');
  for(const [key,label,file] of [['wav','下载 WAV','audio.wav'],['mp3','下载 MP3','audio.mp3'],['project','工程 JSON','project.json'],['report','查看检查报告','report.json']]){
    const url=agentAutomationURL(output[key]);if(!url)continue;
    const link=node('a',label+' ↓');link.href=url;link.download=file;downloads.append(link);
  }root.append(downloads);
  if(Array.isArray(output.candidates)&&output.candidates.length){
    const alternatives=node('details');alternatives.append(node('summary','可选 A/B 方案 · 自行试听比较'));
    for(const candidate of output.candidates){
      const url=agentAutomationURL(candidate?.url);if(!url)continue;
      alternatives.append(node('p',candidate.name||'候选方案'));
      const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=url;audio.setAttribute('aria-label',candidate.name||'候选方案试听');alternatives.append(audio);
      const link=node('a','下载此方案 ↓');link.href=url;link.download='candidate.mp3';alternatives.append(link);
    }root.append(alternatives);
  }
  if(Array.isArray(task.explanations)&&task.explanations.length){
    const reasons=node('details');reasons.append(node('summary','编排依据与检查结果'));
    for(const reason of task.explanations)reasons.append(node('p',typeof reason==='string'?reason:JSON.stringify(reason)));root.append(reasons);
  }
}
function sourceSequenceMatches(ids,current){
  return current&&JSON.stringify(ids)===JSON.stringify(current.clips?current.clips.map(c=>c.track_id):[current.a.track_id,current.b.track_id]);
}
function agentAutomationRetry(run){
  const prompt=run.record.payload?.prompt;
  if(typeof prompt!=='string'||!prompt.trim())return;
  const retry=node('button','修改后重试','quiet');
  retry.addEventListener('click',()=>{
    const draft=$('shell-draft');
    if(draft.value.trim()&&draft.value.trim()!==prompt.trim()){
      agentMessage('system','输入框已有新灵感，已保留。请先编辑并确认发送，未重新提交任务。');
    }else{
      const ids=[...new Set(run.record.payload.track_ids||[])];
      if(typeof agentSelectedTrackIds!=='undefined'&&!agentSelectedTrackIds.length&&!agentCurrentProject()&&
          ids.length>=2&&ids.length<=8&&ids.every(id=>tracks.some(t=>t.id===id))){
        agentSelectedTrackIds=ids;
        if(typeof agentRefreshInventory==='function')agentRefreshInventory();
      }
      draft.value=prompt;draft.dispatchEvent(new Event('input'));
      agentMessage('system','已填回本次灵感。请检查素材和参数，确认后点击发送；这会创建新任务。');
    }
    draft.focus();
  });
  run.outputs.append(retry);
}
async function agentUpdateAutomation(run,task){
  if(run.terminal)return;
  if(!['queued','running','succeeded','failed','cancelled'].includes(task.status))throw Error('任务状态无法识别，请继续查看原任务');
  const stages={queued:'等待引擎',preparing:'准备素材',analysis:'分析素材',analyzing:'分析素材',separation:'分离人声与伴奏',separating:'分离人声与伴奏',planning:'编排候选方案',arranging:'编排候选方案',rendering:'生成音频',render:'生成音频',evaluation:'检查音频',evaluating:'检查音频',checking:'检查音频',exporting:'导出完整作品',export:'导出完整作品',delivering:'整理成品文件',complete:'处理完成',cancelling:'正在取消',done:'处理完成'};
  const progress=Number(task.progress);const fraction=Number.isFinite(progress)?Math.max(0,Math.min(1,progress>1?progress/100:progress)):0;
  run.meter.value=fraction;run.meter.setAttribute('aria-valuetext',Math.round(fraction*100)+'%');
  run.state.textContent=`${stages[task.stage]||task.stage||'处理中'} · ${Math.round(fraction*100)}% · 任务 ${run.record.id}`;
  run.state.classList.toggle('agent-task-error',task.status==='failed');
  run.state.classList.toggle('agent-task-cancelled',task.status==='cancelled');
  run.record.status=task.status;agentSaveAutomation(run);
  run.cancel.disabled=task.stage==='cancelling';
  run.cancel.textContent=task.stage==='cancelling'?'正在取消…':'取消任务';
  if(typeof agentUpdateJumpLatest==='function')agentUpdateJumpLatest();
  if(task.status==='queued'||task.status==='running')return;
  run.terminal=true;clearTimeout(run.timer);run.cancel.disabled=true;run.recover.hidden=true;
  if(task.status!=='succeeded'){
    const failure=String(typeof task.error==='string'?task.error:task.error?.message||task.message||'请查看本地任务报告')
      .replace(/^(?:自动)?任务未完成[：:]\s*/u,'').replace(/[。.!！？?]+$/u,'');
    run.state.textContent=task.status==='cancelled'?'任务已取消，当前工程保留。':'任务未完成：'+failure+'。未自动重发。';
    if(typeof agentUpdateJumpLatest==='function')agentUpdateJumpLatest();
    agentAutomationOutputs(run,task);agentAutomationRetry(run);agentHistory.push({role:'assistant',content:run.state.textContent});return;
  }
  run.meter.value=1;agentAutomationOutputs(run,task);
  run.state.textContent='自动处理与导出已完成。请自行试听确认听感；机器检查不代表你已满意。';
  if(task.coverage?.state&&task.coverage.state!=='complete')run.outputs.append(node('p','检查覆盖不完整：请结合报告试听全曲，尤其留意转场与主唱冲突。','agent-outcome'));
  if(run.record.applied){run.outputs.append(node('p','这份结果之前已处理，刷新后只恢复任务和下载。','agent-outcome'));return;}
  if(run.record.snapshot!==agentSnapshot()||busy){
    run.outputs.append(node('p','任务期间工程或素材有更新，结果仅供试听和下载，当前工程未覆盖。','agent-outcome'));return;
  }
  if(!task.project||!['pair','set'].includes(task.kind)){
    run.outputs.append(node('p','音频已保存，但未收到可恢复工程，当前工程未更新。','agent-error'));return;
  }
  try{
    const before=task.kind==='set'?setPlan:project;
    agentCheckHistoryLocks(before,task.project);
    // Keep every locked source exactly as submitted, even under creative policy.
    const slots=p=>!p?[]:p.clips||[p.a,p.b];
    const remaining=slots(task.project).slice();
    for(const clip of slots(before).filter(c=>c?.preserve_original)){
      const index=remaining.findIndex(c=>c?.track_id===clip.track_id&&!agentDiff(c,clip).length);
      if(index<0)throw Error('原貌锁片段有变化，已保留当前工程，请检查报告');
      remaining.splice(index,1);
    }
    const execution=await applyAgentResult({kind:task.kind,project:task.project,commands:[]},run.record.snapshot);
    run.record.applied=true;agentSaveAutomation(run);
    run.outputs.append(node('p',execution.changed?'工程已纳入版本历史，可撤销本次编排。':'工程参数已保持当前版本。','agent-outcome'));
    if(execution.changed){
      let expected=execution.snapshot;const undo=node('button','撤销本次自动编排','quiet');
      undo.addEventListener('click',async()=>{
        if(agentPending||busy||undo.disabled)return;
        if(expected!==agentSnapshot())return agentMessage('system','工程有更新，请在版本面板撤销，避免覆盖新修改。');
        try{const result=await applyAgentResult({kind:execution.kind,project:clone(agentCurrentProject()),commands:['undo']},expected);expected=result.snapshot;undo.disabled=true;agentMessage('system','已撤销本次自动编排，导出文件仍可下载。');}
        catch(error){agentMessage('system','撤销未完成：'+error.message);}
      });run.outputs.append(undo);
    }
  }catch(error){run.outputs.append(node('p','音频可下载；工程未应用：'+error.message,'agent-error'));}
  agentHistory.push({role:'assistant',content:'本地全自动任务 '+run.record.id+' 已生成音频与导出。人类听感确认仍待完成。'+(run.record.applied?'工程已纳入版本历史。':'当前工程未覆盖。')});agentHistory=agentHistory.slice(-16);
}
function agentResumeAutomation(){
  let record;try{record=JSON.parse(sessionStorage.getItem(agentAutomationStorageKey)||'null');}catch(_){return;}
  if(!record||typeof record.request_id!=='string'||typeof record.snapshot!=='string'||(!record.id&&!record.payload))return;
  if(record.id!==null&&(typeof record.id!=='string'||!/^[-\w]+$/.test(record.id)))return;
  const run=agentAutomationCard(record);agentAutomationRun=run;
  if(record.id)agentPollAutomation(run);
  else if(record.status==='failed'){run.terminal=true;run.state.textContent='上次任务未能提交，未自动重发。请调整素材后重新发起。';agentAutomationRetry(run);}
  else{run.record.submission_uncertain=true;run.state.textContent='上次提交未取得任务号。可沿用同一请求号取回结果，未自动重发。';run.recover.textContent='取回本次提交（沿用同一请求号）';run.recover.hidden=false;}
}
agentResumeAutomation();
