import * as THREE from '../lingke-motion-v3/vendor/three.module.js';
import {SHAPES,shapePoints} from '../lingke-motion-v3/motion.mjs';
import {deformFace} from '../lingke-motion-v4/personality.mjs';
import {performanceAt,PALETTES,ROLES,deviceWeights,screenApproach} from './performance.mjs';
import {poseHeadphone,poseRaised,poseConsole,poseGlassesPush,glassesPoseAt} from './performance-rig.mjs';

const mix=(a,b,t)=>a+(b-a)*t;
const expressionNames={smile:'招牌微笑',cheeky:'吐舌鬼脸',wow:'惊讶鬼脸',melancholy:'忧郁',angry:'愤怒'};
const featureOpacity=(expression,kind)=>(['wow','melancholy'].includes(expression)&&['teeth','tongue'].includes(kind))||expression==='angry'&&kind==='tongue'?0:1;
const workspacePalettes={
  smart:[['#a991ff','#e7a7ff'],['#cbb4ff','#96b5ff'],['#ed99dd','#ad8aff'],['#9ca5ff','#dfb7ff']],
  pro:[['#5ecaff','#899aff'],['#7ae7ee','#6ca8ff'],['#a5c5ff','#57bbeb'],['#73a7ec','#9cf0ef']],
};

function createGazeEyes(){
  const root=new THREE.Group();root.name='LINGKE / hidden gaze eyes';root.visible=false;
  // B study: a tapered, swept eye silhouette and restrained ruby lens replace
  // the round sclera/iris stack. Curvature follows the planet instead of a
  // camera-facing sticker; the original planet, mouth and accessories remain.
  const outline=new THREE.Shape();
  outline.moveTo(-.255,-.055);
  outline.quadraticCurveTo(.015,.025,.294,.155);
  outline.quadraticCurveTo(.313,.035,.242,-.049);
  outline.quadraticCurveTo(.088,-.166,-.116,-.126);
  outline.quadraticCurveTo(-.226,-.105,-.255,-.055);
  const lensGeometry=new THREE.ShapeGeometry(outline,40);
  const positions=lensGeometry.attributes.position;
  for(let i=0;i<positions.count;i++){
    const x=positions.getX(i),y=positions.getY(i);
    positions.setZ(i,.044-x*x*.55-y*y*.80);
  }
  lensGeometry.computeVertexNormals();
  const sphere=new THREE.SphereGeometry(1,40,24);
  const socket=new THREE.MeshPhysicalMaterial({color:'#261235',roughness:.45,clearcoat:.3,side:THREE.DoubleSide});
  const lens=new THREE.MeshPhysicalMaterial({color:'#bf3d62',emissive:'#a11946',emissiveIntensity:.10,
    roughness:.43,metalness:0,clearcoat:.5,clearcoatRoughness:.3,specularIntensity:.25,envMapIntensity:.12,side:THREE.DoubleSide});
  const lid=new THREE.MeshPhysicalMaterial({color:'#49264f',roughness:.38,clearcoat:.35});
  const pupil=new THREE.MeshBasicMaterial({color:'#211126'});
  const shine=new THREE.MeshBasicMaterial({color:'#ffe2ed',transparent:true,opacity:.68});
  const top=new THREE.QuadraticBezierCurve3(new THREE.Vector3(-.255,-.055,.009),new THREE.Vector3(.015,.025,.062),new THREE.Vector3(.294,.155,-.008));
  const lidGeometry=new THREE.TubeGeometry(top,40,.013,8,false);
  // A broad pupil is masked on the eye's own curved surface. The silhouette
  // clips it naturally, so looking up never puts a black oval outside the lid.
  const pupilGeometry=lensGeometry.clone(),uv=pupilGeometry.attributes.uv;
  for(let i=0;i<uv.count;i++)uv.setXY(i,(positions.getX(i)+.32)/.64,(positions.getY(i)+.20)/.40);
  const maskWidth=256,maskHeight=160,maskData=new Uint8Array(maskWidth*maskHeight*4);
  for(let y=0;y<maskHeight;y++)for(let x=0;x<maskWidth;x++){
    const px=(x+.5)/maskWidth*.64-.32,py=(y+.5)/maskHeight*.40-.20;
    const radius=Math.hypot((px-.014)/.080,(py+.018)/.100);
    const coverage=Math.round(Math.max(0,Math.min(1,(1-radius)/.055))*255),i=(y*maskWidth+x)*4;
    maskData[i]=maskData[i+1]=maskData[i+2]=coverage;maskData[i+3]=255;
  }
  for(const side of [-1,1]){
    const eye=new THREE.Group();eye.name=side<0?'B / left swept eye':'B / right swept eye';
    // Scale the complete eye assembly so the rim, pupil and highlights retain
    // their proportions on the broad planet face, including expression changes.
    eye.userData.side=side;eye.userData.baseHeight=1.32;
    eye.position.set(side*.53,1.005,1.26);eye.scale.set(side*1.28,eye.userData.baseHeight,1);
    eye.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),new THREE.Vector3(side*.46,.86,1.26).normalize());
    eye.userData.baseTilt=eye.rotation.z;
    const rim=new THREE.Mesh(lensGeometry,socket);rim.name='Soft plum eye rim';rim.scale.set(1.10,1.15,1);rim.position.z=-.01;eye.add(rim);
    const glass=new THREE.Mesh(lensGeometry,lens);glass.name='Curved rose lens';eye.add(glass);
    eye.add(new THREE.Mesh(lidGeometry,lid));
    const gaze=new THREE.Group();gaze.name='gaze';eye.add(gaze);
    const mask=new THREE.DataTexture(maskData,maskWidth,maskHeight,THREE.RGBAFormat);
    mask.magFilter=mask.minFilter=THREE.LinearFilter;mask.needsUpdate=true;gaze.userData.mask=mask;
    const pupilMaterial=pupil.clone();pupilMaterial.alphaMap=mask;pupilMaterial.transparent=true;pupilMaterial.alphaTest=.015;
    pupilMaterial.userData.gazeTransparent=true;
    const center=new THREE.Mesh(pupilGeometry,pupilMaterial);center.name='Broad clipped oval pupil';center.position.z=.009;gaze.add(center);
    const glint=new THREE.Mesh(sphere,shine);glint.name='Soft pupil catchlight';glint.position.set(-.009,-.009,.066);glint.scale.set(.012,.015,.003);gaze.add(glint);
    const light=new THREE.Mesh(sphere,shine);light.position.set(.11,.035,.047);light.scale.set(.024,.008,.003);eye.add(light);
    root.add(eye);
  }
  return root;
}

export function createPerformanceScene(ctx){
  const {characterRoot,ipRoot,ipRing,tracks,satellites,hands,handCloud,deviceRigs,devicePivots,particleMaterials,faceParts,glassesRig}=ctx;
  const gazeEyes=createGazeEyes();characterRoot.add(gazeEyes);
  let gazeVisibility=-1;
  function setGazeVisibility(amount){
    const level=Math.max(0,Math.min(1,amount));
    if(Math.abs(level-gazeVisibility)<.01)return;
    gazeVisibility=level;
    gazeEyes.visible=level>.01;
    gazeEyes.traverse(object=>{
      if(!object.material)return;
      const materials=Array.isArray(object.material)?object.material:[object.material];
      for(const material of materials){
        if('opacity' in material){
          material.userData.gazeBaseOpacity??=material.opacity;
          const opacity=level*material.userData.gazeBaseOpacity,transparent=material.userData.gazeTransparent===true||opacity<.999;
          if(material.transparent!==transparent){material.transparent=transparent;material.needsUpdate=true;}
          material.opacity=opacity;material.depthWrite=!transparent;
        }
      }
    });
  }
  const temp=new THREE.Vector3(),tint=new THREE.Color(),fromTint=new THREE.Color(),toTint=new THREE.Color();
  const work=hands.map(h=>({a:new Float32Array(h.posed.length),b:new Float32Array(h.posed.length)}));
  const satelliteData=satellites.map(({shell},i)=>{
    const initial=shell.geometry.attributes.position.array.slice();let radius=0;
    for(let k=0;k<initial.length;k+=3)radius=Math.max(radius,Math.hypot(initial[k],initial[k+1],initial[k+2]));
    return {initial,targets:SHAPES.map((shape,k)=>shapePoints(shape,initial.length/3,radius,4022+i*97+k))};
  });
  let expressionKey='',manualGlassesBase=null;
  function endpoint(part,name){
    const cache=part.performanceCache??=new Map();
    if(cache.has(name))return cache.get(name);
    const positions=new Float32Array(part.source.length);deformFace(positions,part.source,part.kind,name,1);
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
    geometry.computeVertexNormals();
    const normals=name==='smile'?part.normals:geometry.attributes.normal.array.slice();
    const result={positions,normals};cache.set(name,result);geometry.dispose();return result;
  }
  function face(state,speech=0){
    // Cache end shapes, then blend positions and normals; no face re-tessellation per frame.
    const blend=Math.round(state.expressionMix*30)/30;
    const speaking=Math.round(Math.max(0,Math.min(1,speech))*12)/12;
    const key=state.previousExpression+'/'+state.expression+'/'+blend+'/'+speaking;
    if(key===expressionKey)return;expressionKey=key;
    for(const part of faceParts){
      const from=endpoint(part,state.previousExpression),to=endpoint(part,state.expression);
      const position=part.mesh.geometry.attributes.position,normal=part.mesh.geometry.attributes.normal;
      for(let i=0;i<position.array.length;i++)position.array[i]=mix(from.positions[i],to.positions[i],blend);
      if(['mouth','teeth','tongue'].includes(part.kind)&&speaking>0){
        // Small bounded jaw deformation from the actual speech waveform.
        // No fake music pulse or phoneme-alignment claim.
        for(let i=1;i<position.array.length;i+=3){
          const y=position.array[i];position.array[i]=-.55+(y+.55)*(1+speaking*.16)-speaking*.025;
        }
      }
      for(let i=0;i<normal.array.length;i+=3){
        const x=mix(from.normals[i],to.normals[i],blend),y=mix(from.normals[i+1],to.normals[i+1],blend),z=mix(from.normals[i+2],to.normals[i+2],blend);
        const length=Math.hypot(x,y,z)||1;normal.array[i]=x/length;normal.array[i+1]=y/length;normal.array[i+2]=z/length;
      }
      position.needsUpdate=normal.needsUpdate=true;
      const opacity=mix(featureOpacity(state.previousExpression,part.kind),featureOpacity(state.expression,part.kind),blend);
      const material=part.mesh.material;
      if(material.transparent!==(opacity<1)){material.transparent=opacity<1;material.needsUpdate=true;}
      material.opacity=opacity;part.mesh.visible=opacity>0;
      if(part.kind==='tongue'){
        const amount=mix(state.previousExpression==='cheeky'?1:0,state.expression==='cheeky'?1:0,blend);
        material.color.setRGB(.34+.27*amount,.035+.045*amount,.13+.08*amount);
      }
    }
    document.querySelectorAll('[data-expression]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.expression===state.expression)));
    ctx.expressionStatus.textContent=expressionNames[state.expression]+' · 随乐句';
  }
  function handPose(out,role,side,state){
    if(role==='ear'||role==='raise'){
      if(role==='ear')poseHeadphone(out,handCloud);else poseRaised(out,handCloud);
      for(let k=0;k<out.length;k+=3){temp.fromArray(out,k).applyMatrix4(characterRoot.matrixWorld).toArray(out,k);}
    }else{
      poseConsole(out,handCloud,role,side,state.movement);
      // Hands lift away with the performer during a flight; the physical desk
      // and the independent background particle fields retain their anchors.
      for(let k=0;k<out.length;k+=3){out[k]+=characterRoot.position.x;out[k+1]+=characterRoot.position.y-4.5;out[k+2]+=characterRoot.position.z+1.15;}
    }
  }
  function update(beat,{brightness,intensity,reduced,autoExpression,drive,ambient,cameraPosition,cameraAspect=1.5,cameraFov=40,dialogue,compact=false,review=false}){
    const idle=ambient&&!drive?.playing;
    const state=performanceAt(idle?ambient.time*.42:beat),strength=reduced?0:intensity;
    if(drive){
      state.kick=drive.pulse;state.heavy=drive.heavy;state.energy=drive.energy*.25+drive.pulse*.75;
      if(drive.action){state.action=state.previousAction=drive.action;state.actionMix=1;}
      if(drive.expression){state.expression=state.previousExpression=drive.expression;state.expressionMix=1;}
    }
    if(idle){
      state.kick=state.heavy=state.energy=0;
      state.action=state.previousAction='cue';state.actionMix=1;
      state.expression=state.previousExpression=drive?.expression||'smile';state.expressionMix=1;
      state.title='星尘巡游 · 等你开场';state.detail='待机星云持续流动，导入音乐后跟随真实节奏。';
    }
    const cameo=reduced?0:(ambient?.cameo.amount||0);
    const glassesDown=reduced&&!review?0:(ambient?.cameo.glassesDown||0),bow=reduced&&!review?0:(ambient?.cameo.bow||0);
    if(cameo>0){
      state.previousExpression=drive?.expression||(idle?'smile':state.expression);
      state.expression=ambient.cameo.expression;state.expressionMix=cameo;
    }
    const flight=reduced?null:drive?.flight;
    const travel=1-cameo,distance=cameraPosition?Math.hypot(cameraPosition.x,cameraPosition.y-4.5,cameraPosition.z+1.15):10;
    const approach=screenApproach(cameo,{distance,aspect:cameraAspect,fov:cameraFov,review});
    const bob=reduced?0:idle?ambient.bob:Math.sin(beat*Math.PI)*.025;
    const sway=reduced?0:idle?ambient.sway:Math.sin(beat*Math.PI/4)*.075;
    characterRoot.position.set((flight?.active?flight.x*travel:0)+(cameraPosition?.x||0)*approach,
      4.5+(flight?.active?flight.y*travel:0)+(state.kick*.055+bob)*strength+(cameraPosition?cameraPosition.y-4.5:0)*approach,
      -1.15+(flight?.active?flight.z*travel:0)+(cameraPosition?cameraPosition.z+1.15:0)*approach);
    characterRoot.rotation.set(-.10+(idle?bob*.4:Math.sin(beat*Math.PI)*.035)*strength*(1-cameo)+bow*.20,
      sway*strength*(1-cameo)+(flight?.active?flight.tilt*travel:0)+cameo*.15,
      (idle?sway*.45:Math.sin(beat*Math.PI/8)*.045)*strength*(1-cameo)+(ambient?.cameo.roll||0));
    characterRoot.updateMatrixWorld(true);
    // Pose every frame, even when the face-expression cache does not change.
    const expressionBlend=autoExpression?Math.round(state.expressionMix*30)/30:0;
    const offset=name=>name==='angry'?-.07:name==='melancholy'?-.035:0;
    if(!autoExpression&&cameo>0&&!manualGlassesBase)manualGlassesBase={y:glassesRig.position.y,forward:glassesRig.position.z,x:glassesRig.rotation.x,z:glassesRig.rotation.z};
    if(autoExpression||cameo>0){
      const baseY=autoExpression?mix(offset(state.previousExpression),offset(state.expression),expressionBlend):manualGlassesBase.y;
      const baseZ=autoExpression?mix(state.previousExpression==='cheeky'?-.065:0,state.expression==='cheeky'?-.065:0,expressionBlend):manualGlassesBase.z;
      const pose=glassesPoseAt(glassesDown,baseY*(1-cameo),baseZ*(1-cameo));
      glassesRig.position.set(0,pose.y,pose.z);
      glassesRig.rotation.set(pose.pitch,0,pose.roll);
      if(autoExpression)manualGlassesBase=null;
    }else if(manualGlassesBase){
      glassesRig.position.y=manualGlassesBase.y;glassesRig.position.z=manualGlassesBase.forward;glassesRig.rotation.x=manualGlassesBase.x;glassesRig.rotation.z=manualGlassesBase.z;manualGlassesBase=null;
    }
    // Fade the eyes in while the frames clear the brow, avoiding a hard pop
    // during the micro-camera push.
    const gazeAmount=Math.max(0,Math.min(1,(glassesDown-.015)/.17));
    setGazeVisibility(gazeAmount);
    const eyeHeight=name=>name==='wow'?1.3:name==='melancholy'?.75:name==='angry'?.80:1;
    const eyeTilt=(name,side)=>name==='cheeky'?(side<0?.07:-.035):name==='melancholy'?.14:name==='angry'?-.08:0;
    const eyeBlend=Math.max(0,Math.min(1,state.expressionMix));
    for(const eye of gazeEyes.children){
      eye.scale.y=eye.userData.baseHeight*mix(eyeHeight(state.previousExpression),eyeHeight(state.expression),eyeBlend);
      eye.rotation.z=eye.userData.baseTilt+eye.userData.side*mix(eyeTilt(state.previousExpression,eye.userData.side),eyeTilt(state.expression,eye.userData.side),eyeBlend);
    }
    if(gazeEyes.visible&&cameraPosition){
      gazeEyes.updateMatrixWorld(true);
      for(const eye of gazeEyes.children){
        temp.copy(cameraPosition);eye.worldToLocal(temp);temp.normalize();
        const gaze=eye.getObjectByName('gaze'),dx=Math.max(-.035,Math.min(.035,temp.x*.055)),dy=Math.max(-.012,Math.min(.016,temp.y*.035));
        gaze.userData.mask.offset.set(-dx/.64,-dy/.40);
        gaze.getObjectByName('Soft pupil catchlight').position.set(-.009+dx,-.009+dy,.066);
      }
    }
    ipRoot.position.copy(characterRoot.position);ipRoot.rotation.set(characterRoot.rotation.x+.10,characterRoot.rotation.y,characterRoot.rotation.z);
    const weight=deviceWeights(state);
    for(const [key,rig] of deviceRigs){
      rig.position.set(...devicePivots[key]);rig.rotation.y=0;
      if(key==='stage.deckA')rig.rotation.y=state.movement.jog*weight.deckA;
      if(key==='stage.deckB')rig.rotation.y=state.movement.jog*weight.deckB;
      if(key==='stage.eq')rig.rotation.y=state.movement.eq*weight.eq;
      if(key==='stage.crossfader')rig.position.x+=state.movement.fader*weight.fader;
    }
    for(let side=0;side<2;side++){
      const hand=hands[side],{a,b}=work[side];
      handPose(a,ROLES[state.previousAction][side],side,state);handPose(b,ROLES[state.action][side],side,state);
      const output=hand.object.geometry.attributes.position;
      for(let k=0;k<output.array.length;k++)output.array[k]=mix(a[k],b[k],state.actionMix);
      if(cameo>0){
        if(side===0)handPose(a,'ear',side,state);
        else{
          poseGlassesPush(a,handCloud,glassesDown,{y:glassesRig.position.y,z:glassesRig.position.z,pitch:glassesRig.rotation.x,roll:glassesRig.rotation.z});
          for(let k=0;k<a.length;k+=3)temp.fromArray(a,k).applyMatrix4(characterRoot.matrixWorld).toArray(a,k);
        }
        const handBlend=side===0?cameo:(ambient?.cameo.handReach||0);
        for(let k=0;k<output.array.length;k++)output.array[k]=mix(output.array[k],a[k],handBlend);
      }
      output.needsUpdate=true;hand.object.visible=true;
      hand.object.geometry.setDrawRange(0,ctx.quality()==='low'?2200:output.count);
      hand.object.material.uniforms.uOpacity.value=.92;
      hand.object.material.uniforms.uLight.value=2.5*brightness;
    }
    for(const track of tracks){
      const from=track.targets[state.previousShape],to=track.targets[state.shape],out=track.attribute.array;
      for(let k=0;k<out.length;k++)out[k]=mix(from[k],to[k],state.shapeMix);
      track.attribute.needsUpdate=true;
    }
    satellites.forEach(({shell,orbit,group},i)=>{
      const data=satelliteData[i],out=shell.geometry.attributes.position;
      const from=data.targets[(state.previousShape+i)%4],to=data.targets[(state.shape+i)%4];
      for(let k=0;k<out.array.length;k++)out.array[k]=mix(from[k],to[k],state.shapeMix);
      out.needsUpdate=true;shell.scale.setScalar(1+state.heavy*.14*strength);
      group.position.y+=Math.sin(beat*Math.PI+i)*state.kick*.045*strength;
      orbit.scale.setScalar(1+state.heavy*.20*strength);
    });
    const moodPalette=drive?.expression==='melancholy'?['#809be8','#ae8bd4']:drive?.expression==='angry'?['#ed6d84','#efa66f']:null;
    const palette=workspacePalettes[drive?.workspaceMode]||PALETTES;
    const from=moodPalette||palette[state.previousPalette%palette.length],to=moodPalette||palette[state.palette%palette.length];
    particleMaterials.forEach((material,i)=>{
      fromTint.set(from[i%2]);toTint.set(to[i%2]);tint.lerpColors(fromTint,toTint,state.paletteMix);
      material.uniforms.uPulse.value=state.energy*strength;
      material.uniforms.uTint.value.copy(tint);material.uniforms.uColorMix.value=.60;
    });
    ipRing.material.uniforms.uColorMix.value=.28;
    if(autoExpression)face(state,dialogue?.phase==='speaking'?dialogue.level:0);else expressionKey='';
    return state;
  }
  function reset(){
    expressionKey='';manualGlassesBase=null;
    setGazeVisibility(0);glassesRig.position.set(0,0,0);glassesRig.rotation.set(0,0,0);
    characterRoot.position.set(0,4.5,-1.15);characterRoot.rotation.set(-.10,0,0);
    ipRoot.position.set(0,4.5,-1.15);ipRoot.rotation.set(0,0,0);
    for(const material of particleMaterials){material.uniforms.uPulse.value=0;material.uniforms.uTint.value.set('#ffffff');material.uniforms.uColorMix.value=0;}
    satellites.forEach(({shell,orbit},i)=>{shell.geometry.attributes.position.array.set(satelliteData[i].initial);shell.geometry.attributes.position.needsUpdate=true;orbit.scale.setScalar(1);});
  }
  return {update,reset};
}
