'use strict';
let agentConfigured=false,agentPending=false,agentHistory=[];
function agentMessagesAtBottom(root){
  if(!root)return true;
  return root.scrollHeight-root.scrollTop-root.clientHeight<=36;
}
function agentUpdateJumpLatest(){
  const root=$('agent-messages'),jump=$('agent-jump-latest');
  if(!root||!jump)return;
  jump.hidden=!root.childElementCount||agentMessagesAtBottom(root);
}
function agentFollowLatest(instant=false){
  const root=$('agent-messages');
  if(!root)return;
  if(typeof root.scrollTo==='function')root.scrollTo({top:root.scrollHeight,behavior:instant?'auto':'smooth'});
  else root.scrollTop=root.scrollHeight;
  root.dataset.followLatest='false';
  agentUpdateJumpLatest();
}
function refreshAgentComposer(){
  const local=typeof agentLocalCommand==='function'&&agentLocalCommand($('shell-draft').value);
  $('shell-send').disabled=agentPending||busy||(!agentConfigured&&!local)||!$('shell-draft').value.trim();
  $('shell-send').setAttribute('aria-label',agentPending?'正在处理灵感':'发送混音灵感');
  $('agent-clear').disabled=agentPending;
  if($('agent-files'))$('agent-files').disabled=agentPending||busy;
  if($('shell-add'))$('shell-add').disabled=agentPending||busy;
}
async function checkTextAgent(verify=false){
  $('agent-check').disabled=true;
  try{
    const info=await api(verify?'/api/agent/check':'/api/agent/status',verify?{}:undefined);agentConfigured=info.configured;
    $('agent-connection').textContent=info.message+(info.configured?'':' · 可直接发送“全自动混音”或“自动串烧”，无需配置文字模型');
    const providerName={deepseek:'DeepSeek',qwen:'千问',compatible:'兼容模型'}[info.provider]||'文字 Agent';
    $('agent-model').textContent=info.configured?info.model:providerName+' · 等待本机配置';
    $('agent-config-help').hidden=info.configured;
  }catch(e){$('agent-connection').textContent='连接检查未通过：'+e.message;}
  finally{$('agent-check').disabled=false;refreshAgentComposer();}
}
function agentMessage(role,text,voiceSession=null,preparedVoice=null){
  $('shell-smart').classList.add('has-conversation');
  const root=$('agent-messages');
  const follow=role==='user'||root.dataset.followLatest==='true'||!root.childElementCount||agentMessagesAtBottom(root);
  const article=node('article',undefined,'agent-message agent-'+role);
  article.append(node('p',role==='user'?'你':role==='assistant'?'零氪星球':'执行状态','agent-speaker'),node('p',text,'agent-message-text'));
  root.append(article);
  if(typeof planetReceiveMessage==='function')planetReceiveMessage(role,text);
  if(role==='assistant'&&typeof agentVoiceReply==='function')agentVoiceReply(text,article,voiceSession,preparedVoice);
  if(follow){
    if(role==='user')root.dataset.followLatest='true';
    if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>agentFollowLatest(role==='user'));
    else agentFollowLatest(role==='user');
  }
  else agentUpdateJumpLatest();
  return article;
}
function agentDiff(before,after){
  const changes=[];
  function visit(a,b,path){
    if(JSON.stringify(a)===JSON.stringify(b))return;
    if(a&&b&&typeof a==='object'&&typeof b==='object'){
      for(const key of new Set([...Object.keys(a),...Object.keys(b)]))visit(a[key],b[key],path?path+'.'+key:key);
    }else changes.push(`${path||'工程'}：${typeof a==='object'?JSON.stringify(a):a??'未设置'} → ${typeof b==='object'?JSON.stringify(b):b??'未设置'}`);
  }visit(before,after,'');return changes;
}
function agentVersionCard(parent,result,execution){
  const card=node('div',undefined,'agent-version');
  card.append(node('strong',execution.changed?'已应用 · '+(execution.after?.title||'本轮修改'):'当前作品 · 操作记录'));
  if(result.reasons?.length){
    const reasons=node('details');reasons.append(node('summary','为什么这样混 · 查看音乐依据'));
    for(const reason of result.reasons)reasons.append(node('p',reason,'agent-reason'));card.append(reasons);
  }
  if(execution.changed){
    const details=node('details');details.append(node('summary','对比修改前后'));
    for(const line of agentDiff(execution.before,execution.after))details.append(node('p',line));card.append(details);
  }
  for(const event of result.events||[])if(!event.result.ok)card.append(node('p','工具未执行：'+event.result.error,'agent-error'));
  for(const outcome of execution.outcomes){
    card.append(node('p',outcome.needs_confirmation?'试听已准备好。先听，再确认完整导出。':outcome.action==='automation'?'全自动混音已接续，进度与成品会在任务卡显示。':outcome.action==='separate'?'分轨已提交，聊天里会显示进度与结果。':outcome.action==='preview'?'试听已生成，点下方播放。':outcome.action==='export'?'完整作品已导出：WAV、MP3和可恢复工程。':outcome.action==='undo'?'已撤销一整批修改。':outcome.action==='redo'?'已重做一整批修改。':outcome.action==='play'?'正在播放当前试听。':outcome.action==='pause'?'已暂停。':'已打开素材入口。','agent-outcome'));
    if(outcome.action==='export'&&outcome.ok){
      const downloads=node('div',undefined,'agent-downloads');
      for(const [file,label] of [['audio.wav','WAV ↓'],['audio.mp3','MP3 ↓'],['project.json','工程 JSON ↓']]){
        const link=node('a',label);link.href='/outputs/'+outcome.task_id+'/'+file+'?download=1';link.download=file;downloads.append(link);
      }card.append(downloads);
    }
  }
  let expected=execution.snapshot;
  const targetKind=execution.kind||result.kind||workbenchKind;
  const actions=node('div',undefined,'agent-card-actions');
  async function perform(action){
    if(agentPending||busy)return;
    if(expected!==agentSnapshot()||targetKind!==workbenchKind)return agentMessage('system','这个版本之后工程有更新或作品已切换，请对当前版本继续操作。');
    agentPending=true;refreshAgentComposer();
    try{
      if(action==='listen'){
        const audio=workbenchKind==='set'?setAudio:$('preview-audio');
        if(!(workbenchKind==='set'?setApproved:approvedPreview)||!audio.getAttribute('src'))throw Error('当前版本尚无试听，请先生成试听');
        if(typeof chooseLiveAudio==='function')chooseLiveAudio(audio);
        await audio.play();
      }else if(action==='export'){
        const outcome=await agentRender(false);
        agentHistory.push({role:'assistant',content:'实际执行结果：当前版本完整导出成功，已提供WAV/MP3/JSON。'});
        agentVersionCard(agentMessage('system','完整导出成功。'),{reasons:[],events:[]},{changed:false,after:clone(agentCurrentProject()),outcomes:[{action:'export',...outcome}],snapshot:agentSnapshot()});
      }else{
        const response=await applyAgentResult({kind:workbenchKind,project:clone(agentCurrentProject()),commands:[action]},expected);
        expected=response.snapshot;
        agentHistory.push({role:'assistant',content:'实际执行结果：'+(action==='undo'?'撤销成功':'试听生成成功')});
        agentVersionCard(agentMessage('system',action==='undo'?'已撤销本次修改。':'真实试听已生成。'),{reasons:[],events:[]},response);
      }
    }catch(e){agentMessage('system','操作未完成：'+e.message);}
    finally{agentPending=false;refreshAgentComposer();}
  }
  const hasPlan=Boolean(execution.after),approved=workbenchKind==='set'?setApproved:approvedPreview;
  const buttons=hasPlan?[[approved?'listen':'preview',approved?'播放试听':'生成试听']]:[];
  if(hasPlan&&approved)buttons.push(['export','确认这版 · 完整导出']);
  if(execution.changed)buttons.push(['undo','撤销这一版']);
  for(const [action,label] of buttons){const b=node('button',label);b.addEventListener('click',()=>perform(action));actions.append(b);}
  card.append(actions);parent.append(card);
}
async function sendAgentMessage({voiceText,voiceSession}={}){
  if(agentPending||busy)return false;
  const draft=$('shell-draft'),text=(voiceText??draft.value).trim().slice(0,2000);if(!text)return false;
  const local=typeof agentLocalCommand==='function'?agentLocalCommand(text):null;
  if(!agentConfigured&&!local)return false;
  const snapshot=agentSnapshot(),sentProject=clone(agentCurrentProject()),kind=workbenchKind;
  const mode=workbenchMode==='smart'?'smart':'pro';
  const history=agentHistory.slice(-14).map(m=>({role:m.role,content:m.content.slice(0,4000)}));
  const requestId=crypto.randomUUID().replaceAll('-','');
  if(typeof agentVoiceBeginTurn==='function')agentVoiceBeginTurn(text,voiceSession);
  agentMessage('user',text);
  if(voiceSession&&mode==='smart'&&typeof planetToggleDialogue==='function')planetToggleDialogue(true);
  if(!voiceSession){draft.value='';draft.dispatchEvent(new Event('input'));}
  agentPending=true;refreshAgentComposer();$('agent-progress').textContent='正在读取工程、理解这次灵感…';
  async function finishResult(result){
    result.intent=text;
    $('agent-progress').textContent='正在校验并执行工作台操作…';
    const reply=result.reply||(local==='undo'?'已撤销。':local==='redo'?'已重做。':local==='play'?'播放当前试听。':'已暂停。');
    const preparedVoice=voiceSession&&result.reply&&typeof agentVoicePrepareReply==='function'
      ?agentVoicePrepareReply(reply,voiceSession):null;
    const execution=await applyAgentResult(result,snapshot);
    const message=agentMessage('assistant',reply,voiceSession,preparedVoice);
    if(mode==='pro'&&!execution.changed&&!execution.outcomes.length)message.append(node('p','本轮未改变工程或生成音频。','agent-outcome'));
    if(execution.changed||execution.outcomes.length||result.events?.length)agentVersionCard(message,result,execution);
    const outcome=execution.changed?'参数已真实应用，音频是否成功请看以下结果。':'工程参数未改变。';
    agentHistory.push({role:'user',content:text},{role:'assistant',content:result.reply+'\n实际执行记录：'+outcome+' '+JSON.stringify(execution.outcomes.map(o=>({action:o.action,ok:o.ok,pending:o.pending,needs_confirmation:o.needs_confirmation})))});
    agentHistory=agentHistory.slice(-16);
    $('agent-connection').textContent=result.source==='model'?'文字Agent · 本轮调用成功':result.source==='local'?'本地操作 · 未调用模型':'需要先明确你的选择';
    $('shell-draft-status').textContent='可以继续反馈听感，或打开开发者模式细调';
  }
  try{
    if(local==='automix'||local==='autoset'){
      if(typeof agentStartAutomation!=='function')throw Error('全自动混音入口尚未加载，请刷新页面后重试');
      $('agent-progress').textContent='正在提交本地全自动混音任务…';
      const run=await agentStartAutomation(text,local);
      if(voiceSession&&run.record.id)agentMessage('assistant','混音任务已提交。我会在对话里更新进度。',voiceSession);
      agentHistory.push({role:'user',content:text});agentHistory=agentHistory.slice(-16);
      $('agent-connection').textContent='本地全自动混音 · 未调用付费文字模型';
      $('shell-draft-status').textContent='任务会自动完成编排和导出，可在聊天里查看进度或取消';
      return true;
    }
    if(local==='help'){
      if(mode==='smart'){
        agentMessage('assistant','你想把几首歌接起来，还是先给一首歌换个感觉？',voiceSession);return true;
      }
      if(!voiceSession)agentOpenGuide();
      const help=voiceSession?'先添加并选择两到八份素材，再告诉我“全自动混音”或“自动串烧”。混好后，你可以在对话里试听。':
        '先添加并选择 2–8 份素材，再发送“全自动混音”或“自动串烧”，本地引擎会自动编排、检查并导出，无需配置文字模型。完成后点音频试听，再评价听感。示例都在使用指引里。';
      agentMessage('assistant',help,voiceSession);return true;
    }
    const result=local?{kind,project:sentProject,commands:[local],reply:'',reasons:[],events:[],source:'local'}:
      await api('/api/agent/chat',{request_id:requestId,message:text,kind,mode,project:sentProject,workspaces:{pair:clone(project),set:clone(setPlan)},selected_track_ids:typeof agentSelectedTrackIds!=='undefined'?agentSelectedTrackIds:[],history});
    await finishResult(result);
  }catch(e){
    const current=JSON.stringify(agentCurrentProject())!==JSON.stringify(sentProject);
    const message=agentMessage('system','本轮没有完成：'+e.message+(current?' 当前参数有变化，请在调音台或版本中核对；可撤销。':''));
    if(e.execution)agentVersionCard(message,{reasons:[],events:[]},e.execution);
    if(!local){
      const details=node('details');details.append(node('summary','查看请求信息'),node('p',`请求编号：${e.request_id||requestId} · ${e.code||'client_execution_error'}。未自动重发模型请求。`));message.append(details);
    }
    if(!local&&['client_network_error','client_protocol_error','request_pending'].includes(e.code)){
      const recover=node('button','取回本轮结果（不重新调用模型）');message.append(recover);
      let consumed=false;
      recover.addEventListener('click',async()=>{
        if(agentPending||busy||consumed)return;
        agentPending=true;recover.disabled=true;refreshAgentComposer();
        try{
          const state=await api('/api/agent/requests/'+requestId);
          if(state.state==='completed'){
            consumed=true;await finishResult(state.result);
            if(!voiceSession&&draft.value.trim()===text){draft.value='';draft.dispatchEvent(new Event('input'));}
            recover.textContent='已取回本轮结果';
          }else if(state.state==='pending')agentMessage('system','本轮仍在处理，稍后可再次取回结果。');
          else{consumed=true;agentMessage('system',state.failure?.error||state.message||'没有可取回的结果；未重新调用模型。');}
        }catch(error){agentMessage('system','取回未完成：'+error.message);}
        finally{agentPending=false;recover.disabled=consumed;$('agent-progress').textContent='';refreshAgentComposer();}
      });
    }
    if(!voiceSession&&!draft.value){draft.value=text;draft.dispatchEvent(new Event('input'));}
  }finally{agentPending=false;$('agent-progress').textContent='';refreshAgentComposer();
    if(typeof agentVoiceEndTurn==='function')agentVoiceEndTurn(voiceSession);
  }
  return true;
}
$('shell-send').addEventListener('click',()=>sendAgentMessage());
$('shell-draft').addEventListener('input',refreshAgentComposer);
$('shell-draft').addEventListener('keydown',event=>{
  if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229&&!event.repeat){
    event.preventDefault();sendAgentMessage();
  }
});
$('agent-check').addEventListener('click',()=>checkTextAgent(true));
$('agent-messages').addEventListener('scroll',agentUpdateJumpLatest,{passive:true});
$('agent-jump-latest').addEventListener('click',()=>{agentFollowLatest();$('agent-messages').focus({preventScroll:true});});
$('agent-clear').addEventListener('click',()=>{
  if(agentPending)return;agentHistory=[];$('agent-messages').replaceChildren();$('shell-smart').classList.remove('has-conversation');
  if(typeof agentAutomationRun!=='undefined'&&agentAutomationRun&&!agentAutomationRun.terminal){
    $('agent-messages').append(agentAutomationRun.article);$('shell-smart').classList.add('has-conversation');
    $('agent-progress').textContent='对话已清空，进行中的自动任务与工程保留。';return;
  }
  agentUpdateJumpLatest();$('agent-progress').textContent='对话已清空，工程和历史保留。';
});
checkTextAgent();
