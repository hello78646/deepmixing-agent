(function(root){
  'use strict';
  async function open(){
    const db=await new Promise((resolve,reject)=>{
      const req=indexedDB.open('deepmixing-avatar-v1',1);
      req.onupgradeneeded=()=>{req.result.createObjectStore('assets',{keyPath:'id'});req.result.createObjectStore('workspace');};
      req.onsuccess=()=>resolve(req.result);
      req.onerror=()=>reject(Error('浏览器本地存储不可用；请允许此站点存储后重试'));
      req.onblocked=()=>reject(Error('请关闭其他人物编辑页，再重试'));
    });
    db.onversionchange=()=>db.close();
    function transaction(stores,mode,work){
      return new Promise((resolve,reject)=>{
        let value,tx;
        try{
          tx=db.transaction(stores,mode);
          tx.oncomplete=()=>resolve(value);
          tx.onabort=()=>reject(Error('本地保存失败：存储空间不足或访问受限。当前编辑仍在页面，可重试。'));
          tx.onerror=()=>{};
          work(tx,result=>{value=result;});
        }catch(error){if(tx) tx.abort();reject(error);}
      });
    }
    return {
      load:()=>transaction(['workspace'],'readonly',(tx,set)=>{tx.objectStore('workspace').get('current').onsuccess=e=>set(e.target.result||null);}),
      image:id=>transaction(['assets'],'readonly',(tx,set)=>{tx.objectStore('assets').get(id).onsuccess=e=>set(e.target.result||null);}),
      save:(state,assets=[])=>transaction(['assets','workspace'],'readwrite',tx=>{
        for(const asset of assets)tx.objectStore('assets').put(asset);
        tx.objectStore('workspace').put(state,'current');
      }),
      clear:()=>transaction(['assets','workspace'],'readwrite',tx=>{tx.objectStore('assets').clear();tx.objectStore('workspace').clear();})
    };
  }
  root.DeepmixAvatarStore={open};
})(globalThis);
