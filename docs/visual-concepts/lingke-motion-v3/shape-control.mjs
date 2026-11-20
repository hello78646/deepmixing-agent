import {SHAPES,phaseAt,transitionPoints} from './motion.mjs';
export function automaticTrackPositions(track,time,out){
  const local=phaseAt(Math.max(0,time-track.index*.22));
  const from=local.cycle===0?track.initial:track.targets[(local.cycle-1+track.index)%4];
  transitionPoints(out,from,track.cloud,track.targets[(local.cycle+track.index)%4],local);
}
export function applyShapeSelection(track,shape,time,immediate){
  if(immediate){
    if(shape==='auto')automaticTrackPositions(track,time,track.attribute.array);
    else track.attribute.array.set(track.targets[SHAPES.indexOf(shape)]);
    track.switchFrom=null;track.attribute.needsUpdate=true;
  }else{track.switchFrom=track.attribute.array.slice();track.switchAt=time;}
}
