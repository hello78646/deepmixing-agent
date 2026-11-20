'use strict';
(() => {
  const dialog=document.getElementById('avatar-workshop'), entry=document.getElementById('avatar-entry');
  const content=document.getElementById('avatar-workshop-content'), close=document.getElementById('avatar-workshop-close');
  let frame=null;
  entry.addEventListener('click',event=>{
    event.preventDefault();if(frame)return;
    frame=document.createElement('iframe');frame.src='/avatar?embedded=1';frame.title='DJ形象编辑面板';content.append(frame);
    dialog.showModal();close.focus();
  });
  function requestClose(){
    if(frame)frame.contentWindow.postMessage({type:'deepmixing-avatar-request-close'},location.origin);
  }
  close.addEventListener('click',requestClose);
  dialog.addEventListener('cancel',event=>{event.preventDefault();requestClose();});
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||!frame||event.source!==frame.contentWindow)return;
    if(event.data?.type==='deepmixing-avatar-ready-close'){
      dialog.close();frame.remove();frame=null;entry.focus();
    }
  });
})();
