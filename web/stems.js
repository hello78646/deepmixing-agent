'use strict';
let stemReady=false, stemCurrent=null, stemPolling=false;
const stemButtons=[];
const stemStages={queued:'等待处理',loading:'载入模型',separating:'分离人声与伴奏',writing:'保存声音',checking:'检查分轨',cancelling:'正在取消',cancelled:'已取消',failed:'分轨未完成',complete:'分轨完成'};

function stemActive() { return stemCurrent && !['succeeded','failed','cancelled'].includes(stemCurrent.status); }
function refreshStemControls() {
  for (const button of stemButtons) {
    const track=tracks.find(item=>item.id===$('track-'+button.dataset.stemSlot).value);
    button.disabled=busy || !stemReady || stemActive() || !track || track.origin_kind==='separated_stem';
  }
  const cancel=$('stem-cancel');
  if (cancel) cancel.hidden=!stemActive();
  for (const button of document.querySelectorAll('[data-apply-stem]')) button.disabled=busy;
}

async function checkStemModel() {
  $('stem-check').disabled=true;
  try {
    const result=await api('/api/separation/status');stemReady=result.ready;
    $('stem-model-status').textContent=result.message;
    $('stem-model-status').classList.toggle('unavailable',!result.ready);
  } catch(error) { stemReady=false;$('stem-model-status').textContent=error.message; }
  finally { $('stem-check').disabled=false;refreshStemControls(); }
}

function displayStemTask(task) {
  $('stem-progress-wrap').hidden=false;
  $('stem-progress').value=task.progress || 0;
  $('stem-task-text').textContent=`${task.source_name} · ${stemStages[task.stage] || task.stage}`;
  $('stem-task-note').textContent=task.status==='failed' ? task.error : task.status==='cancelled' ? '已取消，原曲保留，可重新分离。' :
    task.status==='succeeded' ? (task.processing_revision==='detail-v1'?'细节优先分轨已完成。':'分轨已完成。')+'先对比试听，再决定放入哪一轨。' : '在本机处理完整素材，可继续查看原曲。细节优先需要多次处理；进度不代表分离纯净度。';
  refreshStemControls();
}

async function applyStem(track,slot) {
  if (busy) return;
  setBusy(true);
  try {
    tracks=await api('/api/tracks');
    if (!tracks.some(item=>item.id===track.id)) throw new Error('分轨素材不可用，请重新处理原曲。');
    clearProject();fillSources({[slot]:track.id});$('role-'+slot).value=track.stem_role;
    status(`已将“${track.name}”放入 ${slot.toUpperCase()} 轨。选择另一份分轨后即可叠搭试听。`);
  } catch(error) { status(error.message,true); }
  finally { setBusy(false); }
}

function displayStemResults(task) {
  const result=$('stem-results');result.replaceChildren();result.hidden=false;
  result.append(node('h3','先听清楚，再换伴奏。'),node('p',task.source_name+' · 原曲保留，分轨可重复使用','hint'));
  const grid=node('div',undefined,'stem-result-grid');
  const source={id:task.source_track_id,name:'原曲对照'};
  for (const [role,track,title] of [['original',source,'原曲'],['vocal',task.stems.vocal,'分离人声'],['instrumental',task.stems.instrumental,'分离伴奏']]) {
    const card=node('article',undefined,'stem-result-card');card.append(node('h4',title));
    const audio=node('audio');audio.controls=true;audio.preload='none';audio.src='/media/'+track.id;
    audio.setAttribute('aria-label',title+'试听');card.append(audio);
    if (role!=='original') {
      const button=node('button',role==='vocal'?'人声放入 A 轨 ↗':'伴奏放入 B 轨 ↗','quiet');
      button.dataset.applyStem=role;button.addEventListener('click',()=>applyStem(track,role==='vocal'?'a':'b'));card.append(button);
      const link=node('a','下载这份分轨 ↓');link.href='/media/'+track.id+'?download=1';link.download=track.name;card.append(link);
    }
    grid.append(card);
  }
  result.append(grid);
  const note=node('p','模型分离可能残留原伴奏、人声混响或金属感。请比较三份音频；不满意可保留原曲或换选段。','hint');result.append(note);
  if (task.quality) {
    const details=node('details'),summary=node('summary','查看文件检查与来源');details.append(summary);
    details.append(node('p',`模型 ${task.model} · ${(task.quality.frames/44100).toFixed(2)} 秒 · 双声道 · 44.1 kHz。文件对齐检查通过，仍需试听。`));
    for (const warning of task.quality.warnings || []) details.append(node('p',warning));
    result.append(details);
  }
  refreshStemControls();
}

async function pollStemTask(identifier) {
  if (stemPolling) return;
  stemPolling=true;$('stem-resume').hidden=true;
  try {
    while (true) {
      const task=await api('/api/separations/'+identifier);stemCurrent=task;displayStemTask(task);
      if(typeof agentSeparationRun!=='undefined'&&agentSeparationRun?.current===identifier&&typeof agentStemProgress==='function')agentStemProgress(agentSeparationRun,task);
      if (task.status==='succeeded') {
        // Both chat and manual editors must see the newly published stem IDs.
        const inventory=await api('/api/tracks');
        const selections={a:$('track-a').value,b:$('track-b').value};
        tracks=inventory;fillSources(selections);
        displayStemResults(task);sessionStorage.removeItem('deepmixing-stem-task');return task;
      }
      if (['failed','cancelled'].includes(task.status)) { sessionStorage.removeItem('deepmixing-stem-task');return task; }
      await new Promise(resolve=>setTimeout(resolve,1200));
    }
  } catch(error) {
    $('stem-task-note').textContent='检查任务时连接中断：'+error.message+'。可以继续检查，暂不重复提交。';$('stem-resume').hidden=false;
  } finally { stemPolling=false;refreshStemControls(); }
}

async function startStemTask(slot) {
  const id=$('track-'+slot).value;
  if (!id || busy || stemActive()) return;
  stemCurrent={status:'queued',stage:'queued'};refreshStemControls();
  try {
    const task=await api('/api/separations',{track_id:id});stemCurrent=task;
    sessionStorage.setItem('deepmixing-stem-task',task.id);$('stem-results').hidden=true;
    displayStemTask(task);pollStemTask(task.id);
    $('stem-workshop').scrollIntoView({behavior:'smooth',block:'center'});
  } catch(error) { stemCurrent=null;$('stem-model-status').textContent=error.message;status(error.message,true);refreshStemControls(); }
}

function initializeStems() {
  const section=node('section',undefined,'stem-workshop');section.id='stem-workshop';
  const title=node('div',undefined,'section-title');title.append(node('h2','把人声留下，让律动重新相遇。'));
  const check=node('button','重新检查','quiet');check.id='stem-check';check.addEventListener('click',checkStemModel);title.append(check);section.append(title);
  section.append(node('p','从完整歌曲生成独立人声和伴奏。原曲始终保留，结果先试听，再放入混音。','hint'));
  const model=node('p','正在检查本地分轨模型…','stem-model-status');model.id='stem-model-status';section.append(model);
  const wrap=node('div',undefined,'stem-task');wrap.id='stem-progress-wrap';wrap.hidden=true;
  const text=node('p');text.id='stem-task-text';const progress=node('progress');progress.id='stem-progress';progress.max=1;progress.value=0;
  const note=node('p',undefined,'hint');note.id='stem-task-note';
  const cancel=node('button','取消分轨','quiet');cancel.id='stem-cancel';cancel.hidden=true;
  cancel.addEventListener('click',async()=>{
    if (!stemCurrent?.id) return;cancel.disabled=true;
    try { const task=await api('/api/separations/'+stemCurrent.id+'/cancel',{});stemCurrent=task;displayStemTask(task); }
    catch(error) { $('stem-task-note').textContent=error.message; }
    finally { cancel.disabled=false; }
  });
  const resume=node('button','继续检查任务','quiet');resume.id='stem-resume';resume.hidden=true;
  resume.addEventListener('click',()=>stemCurrent?.id && pollStemTask(stemCurrent.id));
  wrap.append(text,progress,note,cancel,resume);section.append(wrap);
  const results=node('div');results.id='stem-results';results.hidden=true;section.append(results);
  document.querySelector('.inspiration').before(section);
  for (const slot of ['a','b']) {
    const button=node('button',slot==='a'?'从这首歌提取人声 ↗':'从这首歌提取伴奏 ↗','quiet stem-start');
    button.dataset.stemSlot=slot;button.disabled=true;button.addEventListener('click',()=>startStemTask(slot));
    $('source-'+slot).after(button);stemButtons.push(button);
  }
  refreshStemControls();checkStemModel();
  const previous=sessionStorage.getItem('deepmixing-stem-task');
  if (previous && /^[0-9a-f]{32}$/.test(previous)) {
    stemCurrent={id:previous,status:'running'};pollStemTask(previous);
  }
}
initializeStems();
