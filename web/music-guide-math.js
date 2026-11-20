'use strict';
(function(root){
  const copy=value=>JSON.parse(JSON.stringify(value));
  function gridPoints(g){
    if(!g || !Number.isFinite(g.bpm) || g.bpm<40 || g.bpm>240) return [];
    const step=60/g.bpm, points=[];
    for(let i=Math.ceil((g.start-g.anchor)/step-1e-8);points.length<250;i++){
      const t=g.anchor+i*step;if(t>g.end+1e-8)break;
      points.push(Math.round(t*1e6)/1e6);
    }return points;
  }
  function snapTime(time,g){const points=gridPoints(g);return points.length?points.reduce((a,b)=>Math.abs(a-time)<=Math.abs(b-time)?a:b):Math.max(g.start,Math.min(g.end,time));}
  function check(p,gs){
    for(const s of ['a','b']){
      const g=gs?.[s];if(!g || g.track_id!==p[s].track_id || !Number.isFinite(g.marker) || g.marker<g.start || g.marker>g.end)throw Error('请先检查当前两轨，并确认标记来源与区域。');
    }
  }
  function alignMarkers(p,gs){
    check(p,gs);if(p.mode!=='mashup')throw Error('按两个标记切入适用于人声与伴奏叠搭。');
    const q=copy(p),speed=p.target_bpm?p.target_bpm/p.a.bpm:1;
    const offset=(gs.a.marker-p.a.start)/speed;
    if(!Number.isFinite(offset)||offset<0||offset>60||gs.a.marker>p.a.end||gs.b.marker<p.b.start||p.b.end-gs.b.marker<.25)throw Error('标记超出选段、切入范围或伴奏剩余不足0.25秒。');
    q.b_offset_seconds=offset;q.b.start=gs.b.marker;return q;
  }
  function matchTempo(p,gs){
    check(p,gs);const q=copy(p);q.target_bpm=gs.b.bpm;
    if(!q.target_bpm)throw Error('请先确认两轨BPM。');
    for(const s of ['a','b']){
      const bpm=gs[s].bpm,ratio=q.target_bpm/bpm;
      if(!bpm||ratio<.5||ratio>2)throw Error('请确认两轨BPM，并保持0.5至2倍变速。');
      if(p[s].preserve_original&&Math.abs(ratio-1)>1e-8)throw Error('保持原貌已锁定，请明确解除锁定后再对速。');
      q[s].bpm=bpm;
    }return q;
  }
  function keyRelation(a,b,as=0,bs=0){
    const unknown={relation:'unknown',message:'调性依据不足，或使用非整数移调，请先试听判断。'};
    if([a,b].some(g=>!g||!['estimated','user'].includes(g.key_source)||!Number.isInteger(g.key_tonic)||!['major','minor'].includes(g.key_mode))||![as,bs].every(Number.isInteger))return unknown;
    const delta=((b.key_tonic+bs-a.key_tonic-as)%12+12)%12;
    if(a.key_mode===b.key_mode&&delta===0)return{relation:'same',message:'候选调性相同：可先试原调叠搭，和弦与旋律仍需试听核对。'};
    if(a.key_mode==='major'&&b.key_mode==='minor'&&delta===9||a.key_mode==='minor'&&b.key_mode==='major'&&delta===3)return{relation:'relative',message:'候选为关系大小调：可作为叠搭起点，仍需检查和弦冲突。'};
    if(a.key_mode===b.key_mode&&[5,7].includes(delta))return{relation:'neighbor',message:'候选为五度邻近：适合先试，不保证整段和声兼容。'};
    return{relation:'review',message:'候选调性距离较远：可换片段、缩短叠加或手动试移调。'};
  }
  function remapGuides(p,slot,id){const q=copy(p);q[slot].track_id=id;if(q.musical_guides?.[slot])q.musical_guides[slot].track_id=id;return q;}
  function guideFromReport(r,old){
    const g={track_id:r.track_id,start:r.start,end:r.end,bpm:r.grid.bpm,anchor:r.grid.anchor,marker:r.grid.anchor,key_tonic:r.key.tonic,key_mode:r.key.mode,key_source:r.key.status};
    if(old?.track_id===r.track_id){
      if(old.marker>=r.start&&old.marker<=r.end)g.marker=old.marker;
      if(Math.abs(old.start-r.start)<.00001&&Math.abs(old.end-r.end)<.00001){
        for(const k of ['bpm','anchor','key_tonic','key_mode','key_source'])g[k]=old[k];
        if(Number.isFinite(old.bar_anchor)){g.bar_anchor=old.bar_anchor;g.beats_per_bar=old.beats_per_bar;}
      }
    }return g;
  }
  function calibrateGrid(g,first,last,intervals){
    if(![first,last,intervals].every(Number.isFinite)||!Number.isInteger(intervals)||intervals<1||intervals>128||first<g.start||last>g.end||last<=first)throw Error('请选择区域内两个拍点，并填入1–128之间的相隔拍数。');
    const bpm=60*intervals/(last-first);if(bpm<40||bpm>240)throw Error('计算的BPM超出40–240，请核对相隔拍数。');
    return {...g,bpm:Math.round(bpm*1000)/1000,anchor:first};
  }
  function phraseReferences(g,changes,bars=8){
    if(!g.bpm||!Number.isFinite(g.bar_anchor))return [];
    const beat=60/g.bpm,span=beat*(g.beats_per_bar||4)*bars,seen=new Set(),out=[];
    for(const c of changes){const cycle=Math.round((c.time-g.bar_anchor)/span),time=g.bar_anchor+cycle*span;
      if(cycle!==0&&time>g.start&&time<g.end&&Math.abs(time-c.time)<=beat*2&&!seen.has(time)){
        seen.add(time);out.push({time:Math.round(time*1e6)/1e6,evidence_time:c.time,bars,reason:c.reason});
      }
    }return out.sort((a,b)=>a.time-b.time);
  }
  function pitchOptions(a,b,as=0){
    const priority={same:0,relative:1,neighbor:2};return [-3,-2,-1,0,1,2,3].map(s=>({semitones:s,...keyRelation(a,b,as,s)}))
      .filter(o=>o.relation in priority).sort((x,y)=>priority[x.relation]-priority[y.relation]||Math.abs(x.semitones)-Math.abs(y.semitones)).slice(0,3);
  }
  function applyPitch(p,s,semitones){
    if(!['a','b'].includes(s)||!Number.isInteger(semitones)||Math.abs(semitones)>12)throw Error('请选择有效的整半音移调。');
    if(p[s].preserve_original&&semitones!==0)throw Error('保持原貌已锁定，不能移调。');
    const q=copy(p);q[s].semitones=semitones;return q;
  }
  function barPoints(g){
    if(!g||![3,4].includes(g.beats_per_bar)||!Number.isFinite(g.bar_anchor)||!Number.isFinite(g.start)||!Number.isFinite(g.end)||!Number.isFinite(g.bpm)||g.bpm<40||g.bpm>240)return [];
    const step=60/g.bpm*g.beats_per_bar,first=g.bar_anchor+Math.ceil((g.start-g.bar_anchor)/step)*step,points=[];
    for(let i=0;i<250;i++){const t=first+i*step;if(t>g.end)break;points.push(Math.round(t*1e6)/1e6);}return points;
  }
  const api={gridPoints,snapTime,alignMarkers,matchTempo,keyRelation,remapGuides,guideFromReport,calibrateGrid,phraseReferences,pitchOptions,applyPitch,barPoints};
  if(typeof module!=='undefined')module.exports=api;else root.MusicGuideMath=api;
})(globalThis);
