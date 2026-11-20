/* Local worker. Only an intentional gesture class crosses back to the UI. */
'use strict';
const asset=(name)=>new URL(`/assets/mix-vision/${name}`,self.location).href;
let recognizer;
self.onmessage=async({data})=>{
  if(data.type==='init'){
    let code='asset';
    try{
      const response=await fetch(asset('gesture_recognizer.task'));
      if(!response.ok)throw Error('model asset');
      const model=new Uint8Array(await response.arrayBuffer());
      if(!model.length)throw Error('empty model');
      code='init';
      self.exports={};
      importScripts(asset('mediapipe.js'));
      const {GestureRecognizer}=self.exports;
      // Use one portable WASM build instead of shipping two engines.
      recognizer=await GestureRecognizer.createFromOptions({
        wasmLoaderPath:asset('vision_wasm_nosimd_internal.js'),
        wasmBinaryPath:asset('vision_wasm_nosimd_internal.wasm'),
      },{baseOptions:{modelAssetBuffer:model,delegate:'CPU'},runningMode:'VIDEO',numHands:2,
        minHandDetectionConfidence:.6,minHandPresenceConfidence:.6,minTrackingConfidence:.6});
      self.postMessage({type:'ready'});
    }catch{self.postMessage({type:'error',code});}
    return;
  }
  if(data.type!=='frame')return;
  const frame=data.frame;
  try{
    const {gestures,landmarks}=recognizer.recognizeForVideo(frame,data.at);
    const candidate=gestures[0]?.[0];
    const hands=landmarks.length;
    const signal=hands>1?'uncertain':candidate?.categoryName==='Thumb_Up'?(candidate.score>=.67?'thumb':'uncertain'):'neutral';
    self.postMessage({type:'result',id:data.id,signal});
  }catch{self.postMessage({type:'error',code:'frame'});}
  finally{frame?.close();}
};
