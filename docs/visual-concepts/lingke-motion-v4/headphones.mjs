import * as THREE from '../lingke-motion-v3/vendor/three.module.js';

export function createHeadphones(){
  const root=new THREE.Group();root.name='LINGKE / over-ear DJ headphones';
  const shell=new THREE.MeshPhysicalMaterial({color:'#171b29',metalness:.72,roughness:.29,clearcoat:.35});
  const cushion=new THREE.MeshStandardMaterial({color:'#090c16',roughness:.86,metalness:.03});
  const metal=new THREE.MeshStandardMaterial({color:'#777e96',metalness:.92,roughness:.25});
  const purple=new THREE.MeshStandardMaterial({color:'#9f72ef',emissive:'#8653f4',emissiveIntensity:.65,roughness:.3,metalness:.4});
  const cyan=new THREE.MeshStandardMaterial({color:'#70ecdf',emissive:'#46cfc9',emissiveIntensity:.65,roughness:.3,metalness:.4});
  const mesh=(geometry,material,parent=root)=>{const m=new THREE.Mesh(geometry,material);parent.add(m);return m;};
  // Elliptic padded headband across the crown; rearward position clears the brows.
  function band(radius,width,depth,material,z){
    const verts=[],indices=[],segments=100;
    for(let i=0;i<=segments;i++){
      const a=.05+(Math.PI-.1)*i/segments;
      for(const [r,d] of [[radius-width/2,-depth/2],[radius+width/2,-depth/2],[radius+width/2,depth/2],[radius-width/2,depth/2]]){
        verts.push(Math.cos(a)*r,Math.sin(a)*r*.99+.12,z+d);
      }
    }
    for(let i=0;i<segments;i++)for(let k=0;k<4;k++){
      const a=i*4+k,b=i*4+(k+1)%4,c=b+4,d=a+4;indices.push(a,b,c,a,c,d);
    }
    indices.push(0,2,1,0,3,2,segments*4,segments*4+1,segments*4+2,segments*4,segments*4+2,segments*4+3);
    const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(verts,3));geo.setIndex(indices);geo.computeVertexNormals();
    return mesh(geo,material);
  }
  band(1.84,.13,.29,shell,-.10);band(1.735,.11,.25,cushion,-.10);
  band(1.853,.026,.026,purple,.061);
  for(const side of [-1,1]){
    const cup=new THREE.Group();cup.position.set(side*1.73,.15,-.08);cup.rotation.z=side*.06;root.add(cup);
    const cylinder=(radius,height,material,x)=>{
      const m=mesh(new THREE.CylinderGeometry(radius,radius,height,64,1),material,cup);
      m.rotation.z=Math.PI/2;m.position.x=side*x;return m;
    };
    const pad=cylinder(.51,.20,cushion,-.035);pad.scale.set(1,1,1.22);
    const housing=cylinder(.49,.25,shell,.15);housing.scale.set(1,1,1.22);
    const plate=cylinder(.405,.015,metal,.284);plate.scale.set(1,1,1.22);
    const inset=cylinder(.37,.02,shell,.299);inset.scale.set(1,1,1.22);
    const rim=mesh(new THREE.TorusGeometry(.428,.019,10,96),side<0?purple:cyan,cup);
    rim.rotation.y=Math.PI/2;rim.scale.set(1.22,1,1);rim.position.x=side*.31;
    // Machined yoke, pivot fasteners and five small illuminated equalizer bars.
    for(const depth of [-.39,.39]){
      const yoke=mesh(new THREE.BoxGeometry(.09,.63,.075),metal,cup);yoke.position.set(side*.03,.43,depth);
      const pin=cylinder(.059,.036,metal,.065);pin.position.set(side*.065,.18,depth);
    }
    for(let i=0;i<5;i++){
      const led=mesh(new THREE.BoxGeometry(.022,.065+Math.sin(i*.9)*.09,.026),side<0?purple:cyan,cup);
      led.position.set(side*.322,-.04,(i-2)*.075);
    }
    // Subtle stitched cushion seams, never luminous like the housing strip.
    const seam=mesh(new THREE.TorusGeometry(.44,.009,6,72),metal,cup);
    seam.rotation.y=Math.PI/2;seam.scale.set(1.22,1,1);seam.position.x=-side*.12;
  }
  return root;
}
