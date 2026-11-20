(function (root) {
  'use strict';
  const fail = () => { throw Error('图片格式不可用，请选择静态 PNG、JPEG 或 WebP'); };
  function inspectImage(buffer) {
    const b = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const v = new DataView(b.buffer,b.byteOffset,b.byteLength);
    const str = (offset,size) => String.fromCharCode(...b.slice(offset,offset+size));
    let type, width, height;
    if (b.length>=33 && str(1,3)==='PNG' && b[0]===137 && b[4]===13 && b[5]===10 && b[6]===26 && b[7]===10) {
      type='image/png';
      if(str(12,4)!=='IHDR'||v.getUint32(8)!==13) fail();
      width=v.getUint32(16); height=v.getUint32(20);
      for(let pos=8;pos+12<=b.length;){
        const size=v.getUint32(pos), chunk=str(pos+4,4);
        if(size>b.length-pos-12) fail();
        if(chunk==='acTL') throw Error('暂不支持动画图片，请上传静态人物图');
        pos+=12+size;
      }
    } else if (b.length>12 && b[0]===255 && b[1]===216) {
      type='image/jpeg';
      let pos=2;
      while(pos+3<b.length){
        if(b[pos++]!==255) fail();
        while(b[pos]===255) pos++;
        const marker=b[pos++];
        if(marker===217 || marker===218) break;
        if(marker===1 || (marker>=208&&marker<=215)) continue;
        if(pos+2>b.length) fail();
        const len=v.getUint16(pos);
        if(len<2||pos+len>b.length) fail();
        if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)){
          if(len<8) fail();
          height=v.getUint16(pos+3); width=v.getUint16(pos+5); break;
        }
        pos+=len;
      }
    } else if (b.length>=20 && str(0,4)==='RIFF' && str(8,4)==='WEBP') {
      type='image/webp';
      if(v.getUint32(4,true)+8!==b.length) fail();
      const u24 = offset => b[offset]+b[offset+1]*256+b[offset+2]*65536;
      for(let pos=12;pos+8<=b.length;){
        const size=v.getUint32(pos+4,true), chunk=str(pos,4), p=pos+8;
        if(p+size>b.length) fail();
        if(chunk==='ANIM'||chunk==='ANMF'||(chunk==='VP8X'&&size>=10&&(b[p]&2))) throw Error('暂不支持动画图片，请上传静态人物图');
        if(chunk==='VP8X'&&size>=10){ width=1+u24(p+4);height=1+u24(p+7); }
        if(!width&&chunk==='VP8 '&&size>=10&&str(p+3,3)==='\x9d\x01\x2a'){
          width=v.getUint16(p+6,true)&16383;height=v.getUint16(p+8,true)&16383;
        }
        if(!width&&chunk==='VP8L'&&size>=5&&b[p]===47){
          const bits=v.getUint32(p+1,true);width=(bits&16383)+1;height=((bits>>>14)&16383)+1;
        }
        pos=p+size+(size%2);
      }
    } else fail();
    if(!width||!height) throw Error('无法读取有效图片尺寸');
    if(width*height>20000000) throw Error('图片超过2000万像素，请缩小后重试');
    return {type,width,height};
  }
  async function validateFile(file) {
    if(!file.size||file.size>10*1024*1024) throw Error('图片需大于0且不超过10 MiB');
    const info=inspectImage(await file.arrayBuffer());
    if(file.type&&file.type!==info.type) throw Error('图片内容与声明格式不一致');
    return info;
  }
  async function decode(file) {
    const info=await validateFile(file);
    const bitmap=await createImageBitmap(file, {imageOrientation:'from-image'}).catch(()=>{throw Error('图片损坏或无法解码，请换一张');});
    const width=bitmap.width, height=bitmap.height;
    bitmap.close();
    if(!width||!height||width*height>20000000) throw Error('图片像素超限');
    return {...info,width,height};
  }
  const api={inspectImage,validateFile,decode};
  if(typeof module!=='undefined'&&module.exports) module.exports=api;
  else root.DeepmixAvatarImage=api;
})(globalThis);
