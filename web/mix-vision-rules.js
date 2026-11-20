/* Pure playback and gesture rules, shared with the tests. */
(function(root){
  'use strict';
  const players={'preview-audio':'mix_preview','full-audio':'mix_full','set-audio':'set'};
  const kinds=new Set(['mix_preview','mix_full','set_preview','set_full']);
  const focusLabels=Object.freeze({transition:'转场衔接',groove:'节奏律动',balance:'人声与伴奏',tone:'音色与空间'});
  const output=/^\/outputs\/([0-9a-f]{32})\/audio\.(?:mp3|wav)$/;
  const idPattern=/^[0-9a-f]{32}$/;
  function point(audio,origin){
    const type=players[audio?.id];
    if(!type||!audio.currentSrc||audio.seeking||audio.ended||audio.readyState<2||audio.error)return null;
    let url;
    try{url=new URL(audio.currentSrc,origin);}catch{return null;}
    const rendered=output.exec(url.pathname);
    if(url.origin!==origin||!rendered||!Number.isFinite(audio.duration)||audio.duration<=0)return null;
    if(audio.getAttribute('src')===null)return null;
    try{if(new URL(audio.getAttribute('src'),origin).href!==url.href)return null;}catch{return null;}
    const offset=Number(audio.dataset.stageOffset??(type==='mix_full'?0:NaN));
    if(!Number.isFinite(offset)||offset<0||!Number.isFinite(audio.currentTime)||audio.currentTime<0||audio.currentTime>audio.duration+.05)return null;
    const kind=type==='set'?`set_${audio.dataset.outputKind==='full'?'full':'preview'}`:type;
    return {output_id:rendered[1],kind,offset,sec:audio.currentTime,duration:audio.duration,src:url.href};
  }
  const round=(value)=>Math.round(value*1000)/1000;
  function segment(p){
    const end=Math.min(p.sec,p.duration);
    if(end<.25)return null;
    return {output_id:p.output_id,kind:p.kind,start_seconds:round(p.offset+Math.max(0,end-5)),end_seconds:round(p.offset+end)};
  }
  function valid(row){
    return Boolean(row&&typeof row.output_id==='string'&&idPattern.test(row.output_id)&&kinds.has(row.kind)&&
      (row.focus===undefined||typeof row.focus==='string'&&Object.hasOwn(focusLabels,row.focus))&&
      Number.isFinite(row.start_seconds)&&row.start_seconds>=0&&Number.isFinite(row.end_seconds)&&
      row.end_seconds>row.start_seconds&&row.end_seconds-row.start_seconds<=5.002);
  }
  function sameSection(a,b){return a.output_id===b.output_id&&a.kind===b.kind&&Math.abs(a.end_seconds-b.end_seconds)<2;}
  function fresh(sent,now,at,epoch){
    return Boolean(now&&sent.epoch===epoch&&at-sent.at<750&&sent.point.src===now.src&&sent.point.offset===now.offset&&now.sec>=sent.point.sec-.06);
  }
  function thumbGate(){
    let armed,neutral,first,hits,last;
    function reset(){armed=false;neutral=0;first=null;hits=0;last=-Infinity;}
    reset();
    return {reset,step(point,at,signal){
      if(at-last>1200)reset();
      last=at;
      if(signal==='uncertain'){neutral=0;first=null;hits=0;return null;}
      if(signal==='neutral'){
        if(++neutral>=2)armed=true;
        first=null;hits=0;return null;
      }
      neutral=0;
      if(!armed)return null;
      first??={point,at};
      if(++hits<3||at-first.at<150)return null;
      const hit=first.point;armed=false;first=null;hits=0;
      return hit;
    }};
  }
  const api={point,segment,valid,sameSection,fresh,thumbGate,focusLabels};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.DeepmixingVisionRules=api;
})(globalThis);
