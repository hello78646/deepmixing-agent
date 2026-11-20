'use strict';
function refreshCueControls() {
  const select=$('arrangement');
  if (!select) return;
  const mashup=$('mode').value==='mashup';
  select.disabled=busy || !mashup;
  $('cue-opening').hidden=!mashup;
  if (!mashup) select.value='together';
  $('recommend').textContent=mashup && select.value==='acappella_drop' ? '分析旋律 · 给我三个切入点 ↗' : '给我三个起点 ↗';
}
const cueLabel=node('label','开场方式');cueLabel.id='cue-opening';
const cueSelect=node('select');cueSelect.id='arrangement';
for (const [value,text] of [['together','人声与伴奏一起进入'],['acappella_drop','先清唱，在旋律抬起处加入伴奏']]) {
  const option=node('option',text);option.value=value;cueSelect.append(option);
}
cueSelect.value='together';
const cueHint=node('small','Agent先选一个位置并解释。你可以早一点、晚一点，或自己改时间。');
cueLabel.append(cueSelect,cueHint);$('mode').parentElement.after(cueLabel);
$('mode').addEventListener('change',refreshCueControls);
cueSelect.addEventListener('change',refreshCueControls);
refreshCueControls();
