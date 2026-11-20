'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="deepmixing-token"]').content;
const clone = value => JSON.parse(JSON.stringify(value));
let tracks = [], project = null, undoStack = [], redoStack = [], approvedPreview = null, busy = false;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function status(text, error = false) {
  $('status').textContent = text;
  document.querySelector('.statusbar').classList.toggle('error', error);
}
function refreshButtons() {
  $('recommend').disabled = busy || !$('track-a').value || !$('track-b').value;
  $('preview').disabled = busy || !project;
  $('export').disabled = busy || !approvedPreview;
  $('undo').disabled = busy || !undoStack.length;
  $('redo').disabled = busy || !redoStack.length;
  for (const id of ['file-a', 'file-b', 'track-a', 'track-b', 'role-a', 'role-b', 'mode', 'import-project', 'import-anywhere', 'library-search', 'link-import', 'import-target', 'file-video']) $(id).disabled = busy;
  for (const button of document.querySelectorAll('[data-import-asset]')) button.disabled = busy;
  if (typeof refreshStemControls === 'function') refreshStemControls();
  if (typeof refreshCueControls === 'function') refreshCueControls();
  if (typeof refreshLiveControls === 'function') refreshLiveControls();
  if (typeof refreshMusicGuideControls === 'function') refreshMusicGuideControls();
  if (typeof refreshSetControls === 'function') refreshSetControls();
  if (typeof refreshWorkbenchShell === 'function') refreshWorkbenchShell();
  if (typeof refreshAgentComposer === 'function') refreshAgentComposer();
}
function setBusy(value) {
  busy = value;
  document.querySelector('.statusbar').classList.toggle('busy', value);
  refreshButtons();
}
async function api(path, data, raw = false) {
  const options = {};
  if (data !== undefined) {
    options.method = 'POST';
    options.headers = { 'X-Deepmixing-Token': token, 'Content-Type': raw ? 'application/octet-stream' : 'application/json' };
    options.body = raw ? data : JSON.stringify(data);
  }
  let response,result;
  try { response = await fetch(path, options); }
  catch (_) { const error=new Error('工作台连接中断，请检查本机服务；尚未确认本轮结果。');error.code='client_network_error';throw error; }
  try { result = await response.json(); }
  catch (_) { const error=new Error('工作台返回了无法读取的响应；尚未确认本轮结果。');error.code='client_protocol_error';throw error; }
  if(result===null||typeof result!=='object'){const error=new Error('工作台返回格式异常；尚未确认本轮结果。');error.code='client_protocol_error';throw error;}
  if (!response.ok) {
    const error=new Error(result.error || `请求失败 (${response.status})`);
    for(const key of ['code','request_id','retryable','delivery'])if(result[key]!==undefined)error[key]=result[key];
    error.http_status=response.status;throw error;
  }
  return result;
}
function seconds(value) { return `${Number(value).toFixed(1)}秒`; }
function showSource(key) {
  const track = tracks.find(track => track.id === $('track-' + key).value);
  const audio = $('source-' + key);
  if (!track) {
    audio.removeAttribute('src');
    $('info-' + key).textContent = '请上传或选择一份音频';
    return;
  }
  audio.src = '/media/' + track.id;
  $('info-' + key).textContent = `${/\.(mp4|mov|mkv|webm)$/i.test(track.name) ? '视频原音轨 · ' : ''}${seconds(track.duration)} · ${track.analysis.bpm_estimate ? '估计 ' + track.analysis.bpm_estimate + ' BPM（待试听）' : '速度未确定，可手动填写'} · 前${track.analysis.analyzed_seconds}秒分析`;
}
function fillSources(selections = {}) {
  for (const key of ['a', 'b']) {
    const select = $('track-' + key);
    const selected = selections[key] || select.value;
    select.replaceChildren(new Option('选择一份本地音频', ''));
    for (const track of tracks) select.add(new Option(track.name, track.id));
    select.value = selected;
    showSource(key);
  }
  refreshButtons();
}
function invalidate() {
  approvedPreview = null;
  $('preview-note').textContent = '参数已更新。重新生成试听后，即可确认并完整导出。';
  $('preview-audio').hidden = true;
  $('preview-audio').pause();
  $('downloads').hidden = true;
  $('full-audio').pause();
  refreshButtons();
}
function clearProject() {
  project = null;
  undoStack = []; redoStack = [];
  invalidate();
  for (const id of ['editor', 'finish', 'recommendation-section']) $(id).hidden = true;
}
function remember() {
  if (project) undoStack.push(clone(project));
  if (undoStack.length > 50) undoStack.shift();
  redoStack = [];
}
function updateProject(mutator) {
  if (!project) return;
  remember();
  mutator(project);
  invalidate();
  refreshControls();
}
function refreshControls() {
  if (!project) { for (const id of ['editor','finish','recommendation-section']) $(id).hidden=true; refreshButtons(); return; }
  // History restores the entire project, including its visible source identity.
  // Never let a selector/player audition a different asset from the render plan.
  $('mode').value = project.mode;
  $('arrangement').value = project.mode === 'mashup' && Number(project.b_offset_seconds) > 0 ? 'acappella_drop' : 'together';
  $('prompt').value = project.prompt;
  for (const key of ['a', 'b']) {
    $('role-' + key).value = project[key].role;
    if ($('track-' + key).value !== project[key].track_id) {
      $('track-' + key).value = project[key].track_id;
      showSource(key);
    }
  }
  $('editor').hidden = false; $('finish').hidden = false;
  for (const input of document.querySelectorAll('[data-path]')) {
    const parts = input.dataset.path.split('.');
    const value = parts.length === 2 ? project[parts[0]][parts[1]] : project[parts[0]];
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else input.value = value === null || value === undefined ? '' : value;
  }
  $('echo-value').textContent = `${Math.round(project.echo * 100)}% · 0为关闭`;
  $('overlap-label').hidden = project.mode !== 'transition';
  $('offset-label').hidden = project.mode !== 'mashup';
  if (typeof refreshMusicGuides === 'function') refreshMusicGuides();
  refreshButtons();
}
function buildTrackControls() {
  const root = $('track-controls');
  for (const key of ['a', 'b']) {
    const panel = node('article', undefined, 'control-panel');
    panel.append(node('h3', key.toUpperCase() + ' 轨 · 选段与声音'));
    const grid = node('div', undefined, 'control-grid');
    const fields = [['start', '片段起点 / 秒', 0, 1200, .01], ['end', '片段终点 / 秒', .25, 1200, .01],
      ['bpm', '素材 BPM（可手动校正）', 40, 240, .01], ['gain_db', '音量 / dB', -48, 6, 1],
      ['bass_db', '低频 / dB', -18, 6, 1], ['semitones', '移调 / 半音', -12, 12, 1]];
    for (const [field, text, min, max, step] of fields) {
      const label = node('label', text), input = node('input');
      input.type = 'number'; input.min = min; input.max = max; input.step = step;
      input.dataset.path = key + '.' + field;
      if (field === 'bpm') input.placeholder = '未知可留空';
      label.append(input); grid.append(label);
    }
    panel.append(grid);
    const lock = node('label', undefined, 'lock'), checkbox = node('input');
    checkbox.type = 'checkbox'; checkbox.dataset.path = key + '.preserve_original';
    lock.append(checkbox, node('span', '保持原貌：锁定速度、音高、低频与回声'));
    panel.append(lock); root.append(panel);
  }
}
async function upload(key, suppliedFile = null) {
  const file = suppliedFile || $('file-' + key).files[0];
  if (!file) return;
  if (file.size > 80 * 1024 * 1024) return status('文件超过80 MiB，请选择较小的音频。', true);
  setBusy(true); status('正在上传并分析 ' + file.name + '，音频保存在本机。');
  try {
    const track = await api('/api/tracks?name=' + encodeURIComponent(file.name), file, true);
    tracks = await api('/api/tracks');
    clearProject(); fillSources({ [key]: track.id });
    status('已加入 ' + track.name + '。速度估计仅供参考，你可以手动校正。');
  } catch (error) { status(error.message, true); }
  finally { $('file-' + key).value = ''; setBusy(false); }
}
function evidenceRows(hits) {
  const root = $('evidence'); root.replaceChildren();
  root.append(node('p', '以下是检索得到的方法候选。它们尚未验证是否适合这两份素材，不代表专家已听过本次混音。'));
  for (const hit of hits) {
    const row = node('p', hit.card.id + ' · ' + hit.card.title + '：' + hit.card.summary + ' ');
    for (const source of hit.sources) if (source.url && /^https:\/\//.test(source.url)) {
      const link = node('a', source.publisher || source.id);
      link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.append(link);
    }
    root.append(row);
  }
}
async function recommend() {
  const arrangement=$('arrangement')?.value || 'together';
  setBusy(true); status(arrangement==='acappella_drop' ? '正在分析开头旋律与候选拍点，第一次可能稍久。完成后给你三个可调整的切入方案。' : '正在结合素材信息，整理三个可调整的起点。');
  try {
    const result = await api('/api/recommendations', {
      a_id: $('track-a').value, b_id: $('track-b').value, mode: $('mode').value,
      a_role: $('role-a').value, b_role: $('role-b').value, prompt: $('prompt').value, arrangement
    });
    const root = $('candidates'); root.replaceChildren();
    result.candidates.forEach((candidate, index) => {
      const card = node('article', undefined, 'candidate');
      card.append(node('span', 'START / 0' + (index + 1), 'number'), node('h3', candidate.name), node('p', candidate.reason));
      const reasons = node('ul');
      for (const reason of candidate.explanations) reasons.append(node('li', reason));
      card.append(reasons);
      const choose = node('button', '用这版继续 ↗');
      choose.addEventListener('click', () => {
        remember(); project = clone(candidate.project); invalidate(); refreshControls();
        document.querySelectorAll('.candidate').forEach(element => element.classList.remove('selected'));
        card.classList.add('selected');
        $('editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
        status('已选“' + candidate.name + '”。现在可以手动调整，再生成真实试听。');
      });
      card.append(choose); root.append(card);
    });
    $('intent-note').textContent = result.recognized_intents.length ? '记录到：' + result.recognized_intents.join(' / ') : '灵感已保存为备注，请亲手细化';
    if (result.cue_analysis) $('intent-note').textContent=result.cue_analysis.status==='measured_rise' ? `推荐在 ${result.cue_analysis.entry_seconds.toFixed(2)} 秒加入伴奏 · 可提前或延后比较` : '没有检出可靠的旋律上行 · 已提供可调整的手动试听起点';
    evidenceRows(result.knowledge_candidates);
    $('recommendation-section').hidden = false;
    status(result.cue_analysis ? '切入方案已准备好，先选一版试听；原因在卡片里，切入时间仍由你决定。' : result.requires_manual_interpretation ? '三个起点已准备好。本版未理解这段具体意象，已保留原文；请用控制项细化。' : '三个起点已准备好，选择一个继续调整。');
    $('recommendation-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { status(error.message, true); }
  finally { setBusy(false); }
}
async function render(preview, requestId = undefined) {
  if (busy || !project) return {ok:false,error:'工程未准备好或正在处理'};
  const sent = clone(project), signature = JSON.stringify(sent);
  setBusy(true); status(preview ? '正在真实渲染交接片段，完成后可播放。' : '正在完成整首音频、编码并测量输出，请稍候。');
  let task, job;
  try {
    task = await api('/api/renders', { project: sent, preview, approved_preview_id: approvedPreview, request_id: requestId });
    const deadline = Date.now() + 20 * 60 * 1000;
    while (Date.now() < deadline) {
      job = await api('/api/jobs/' + task.id);
      if (job.status === 'failed') throw new Error(job.error || '处理失败，请调整后重试');
      if (job.status === 'succeeded') break;
      await new Promise(resolve => setTimeout(resolve, 700));
    }
    if (!job || job.status !== 'succeeded') throw new Error('等待超时，请稍后检查任务，不要重复提交。');
    if (signature !== JSON.stringify(project)) {
      status('渲染已完成，但参数在处理中改变了。请为当前参数重新生成试听。');
      return {ok:false,error:'工程已更新，请为当前版本重新生成试听',task_id:task.id};
    }
    if (preview) {
      approvedPreview = task.id;
      $('preview-audio').src = '/outputs/' + task.id + '/audio.mp3';
      $('preview-audio').dataset.stageOffset=String(job.result.preview_start_seconds);
      $('preview-audio').dataset.stageMix='true';
      $('preview-audio').hidden = false;
      if(typeof workbenchDidRender==='function')workbenchDidRender('preview-audio');
      $('preview-note').textContent = `本段从作品第${job.result.preview_start_seconds.toFixed(1)}秒开始，时长${seconds(job.result.duration)}。请播放确认，再完整导出。`;
      status('真实试听已完成。听完可继续调整，或确认这版并完整导出。');
    } else {
      $('full-audio').src = '/outputs/' + task.id + '/audio.mp3';
      $('full-audio').dataset.stageOffset='0';$('full-audio').dataset.stageMix='true';
      const links = $('download-links'); links.replaceChildren();
      for (const [file, label] of [['audio.wav', '下载 WAV'], ['audio.mp3', '下载 MP3'], ['project.json', '保存可编辑工程']]) {
        const link = node('a', label + ' ↓'); link.href = '/outputs/' + task.id + '/' + file + '?download=1';
        link.download = file; links.append(link);
      }
      const quality = job.result.quality.mp3;
      $('quality').textContent = `完整时长${seconds(job.result.duration)} · MP3响度 ${quality.integrated_lufs ?? '未测得'} LUFS · 真峰值 ${quality.true_peak_dbtp ?? '未测得'} dBTP。${job.result.quality.status === 'review_required' ? '机器检查提示需要复核。' : '机器检查通过，仍请试听确认听感。'}工程引用本机素材，支持重新载入调整。`;
      $('downloads').hidden = false;
      if(typeof workbenchDidRender==='function')workbenchDidRender('full-audio');
      status('完整作品已导出。可以试听整首，并下载音频和可编辑工程。');
    }
    return {ok:true,task_id:task.id,preview,result:job.result};
  } catch (error) { status(error.message, true); return {ok:false,error:error.message,task_id:task?.id,task_failed:job?.status==='failed'}; }
  finally { setBusy(false); }
}
function remapProjectAssets(bundle,inventory) {
  const p=clone(bundle.project),mapped=new Map();
  function match(id){
    if(mapped.has(id))return mapped.get(id);
    const asset=bundle.assets.find(a=>a.id===id);
    const t=inventory.find(t=>asset&&typeof asset.sha256==='string'&&t.sha256===asset.sha256);
    if(!t)throw Error('缺少工程素材，请先导入：'+(asset?.name||id));
    mapped.set(id,t.id);return t.id;
  }
  const slots=p.kind==='continuous_set'?p.clips:[p.a,p.b];
  for(const s of slots){
    s.track_id=match(s.track_id);
    for(const e of s.effects||[])if(e.type==='sidechain')e.key_track_id=match(e.key_track_id);
  }
  for(const e of p.performance?.events||[])e.track_id=match(e.track_id);
  for(const g of Object.values(p.musical_guides||{}))g.track_id=match(g.track_id);
  return p;
}
async function importProject(input) {
  const file = input.files[0]; if (!file) return;
  try {
    if (file.size > 256 * 1024) throw new Error('工程文件过大，本版只接受256 KiB以内的JSON工程。');
    const bundle = JSON.parse(await file.text());
    if (bundle.format !== 'deepmixing-project-v1' || !bundle.project || !Array.isArray(bundle.assets)) throw new Error('请选择deepmixing导出的工程文件。');
    const restored = remapProjectAssets(bundle,tracks);
    remember(); project = restored;
    invalidate(); refreshControls();
    $('editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
    status('工程已载入，原素材匹配成功。调整后重新试听即可导出新版本。');
  } catch (error) { status(error.message, true); }
  finally { input.value = ''; }
}
buildTrackControls();
for (const key of ['a', 'b']) {
  $('file-' + key).addEventListener('change', () => upload(key));
  $('track-' + key).addEventListener('change', () => { clearProject(); showSource(key); refreshButtons(); });
  $('role-' + key).addEventListener('change', clearProject);
}
$('mode').addEventListener('change', clearProject);
document.addEventListener('change', event => {
  const input = event.target;
  if (!input.dataset.path || !project) return;
  const parts = input.dataset.path.split('.');
  let value = input.type === 'checkbox' ? input.checked : input.type === 'number' || input.type === 'range' ? (input.value === '' ? null : Number(input.value)) : input.value;
  if (typeof value === 'number' && !Number.isFinite(value)) return status('请输入有效数字。', true);
  updateProject(next => { if (parts.length === 2) next[parts[0]][parts[1]] = value; else next[parts[0]] = value; });
});
$('prompt').addEventListener('change', () => { if (project) updateProject(next => { next.prompt = $('prompt').value; }); });
for (const button of document.querySelectorAll('[data-mood]')) button.addEventListener('click', () => {
  $('prompt').value = [$('prompt').value, button.dataset.mood].filter(Boolean).join('，');
  if (project) updateProject(next => { next.prompt = $('prompt').value; });
});
$('recommend').addEventListener('click', recommend);
$('preview').addEventListener('click', () => render(true));
$('export').addEventListener('click', () => render(false));
$('undo').addEventListener('click', () => {
  if (!undoStack.length || !project) return;
  redoStack.push(clone(project)); project = undoStack.pop(); invalidate(); refreshControls(); status('已撤销上一次参数调整。');
});
$('redo').addEventListener('click', () => {
  if (!redoStack.length || !project) return;
  undoStack.push(clone(project)); project = redoStack.pop(); invalidate(); refreshControls(); status('已重做参数调整。');
});
for (const id of ['import-project', 'import-anywhere']) $(id).addEventListener('change', () => importProject($(id)));
(async () => {
  setBusy(true);
  try {
    const health = await api('/api/status');
    tracks = await api('/api/tracks'); fillSources();
    $('connection').textContent = health.ready ? '● 本地引擎已连接' : '引擎未就绪';
  } catch (error) { $('connection').textContent = '连接失败'; status(error.message, true); }
  finally { setBusy(false); }
})();
