import * as THREE from '../lingke-motion-v3/vendor/three.module.js';

// Outfits are additive layers. The accepted planet mesh and face proportions
// stay untouched; each style is bounded to a local anchor and can be hidden.
const STYLES=['neutral','hiphop','chinese','edm','rock','jazz','chill'];
const material=(color,options={})=>new THREE.MeshStandardMaterial({color,roughness:.42,metalness:.08,...options});

export function createOutfitRig(parent){
  const root=new THREE.Group();root.name='LINGKE / style outfit layers';parent.add(root);
  const layers=new Map();
  const add=(style,object)=>{object.visible=false;object.name='LINGKE outfit / '+style;root.add(object);layers.set(style,object);return object;};
  const mesh=(geometry,mat,parent)=>{const object=new THREE.Mesh(geometry,mat);parent.add(object);return object;};

  // Hip-hop / trap: red cap and a restrained silver grill. Both remain below
  // the brow and inside the face safe area.
  const hiphop=add('hiphop',new THREE.Group());
  const red=material('#b52f4c',{roughness:.34,metalness:.12}),silver=material('#d9e2ee',{roughness:.22,metalness:.85});
  const crown=mesh(new THREE.SphereGeometry(.78,32,16,0,Math.PI*2,0,Math.PI*.62),red,hiphop);crown.position.set(0,1.47,-.05);crown.scale.set(1,.58,1);
  const brim=mesh(new THREE.CylinderGeometry(.52,.72,.08,32),red,hiphop);brim.position.set(0,1.30,.37);brim.rotation.x=-.10;brim.scale.set(1,.55,1);
  const grill=new THREE.Group();grill.position.set(0,.25,1.67);hiphop.add(grill);
  for(let i=0;i<6;i++){const bar=mesh(new THREE.BoxGeometry(.055,.115,.028),silver,grill);bar.position.x=(i-2.5)*.085;bar.rotation.z=(i-2.5)*.018;}
  const chain=mesh(new THREE.TorusGeometry(.34,.025,8,40),silver,hiphop);chain.position.set(0,-.48,1.18);chain.rotation.x=.18;chain.scale.y=.72;

  // Chinese / classical: jade pendant and a restrained cloud collar.
  const chinese=add('chinese',new THREE.Group());
  const jade=material('#86d1bf',{roughness:.3,metalness:.12}),ink=material('#263454',{roughness:.55,metalness:.05});
  const collar=mesh(new THREE.TorusGeometry(.58,.045,10,48,Math.PI*1.35),ink,chinese);collar.position.set(0,-.48,1.12);collar.rotation.x=.35;collar.rotation.z=Math.PI*.18;
  const pendant=mesh(new THREE.OctahedronGeometry(.14,1),jade,chinese);pendant.position.set(.02,-.73,1.38);pendant.rotation.set(.2,.3,.1);
  for(const x of [-.42,.42]){const cloud=mesh(new THREE.TorusGeometry(.14,.025,8,24),jade,chinese);cloud.position.set(x,-.58,1.16);cloud.scale.set(1,.68,1);}
  const ribbon=mesh(new THREE.BoxGeometry(.055,.42,.028),jade,chinese);ribbon.position.set(-.68,.86,.14);ribbon.rotation.z=-.18;

  // EDM: translucent visor and a single luminous body band.
  const edm=add('edm',new THREE.Group());
  const cyan=material('#63e5e0',{roughness:.18,metalness:.45,emissive:'#2bbec2',emissiveIntensity:.7,transparent:true,opacity:.78});
  const visor=mesh(new THREE.BoxGeometry(1.08,.13,.07),cyan,edm);visor.position.set(0,.93,1.48);visor.rotation.z=.02;
  const band=mesh(new THREE.TorusGeometry(.78,.035,8,48),cyan,edm);band.position.set(0,-.18,.10);band.rotation.x=Math.PI/2;band.scale.y=.66;
  for(const x of [-.50,.50]){const led=mesh(new THREE.SphereGeometry(.065,12,8),cyan,edm);led.position.set(x,-.18,.84);}

  // Rock: dark jacket shoulders with two small studs; the planet remains the
  // dominant silhouette instead of becoming a new character.
  const rock=add('rock',new THREE.Group());
  const leather=material('#242333',{roughness:.62,metalness:.28}),redStud=material('#d65563',{roughness:.25,metalness:.5,emissive:'#3b101a',emissiveIntensity:.25});
  for(const side of [-1,1]){
    const shoulder=mesh(new THREE.BoxGeometry(.42,.22,.28),leather,rock);shoulder.position.set(side*.68,-.56,.18);shoulder.rotation.z=side*.20;
    const stud=mesh(new THREE.SphereGeometry(.065,12,8),redStud,rock);stud.position.set(side*.68,-.56,.36);
  }
  const lapel=mesh(new THREE.BoxGeometry(.10,.74,.035),leather,rock);lapel.position.set(0,-.48,1.10);lapel.rotation.z=.08;

  // Jazz: compact fedora silhouette with an amber band.
  const jazz=add('jazz',new THREE.Group());
  const hat=material('#332b55',{roughness:.5,metalness:.18}),amber=material('#e7ac63',{roughness:.3,metalness:.45});
  const hatTop=mesh(new THREE.CylinderGeometry(.55,.62,.20,32),hat,jazz);hatTop.position.set(0,1.37,-.03);hatTop.rotation.z=-.04;
  const hatBrim=mesh(new THREE.CylinderGeometry(.78,.78,.045,32),hat,jazz);hatBrim.position.set(0,1.25,.06);hatBrim.scale.y=.58;
  const hatBand=mesh(new THREE.TorusGeometry(.585,.025,8,32),amber,jazz);hatBand.position.set(0,1.32,-.03);hatBand.rotation.x=Math.PI/2;

  // Lo-fi / chill: beanie and a quiet scarf loop, with no face obstruction.
  const chill=add('chill',new THREE.Group());
  const knit=material('#66789a',{roughness:.92,metalness:.02}),scarf=material('#b39ad6',{roughness:.72,metalness:.02});
  const beanie=mesh(new THREE.SphereGeometry(.70,24,14,0,Math.PI*2,0,Math.PI*.54),knit,chill);beanie.position.set(0,1.43,-.05);beanie.scale.set(1,.62,1);
  const pom=mesh(new THREE.SphereGeometry(.10,12,8),scarf,chill);pom.position.set(0,1.79,-.04);
  const loop=mesh(new THREE.TorusGeometry(.56,.055,8,36),scarf,chill);loop.position.set(0,-.50,1.10);loop.rotation.x=.25;loop.scale.y=.68;

  function setStyle(style='neutral'){
    const next=STYLES.includes(style)?style:'neutral';
    for(const [key,layer] of layers)layer.visible=key===next;
    root.userData.style=next;
  }
  setStyle();
  return {root,setStyle,styles:[...STYLES]};
}
