'use strict';
function enrichGuidePanel(s,g,r,panel){
  const box=node('details',undefined,'guide-corrections');box.append(node('summary','精校拍网 / 乐句参考'));
  const fields=node('div',undefined,'guide-fields'),inputs={};
  for(const [id,label,value] of [['first','首个拍点 / 秒',g.anchor],['last','后一个拍点 / 秒',Math.min(g.end,g.anchor+8*60/(g.bpm||120))],['intervals','两个拍点相隔几拍',8]]){
    const l=node('label',label),i=node('input');i.id=`calibrate-${s}-${id}`;i.type='number';i.step=id==='intervals'?1:.001;i.value=value;inputs[id]=i;l.append(i);fields.append(l);
  }
  box.append(node('p','例如第1拍到第9拍，相隔8拍。先用波形和原素材核对两个拍点。','hint'),fields);
  const actions=node('div',undefined,'guide-actions');
  const correct=node('button','用两个拍点校正','quiet');correct.id=`calibrate-${s}-apply`;
  correct.addEventListener('click',()=>{if(busy)return;try{const q=MusicGuideMath.calibrateGrid(g,...['first','last','intervals'].map(k=>Number(inputs[k].value)));updateProject(p=>{p.musical_guides[s]=q});status('已用两个拍点校正网格。这里只更新标记，尚未改变音频速度。');}catch(e){status(e.message,true);}});actions.append(correct);
  for(const [label,ratio] of [['BPM ÷ 2',.5],['BPM × 2',2]]){const b=node('button',label,'quiet');b.disabled=!g.bpm;b.addEventListener('click',()=>guideEdit(s,'bpm',g.bpm?g.bpm*ratio:null));actions.append(b);}
  const bar=node('button','把切入标记确认为4/4小节首拍','quiet');bar.id=`guide-${s}-bar`;bar.addEventListener('click',()=>{if(busy)return;updateProject(p=>Object.assign(p.musical_guides[s],{bar_anchor:g.marker,beats_per_bar:4}));status('已按你的判断设置4/4首拍。8/16小节只是编排参考，仍需试听乐句。');});actions.append(bar);box.append(actions);
  const d=r?.grid.diagnostics;if(d?.bpm)box.append(node('p',`均匀网格最大残差 ${Math.round(d.max_residual_seconds*1000)} ms · ${d.message}`,'hint'));
  if(Number.isFinite(g.bar_anchor)){
    box.append(node('p',`手动4/4参考首拍 ${g.bar_anchor.toFixed(3)} 秒。候选需同时靠近8/16小节位置与实测变化。`,'hint'));
    let count=0;
    for(const bars of [8,16])for(const c of MusicGuideMath.phraseReferences(g,r?.phrase_changes?.length?r.phrase_changes:r?.changes||[],bars)){
      count++;const b=node('button',`${bars}小节参考 ${c.time.toFixed(2)}秒（变化在${c.evidence_time.toFixed(2)}秒）`,'quiet');b.addEventListener('click',()=>guideEdit(s,'marker',c.time));box.append(b);
    }
    if(!count)box.append(node('p','当前范围没有同时满足网格和变化证据的边界候选；可继续手动标记。','hint'));
  }
  panel.append(box);
  if(r?.key_windows?.windows?.length){
    const keys=node('details',undefined,'guide-evidence');keys.append(node('summary','分段调性与变化检查'));
    keys.append(node('p',r.key_windows.status==='varying_candidates'?'局部候选不一致，请检查是否转调或估计不稳定；不宜只用整段平均调性。':'按8秒窗口、4秒步进检查；未知窗口保留未知。','hint'));
    for(const w of r.key_windows.windows)keys.append(node('p',`${w.start.toFixed(1)}–${w.end.toFixed(1)}秒 · ${w.key.name}`));panel.append(keys);
  }
}
