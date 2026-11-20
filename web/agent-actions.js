'use strict';
const agentRenderKeys=new Map();
let agentSeparationRun=null,agentSeparationPromise=null;
function agentEngineeringSignature(){
  const state=JSON.parse(agentSnapshot());delete state.tracks;return JSON.stringify(state);
}
// Chat edits the same live objects and history that the professional UI uses.
function agentSnapshot(){
  return JSON.stringify({kind:workbenchKind,pair:project,set:setPlan,pending:setPending,
    selected:typeof agentSelectedTrackIds==='undefined'?[]:agentSelectedTrackIds,
    sources:['a','b'].map(k=>[$('track-'+k).value,$('role-'+k).value]),
    tracks:tracks.map(t=>t.id),history:[undoStack.length,redoStack.length,setUndo.length,setRedo.length]});
}
function agentCurrentProject(){return workbenchKind==='set'?setPlan:project;}
function agentCheckHistoryLocks(before,after){
  if(!before||!after)return; // Removing a newly created work does not unlock a source in another work.
  const slots=p=>p.kind==='continuous_set'?p.clips:[p.a,p.b];
  const remaining=slots(after).filter(Boolean).slice();
  for(const old of slots(before).filter(Boolean))if(old.preserve_original){
    const index=remaining.findIndex(c=>c.track_id===old.track_id&&c.preserve_original);
    if(index<0)throw Error('这次历史恢复会解除原貌锁或替换锁定素材，请到版本面板亲手确认');
    remaining.splice(index,1);
  }
}
function agentRestore(direction){
  const isSet=workbenchKind==='set';
  const from=isSet?(direction==='undo'?setUndo:setRedo):(direction==='undo'?undoStack:redoStack);
  const to=isSet?(direction==='undo'?setRedo:setUndo):(direction==='undo'?redoStack:undoStack);
  if(!from.length)throw Error(direction==='undo'?'当前没有可撤销的修改':'当前没有可重做的修改');
  agentCheckHistoryLocks(isSet?setPlan:project,isSet?from[from.length-1].plan:from[from.length-1]);
  if(isSet){
    to.push({plan:clone(setPlan),pending:[...setPending]});const old=from.pop();setPlan=old.plan;setPending=old.pending;
    invalidateSetPlayback();drawSet();
  }else{
    to.push(clone(project));project=from.pop();invalidate();refreshControls();
  }
}
async function agentRender(preview){
  const snapshot=agentSnapshot(),kind=workbenchKind;
  const signature=JSON.stringify([workbenchKind,agentCurrentProject(),preview]);
  if(!agentRenderKeys.has(signature))agentRenderKeys.set(signature,crypto.randomUUID().replaceAll('-',''));
  const key=agentRenderKeys.get(signature);
  const outcome=await (kind==='set'?renderSet(preview,key):render(preview,key));
  if(!outcome?.ok){
    if(outcome?.task_failed)agentRenderKeys.delete(signature);
    throw Error((outcome?.error||'音频任务未成功，请检查工程')+(outcome?.task_id?' · 任务 '+outcome.task_id+'；重试会先检查同一任务':''));
  }
  if(snapshot!==agentSnapshot())throw Error('音频任务已结束，但作品类型、工程或素材已变化，已停止后续操作；请回原作品检查结果');
  return outcome;
}
async function agentSeparate(trackId,allStems=false){
  if(typeof stemActive==='function'&&stemActive())throw Error('已有分轨任务，请先查看结果');
  const body={track_id:trackId};if(allStems)body.all_stems=true;
  const task=await api('/api/separations',body);
  if(typeof displayStemTask==='function'){
    stemCurrent=task;sessionStorage.setItem('deepmixing-stem-task',task.id);displayStemTask(task);
    $('stem-results').hidden=true;agentSeparationPromise=pollStemTask(task.id);
  }
  return {separation:task.id,pending:true};
}
async function agentBeginSeparations(ids,intent=''){
  if(agentSeparationRun?.status==='running')throw Error('聊天分轨仍在处理中，请先等待或取消');
  const requests=ids.map(c=>typeof c==='string'?{track_id:c}:c);
  const allStems=new Set(requests.filter(c=>c.all_stems===true).map(c=>c.track_id));
  const run={ids:[...new Set(requests.map(c=>c.track_id))],intent,signature:agentEngineeringSignature(),status:'running',tasks:[],current:null};
  agentSeparationRun=run;
  let first;
  try{first=await agentSeparate(run.ids[0],allStems.has(run.ids[0]));run.current=first.separation;}
  catch(error){run.status='failed';throw error;}
  run.completion=(async()=>{
    try{
      for(let i=0;i<run.ids.length;i++){
        if(run.status!=='running')throw Error('分轨接续已取消');
        if(i){
          if(run.signature!==agentEngineeringSignature()||busy)throw Error('工程已变化，后续分轨已停止；请根据当前版本重新安排');
          const started=await agentSeparate(run.ids[i],allStems.has(run.ids[i]));run.current=started.separation;
        }
        if(typeof agentStemProgress==='function')agentStemProgress(run,stemCurrent);
        const task=await agentSeparationPromise;
        if(!task||task.status!=='succeeded')throw Error(task?.error||(task?.status==='cancelled'?'分轨已取消':'任务检查中断，请在素材分离面板继续检查'));
        run.tasks.push(task);
        if(typeof agentStemProgress==='function')agentStemProgress(run,task);
      }
      if(run.status!=='running')throw Error('分轨接续已取消');
      run.status=run.signature===agentEngineeringSignature()?'succeeded':'stale';
      if(typeof agentStemComplete==='function')agentStemComplete(run);
    }catch(error){if(run.status==='running')run.status='failed';run.error=error.message;if(typeof agentStemComplete==='function')agentStemComplete(run,error);}
  })();
  return first;
}
async function applyAgentResult(result,snapshot){
  if(busy)throw Error('音频引擎正在处理，请稍后再执行');
  if(snapshot!==agentSnapshot())throw Error('工程或素材已变化，旧回复没有应用。请基于当前版本重新发送。');
  if(!['pair','set'].includes(result.kind))throw Error('作品类型无效，未应用修改');
  const ids=result.project?(result.kind==='set'?result.project.clips.map(c=>c.track_id):[result.project.a.track_id,result.project.b.track_id]):[];
  ids.push(...(result.project?.performance?.events||[]).map(e=>e.track_id));
  const slots=result.project?(result.kind==='set'?result.project.clips:[result.project.a,result.project.b]):[];
  ids.push(...slots.flatMap(s=>(s.effects||[]).filter(e=>e.type==='sidechain').map(e=>e.key_track_id)));
  if(ids.some(id=>!tracks.some(t=>t.id===id))){
    const inventory=await api('/api/tracks');
    if(snapshot!==agentSnapshot())throw Error('读取新分轨时工程已变化，旧回复没有应用');
    if(ids.some(id=>!inventory.some(t=>t.id===id)))throw Error('缺少模型引用的素材，请先导入或等待分轨完成');
    const selections={a:$('track-a').value,b:$('track-b').value};tracks=inventory;fillSources(selections);
  }
  const kind=result.kind,before=clone(kind==='set'?setPlan:project);
  const changed=JSON.stringify(before)!==JSON.stringify(result.project);
  if(changed){
    if(kind==='set')setEdit(()=>{setPlan=clone(result.project);setPending=[];});
    else{
      undoStack.push(clone(project));if(undoStack.length>50)undoStack.shift();redoStack=[];
      project=clone(result.project);invalidate();refreshControls();
    }
  }
  workbenchKind=kind;
  if(changed)status('本轮编排已更新。生成试听，听完再继续调整或完整导出。');
  if(typeof refreshWorkbenchShell==='function')refreshWorkbenchShell();
  const originallyApproved=workbenchKind==='set'?setApproved:approvedPreview;
  const outcomes=[];
  try{
  const separations=(result.commands||[]).filter(c=>c?.action==='separate');
  let separationStarted=false;
  for(const command of result.commands||[]){
    if(workbenchKind!==kind)throw Error('作品已切换，已停止后续操作');
    const commandSnapshot=agentSnapshot();
    const commandEngineering=agentEngineeringSignature();
    const action=typeof command==='string'?command:command.action;
    if(action==='undo'||action==='redo'){agentRestore(action);outcomes.push({action,ok:true});}
    else if(action==='materials'){if(typeof agentOpenMaterials==='function')agentOpenMaterials();else if(typeof openWorkbenchTool==='function')openWorkbenchTool('materials');outcomes.push({action,ok:true});}
    else if(action==='automation'){
      if(typeof agentObserveAutomation!=='function')throw Error('全自动混音入口尚未加载，请刷新页面后取回原任务');
      agentObserveAutomation(command.task_id,commandSnapshot);outcomes.push({action,task_id:command.task_id,pending:true,ok:true});
    }
    else if(action==='separate'){
      if(!separationStarted){outcomes.push({action,...await agentBeginSeparations(separations,result.intent||'')});separationStarted=true;}
    }
    else if(action==='play'||action==='pause'){
      const audio=workbenchKind==='set'?setAudio:$('preview-audio');
      if(action==='pause'){
        for(const a of new Set([audio,$('source-a'),$('source-b'),$('preview-audio'),$('full-audio'),setAudio,...document.querySelectorAll('audio')]))a?.pause();
      }
      else{
        if(!(workbenchKind==='set'?setApproved:approvedPreview)||!audio.getAttribute('src'))throw Error('当前版本尚无试听，请先说“生成试听”');
        if(typeof chooseLiveAudio==='function')chooseLiveAudio(audio);await audio.play();
      }
      outcomes.push({action,ok:true});
    }
    else if(action==='preview')outcomes.push({action,...await agentRender(true)});
    else if(action==='export'){
      if(!originallyApproved){
        if(!(workbenchKind==='set'?setApproved:approvedPreview))outcomes.push({action:'preview',...await agentRender(true)});
        outcomes.push({action:'export',needs_confirmation:true});
      }else outcomes.push({action,...await agentRender(false)});
    }else throw Error('模型返回了未支持的操作');
    if(action!=='undo'&&action!=='redo'&&(action==='separate'?commandEngineering!==agentEngineeringSignature():commandSnapshot!==agentSnapshot()))throw Error('任务处理中工程或素材已变化，已停止后续操作');
  }
  }catch(error){error.execution={kind,changed,before,after:clone(kind==='set'?setPlan:project),outcomes,snapshot:agentSnapshot()};throw error;}
  return {kind,changed,before,after:clone(agentCurrentProject()),outcomes,snapshot:agentSnapshot()};
}
