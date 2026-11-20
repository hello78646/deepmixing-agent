'use strict';
const guideReports=new Map();
let localHarmonyEvidence=null;
const guidePanel=node('section',undefined,'music-guides');guidePanel.id='music-guides';
guidePanel.append(node('p','FIND YOUR ENTRY','eyebrow'),node('h3','对拍与搭配检查'),node('p','先检查，再亲手决定。网格是候选脉冲；变化标记供接歌参考，不代表已确认的强拍或完整乐句。','hint'));
const inspectButton=node('button','检查当前选段 ↗','secondary');inspectButton.id='music-inspect';guidePanel.append(inspectButton);
const guideBody=node('div');guideBody.id='guide-body';guidePanel.append(guideBody);
$('editor').append(guidePanel);
function reportKey(g){return `${g.track_id}:${g.start}:${g.end}`;}
function guideUsable(s){const g=project?.musical_guides?.[s],p=project?.[s];return g&&g.track_id===p.track_id&&g.marker>=p.start&&g.marker<=p.end&&g.start<=p.start&&g.end>=Math.min(p.end,p.start+60)-.002;}
function refreshMusicGuideControls(){
  inspectButton.disabled=busy||!project;
  for(const input of guideBody.querySelectorAll?.('input,select,button')||[])input.disabled=busy;
  if($('guide-align'))$('guide-align').disabled=busy||!project||project.mode!=='mashup'||!['a','b'].every(guideUsable);
  if($('guide-tempo'))$('guide-tempo').disabled=busy||!['a','b'].every(guideUsable);
}
function guideEdit(s,field,value){
  if(busy||!project)return;
  const g=project.musical_guides[s];
  if(field!=='key' && (value!==null&&!Number.isFinite(value)||field==='bpm'&&value!==null&&(value<40||value>240)||field!=='bpm'&&(value===null||value<g.start||value>g.end))){status('数值超出当前检查区域或有效BPM范围。',true);refreshMusicGuides();return;}
  updateProject(p=>{
    const next=p.musical_guides[s];
    if(field==='key'){const [t,m]=value.split(':');next.key_tonic=value===''?null:Number(t);next.key_mode=value===''?null:m;next.key_source=value===''?'unknown':'user';}
    else next[field]=value;
  });
  status('检查标记已保存。尚未修改声音；可按标记切入、对速，或继续试听。');
}
function drawGuide(s,g,r){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 960 120');svg.setAttribute('role','img');svg.setAttribute('aria-label',s.toUpperCase()+'轨波形，点击设标记；精确位置也可在下方输入');svg.classList.add?.('guide-wave');
  const x=t=>960*(t-g.start)/(g.end-g.start);
  const add=(tag,attrs)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);svg.append(e);};
  if(r){const max=Math.max(.001,...r.waveform);add('path',{d:r.waveform.map((v,i)=>`M${i*960/r.waveform.length},${60-v/max*43}v${v/max*86}`).join(' '),class:'guide-peaks'});}
  for(const t of MusicGuideMath.gridPoints(g))add('line',{x1:x(t),x2:x(t),y1:8,y2:112,class:'guide-gridline'});
  for(const t of MusicGuideMath.barPoints(g))add('line',{x1:x(t),x2:x(t),y1:0,y2:120,class:'guide-barline'});
  for(const c of r?.changes||[])add('circle',{cx:x(c.time),cy:10,r:4,class:'guide-change'});
  add('line',{x1:x(g.marker),x2:x(g.marker),y1:0,y2:120,class:'guide-marker'});
  svg.addEventListener('click',event=>{if(busy)return;const rect=svg.getBoundingClientRect();guideEdit(s,'marker',Math.round(Math.max(g.start,Math.min(g.end,g.start+(event.clientX-rect.left)/rect.width*(g.end-g.start)))*1000)/1000);});
  return svg;
}
function refreshMusicGuides(){
  guideBody.replaceChildren();if(!project)return;
  const gs=project.musical_guides;
  if(!gs||!gs.a||!gs.b){refreshMusicGuideControls();return;}
  const columns=node('div',undefined,'guide-columns');guideBody.append(columns);
  for(const s of ['a','b']){
    const g=gs[s],r=guideReports.get(reportKey(g)),panel=node('article',undefined,'guide-track');
    panel.append(node('h4',s.toUpperCase()+' · '+g.start.toFixed(2)+'–'+g.end.toFixed(2)+' 秒'),node('p',guideUsable(s)?'原素材时间 · 点击波形设标记':'选段已改变，当前标记不适用，请重新检查。','hint'),drawGuide(s,g,r));
    if(!r)panel.append(node('p','已恢复工程标记；重新检查可恢复波形与变化候选。','hint'));
    if(r){
      const evidence=node('details',undefined,'guide-evidence');evidence.append(node('summary','查看检测依据与调性备选'));
      evidence.append(node('p',r.grid.bpm?`候选脉冲 ${r.grid.bpm.toFixed(3)} BPM；仍需校正半速、倍速和网格相位。`:'节奏证据不足，BPM保留未知。'));
      for(const k of r.key.candidates||[])evidence.append(node('p',`${k.name} · 模板相关度 ${k.profile_score}（不是正确率）`));
      evidence.append(node('p','波形按各轨峰值缩放，只用于定位。色谱与能量变化不是已确认的乐句；调性按局部谐波估计，清唱和转调可能不稳定。'));panel.append(evidence);
    }
    const fields=node('div',undefined,'guide-fields');
    for(const [field,label] of [['bpm','候选 / 手动 BPM'],['anchor','网格起点 / 秒'],['marker','切入标记 / 秒']]){
      const l=node('label',label),input=node('input');input.id=`guide-${s}-${field}`;input.type='number';input.step='.001';input.min=field==='bpm'?40:g.start;input.max=field==='bpm'?240:g.end;input.value=g[field]??'';
      input.addEventListener('change',()=>guideEdit(s,field,input.value===''?null:Number(input.value)));l.append(input);fields.append(l);
    }
    const label=node('label','局部调性 · '+({'estimated':'算法候选','user':'手动指定','unknown':'未确定'}[g.key_source])),select=node('select');select.id=`guide-${s}-key`;select.add(new Option('未知 / 先试听',''));
    const notes=['C','C♯','D','E♭','E','F','F♯','G','A♭','A','B♭','B'];
    for(const mode of ['major','minor'])notes.forEach((n,i)=>select.add(new Option(n+(mode==='major'?' 大调':' 小调'),i+':'+mode)));
    select.value=g.key_tonic===null?'':g.key_tonic+':'+g.key_mode;select.addEventListener('change',()=>guideEdit(s,'key',select.value));label.append(select);fields.append(label);panel.append(fields);
    const actions=node('div',undefined,'guide-actions');
    const snap=node('button','标记吸附到候选网格','quiet');snap.addEventListener('click',()=>guideEdit(s,'marker',MusicGuideMath.snapTime(g.marker,g)));actions.append(snap);
    const listen=node('button','从标记前1秒试听原素材','quiet');listen.addEventListener('click',()=>{const audio=$('source-'+s);audio.currentTime=Math.max(0,g.marker-1);audio.play().catch(e=>status(e.message,true));});actions.append(listen);
    for(const c of r?.changes||[]){const b=node('button',c.time.toFixed(2)+'秒 · '+c.reason,'quiet');b.addEventListener('click',()=>guideEdit(s,'marker',c.time));actions.append(b);}
    panel.append(actions);if(typeof enrichGuidePanel==='function')enrichGuidePanel(s,g,r,panel);columns.append(panel);
  }
  const harmony=node('p',['a','b'].every(guideUsable)?MusicGuideMath.keyRelation(gs.a,gs.b,project.a.semitones,project.b.semitones).message:'选段已改变，请重新检查当前选段后再查看调性搭配。','guide-harmony');guideBody.append(harmony);
  const chord=localHarmonyEvidence?.comparison?.chord_evidence;
  if(chord&&['a','b'].every(guideUsable)){
    const statusText=chord.status==='estimated'?'局部和弦候选未发现明显冲突，仍需试听。':chord.status==='review'?'局部和弦候选存在冲突风险，建议短接、换段或轮换旋律主角。':'局部和弦证据不足，不能把未知当作兼容。';
    const detail=chord.shared_tone_ratio==null?'':'共同音 '+(chord.shared_tone_ratio*100).toFixed(1)+'% · 冲突音 '+(chord.conflict_ratio*100).toFixed(1)+'%';
    guideBody.append(node('p',`和弦级兼容候选：${statusText}${detail?' '+detail+'。':''}`,'guide-harmony'));
  }
  const buttons=node('div',undefined,'guide-actions');
  for(const [id,label,fn] of [['guide-align','按两个标记切入',MusicGuideMath.alignMarkers],['guide-tempo','以 B 律动为基准对速',MusicGuideMath.matchTempo]]){
    const b=node('button',label,'secondary');b.id=id;b.addEventListener('click',()=>{if(busy)return;try{const q=fn(project,project.musical_guides);updateProject(p=>Object.assign(p,q));status('已更新试听工程。请重新生成试听，确认声音后再导出。');}catch(e){status(e.message,true);}});buttons.append(b);
  }
  if(['a','b'].every(guideUsable))for(const option of MusicGuideMath.pitchOptions(gs.a,gs.b,project.a.semitones)){
    const b=node('button',`试听方案：B ${option.semitones>=0?'+':''}${option.semitones}半音`,'quiet');b.disabled=busy||project.b.preserve_original&&option.semitones!==0;b.title=option.message+' 变调可能改变音色；点击后重新渲染试听。';b.addEventListener('click',()=>{if(busy)return;try{const q=MusicGuideMath.applyPitch(project,'b',option.semitones);updateProject(p=>Object.assign(p,q));status(option.message+' 已修改B轨移调，请重新生成试听比较音色。');}catch(e){status(e.message,true);}});buttons.append(b);
  }
  guideBody.append(buttons,node('p','检查每轨选段开头最多60秒。建议先对速，再按标记切入。不自动移调；操作后重新渲染才改变声音。调性候选不保证和弦兼容。','hint'));refreshMusicGuideControls();
}
inspectButton.addEventListener('click',async()=>{
  if(busy||!project)return;const sent=clone(project),signature=JSON.stringify(sent);setBusy(true);status('正在检查选段的节奏、调性与变化位置，首次分析可能稍久。');
  try{
    const result=await api('/api/music/inspect',{project:sent});
    if(signature!==JSON.stringify(project))throw Error('工程已改变，请重新检查当前选段。');
    const gs={};for(const s of ['a','b']){const r=result[s];if(guideReports.size>=16)guideReports.delete(guideReports.keys().next().value);guideReports.set(reportKey(r),r);gs[s]=MusicGuideMath.guideFromReport(r,project.musical_guides?.[s]);}
    localHarmonyEvidence=result.local_harmony||null;
    updateProject(p=>{p.musical_guides=gs});status('候选标记已准备好。同一区域的手动校正和仍在范围内的切入标记已保留；先试听，再决定是否对速或切入。');
  }catch(e){status(e.message,true);}finally{setBusy(false);}
});
