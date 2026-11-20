'use strict';
(() => {
  const M=DeepmixAvatarModel, $=id=>document.getElementById(id);
  const fields=[...document.querySelectorAll('[data-field]')];
  let state=M.createState(), store=null, currentAsset=null, objectUrl=null;
  let timer=null, dirty=false, revision=0, savedRevision=0, queue=Promise.resolve(), uploading=false, deleting=false, closing=false, lockHeld=false;
  const pendingAssets=new Map();
  const embedded=new URLSearchParams(location.search).get('embedded')==='1'&&window.parent!==window;
  const backLink=document.querySelector('.back-link');
  if(embedded){
    backLink.textContent='保存并返回混音 ↗';
    for(const link of [backLink,document.querySelector('.brand')])link.addEventListener('click',event=>{event.preventDefault();closeEmbedded();});
  }
  else{backLink.textContent='打开混音工作台 ↗';backLink.target='_blank';backLink.rel='noopener';}
  // Use the permitted same-origin stylesheet; an inline style element is blocked by CSP.
  const rule=[...document.styleSheets].find(sheet=>sheet.href&&new URL(sheet.href).pathname==='/avatar.css');
  const actorIndex=rule.insertRule('#actor-slot {}',rule.cssRules.length);
  const photoIndex=rule.insertRule('#avatar-preview {}',rule.cssRules.length);
  const actorStyle=rule.cssRules[actorIndex].style, photoStyle=rule.cssRules[photoIndex].style;
  const stepNames=['上传与取景','人物特征','场景试摆','版本档案'];
  function message(text,error=false){
    $('avatar-status').textContent=text;
    document.querySelector('.save-bar').classList.toggle('is-error',error);
    $('retry').hidden=!error;
  }
  function controlsEnabled(enabled){
    for(const el of document.querySelectorAll('main button,main input,main select,main textarea')) el.disabled=!enabled;
    $('retry').disabled=false;
    if(enabled){$('previous').disabled=state.draft.step===0;$('next').disabled=state.draft.step===3;}
  }
  function edited(patch){
    if(!store||uploading||deleting||closing)return;
    state=M.edit(state,patch);revision++;dirty=true;
    clearTimeout(timer);
    message('编辑中 · 将自动保存草稿');
    render();
    timer=setTimeout(saveSafely,550);
  }
  function persist(asset){
    clearTimeout(timer);
    if(asset)pendingAssets.set(asset.id,asset);
    const snapshot=structuredClone(state), stamp=revision, assets=[...pendingAssets.values()];
    const task=queue.catch(()=>{}).then(async()=>{
      if(!store)throw Error('本地存储尚未就绪，请重试');
      await store.save(snapshot,assets);
      for(const item of assets)pendingAssets.delete(item.id);
      savedRevision=Math.max(savedRevision,stamp);
      dirty=revision>savedRevision;
      if(!dirty)message(`草稿已保存在本浏览器 · ${state.versions.length} 个版本`);
    });
    queue=task;
    return task.catch(error=>{dirty=true;message(error.message,true);throw error;});
  }
  function saveSafely(){return persist().catch(()=>{});}
  async function setAsset(asset){
    if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=null;}
    currentAsset=asset;
    if(asset)objectUrl=URL.createObjectURL(asset.blob);
    $('avatar-preview').hidden=!asset;
    $('original-reference').hidden=!asset;
    $('actor-empty').hidden=Boolean(asset);
    if(asset){$('avatar-preview').src=objectUrl;$('avatar-original').src=objectUrl;}
    else{$('avatar-preview').removeAttribute('src');$('avatar-original').removeAttribute('src');}
    $('file-label').textContent=asset?'替换图片 · '+asset.name:'选择人物图片';
    $('image-info').textContent=asset?`${asset.width} × ${asset.height} · ${(asset.blob.size/1024/1024).toFixed(2)} MiB · 原图保留，可从版本恢复`:'原图会保留；调整取景不会修改图片文件。';
  }
  function paragraph(text,parent,cls){const el=document.createElement('p');el.textContent=text;if(cls)el.className=cls;parent.append(el);return el;}
  function render(){
    const d=state.draft;
    for(const input of fields){const key=input.dataset.field;if(input.type==='checkbox')input.checked=d[key];else if(document.activeElement!==input)input.value=d[key];}
    for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=Number(panel.dataset.panel)!==d.step;
    for(const button of document.querySelectorAll('[data-step]')){
      if(Number(button.dataset.step)===d.step)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');
    }
    $('previous').disabled=d.step===0;$('next').disabled=d.step===3;
    $('preview-name').textContent=d.name.trim()||'你的 DJ，待出场';
    $('scene').className='scene accent-'+d.accent+(d.view==='create'?' is-create':'');
    for(const button of document.querySelectorAll('[data-view]'))button.setAttribute('aria-pressed',String(button.dataset.view===d.view));
    actorStyle.width=d.scale+'%';actorStyle.height=d.scale+'%';actorStyle.left=d.offset+'%';
    photoStyle.transform=`translate(${(50-d.cropX)*.5}%, ${(50-d.cropY)*.5}%) scale(${d.zoom})`;
    $('view-note').textContent=d.view==='create'?'创作视角收起人物，操作留在手边；人物配置完整保留。':'欣赏时看到自己的 DJ 化身；随时回到创作。';
    const suggestion=M.suggest(d), box=$('style-suggestion');box.replaceChildren();
    paragraph(`${suggestion.scene} · ${suggestion.light}`,box);
    const reasons=document.createElement('ul');for(const reason of suggestion.reasons){const li=document.createElement('li');li.textContent=reason;reasons.append(li);}box.append(reasons);
    if(suggestion.outfit)paragraph('服装建议：'+suggestion.outfit,box);
    if(suggestion.accessory)paragraph('配饰建议：'+suggestion.accessory,box);
    const list=$('version-list');list.replaceChildren();
    if(!state.versions.length)paragraph('还没有版本。保存一个版本，或继续下一步时自动留下记录。',list,'note');
    for(const version of [...state.versions].reverse()){
      const row=document.createElement('div');row.className='version-row';
      const info=document.createElement('div'),title=document.createElement('strong'),time=document.createElement('small');
      title.textContent=version.label;time.textContent=new Date(version.time).toLocaleString('zh-CN')+' · '+(version.draft.name||'未命名DJ');
      info.append(title,time);const btn=document.createElement('button');btn.className='quiet';btn.textContent='恢复';btn.addEventListener('click',()=>restoreVersion(version.id));row.append(info,btn);list.append(row);
    }
  }
  async function navigate(step){
    if(!store||uploading||deleting||closing||step===state.draft.step)return;
    clearTimeout(timer);
    state=M.checkpoint(state,'离开 · '+stepNames[state.draft.step]);
    state=M.edit(state,{step});revision++;dirty=true;render();
    await saveSafely();
    document.querySelector(`[data-panel="${step}"] h2`).setAttribute('tabindex','-1');
    document.querySelector(`[data-panel="${step}"] h2`).focus({preventScroll:true});
  }
  async function restoreVersion(id){
    if(uploading||deleting||closing)return;
    clearTimeout(timer);uploading=true;controlsEnabled(false);
    const previous=state, oldAsset=currentAsset;
    try{
      const restored=M.restore(state,id);
      const asset=restored.draft.imageId?(pendingAssets.get(restored.draft.imageId)||await store.image(restored.draft.imageId)):null;
      if(restored.draft.imageId&&!asset)throw Error('该版本的原图缺失，当前版本保持不变');
      await persist();
      state=restored;revision++;dirty=true;
      await persist();await setAsset(asset);render();message('已恢复旧版；恢复前的草稿和后续版本都已保留。');
    }catch(error){state=previous;revision++;dirty=true;await setAsset(oldAsset);render();message(error.message,true);}
    finally{uploading=false;controlsEnabled(true);}
  }
  $('avatar-file').addEventListener('change',async event=>{
    const file=event.target.files[0];if(!file||uploading)return;
    clearTimeout(timer);uploading=true;controlsEnabled(false);message('正在校验与读取图片…');
    const previous=state;
    try{
      const info=await DeepmixAvatarImage.decode(file);
      const asset={id:crypto.randomUUID(),name:file.name,...info,blob:file};
      state=M.checkpoint(state,currentAsset?'换图前 · 保留原图':'上传前');
      state=M.edit(state,{imageId:asset.id,cropX:50,cropY:50,zoom:1});
      state=M.checkpoint(state,'人物原图 · '+file.name);revision++;dirty=true;
      try{await persist(asset);}catch(error){state=previous;pendingAssets.delete(asset.id);revision++;dirty=true;throw error;}
      await setAsset(asset);render();message('人物原图已保存。可以调整取景，或直接进入下一步。');
    }catch(error){render();message(error.message,true);}
    finally{uploading=false;controlsEnabled(true);event.target.value='';}
  });
  for(const field of fields)field.addEventListener(field.tagName==='SELECT'||field.type==='checkbox'?'change':'input',()=>{
    const value=field.type==='checkbox'?field.checked:field.type==='range'?Number(field.value):field.value;
    edited({[field.dataset.field]:value});
  });
  for(const button of document.querySelectorAll('[data-step]'))button.addEventListener('click',()=>navigate(Number(button.dataset.step)));
  for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>edited({view:button.dataset.view}));
  $('reset-crop').addEventListener('click',()=>edited({cropX:50,cropY:50,zoom:1}));
  $('apply-accent').addEventListener('click',()=>edited({accent:M.suggest(state.draft).color}));
  $('previous').addEventListener('click',()=>navigate(state.draft.step-1));
  $('next').addEventListener('click',()=>navigate(state.draft.step+1));
  $('save-version').addEventListener('click',async()=>{
    clearTimeout(timer);state=M.checkpoint(state,'手动保存 · '+stepNames[state.draft.step]);revision++;dirty=true;render();await saveSafely();
  });
  $('retry').addEventListener('click',()=>store?saveSafely():initialize());
  $('delete-all').addEventListener('click',()=>{$('delete-dialog').showModal();$('cancel-delete').focus();});
  $('cancel-delete').addEventListener('click',()=>{$('delete-dialog').close();$('delete-all').focus();});
  $('delete-dialog').addEventListener('close',()=>{if(state.draft.step===3)$('delete-all').focus();});
  $('confirm-delete').addEventListener('click',async()=>{
    if(deleting)return;deleting=true;clearTimeout(timer);controlsEnabled(false);$('confirm-delete').disabled=true;
    try{
      await queue.catch(()=>{});await store.clear();pendingAssets.clear();state=M.createState();revision=0;savedRevision=0;dirty=false;
      await setAsset(null);render();$('delete-dialog').close();message('本浏览器的人物原图、草稿与全部历史已删除。');
      document.querySelector('[data-step="0"]').focus();
    }catch(error){message(error.message,true);$('delete-dialog').close();}
    finally{deleting=false;$('confirm-delete').disabled=false;controlsEnabled(true);}
  });
  window.addEventListener('beforeunload',event=>{if(dirty||uploading){event.preventDefault();event.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&dirty&&!uploading&&!deleting)saveSafely();});
  async function closeEmbedded(){
    if(!embedded||closing)return;
    if(uploading||deleting){message('正在处理人物图片，请完成后再返回。');return;}
    clearTimeout(timer);closing=true;controlsEnabled(false);
    if($('delete-dialog').open)$('delete-dialog').close();
    try{
      if(dirty)await persist();else await queue.catch(()=>{});
      window.parent.postMessage({type:'deepmixing-avatar-ready-close'},location.origin);
    }catch(error){closing=false;controlsEnabled(true);message('请重试保存后再返回；当前编辑仍保留在面板。',true);}
  }
  window.addEventListener('message',event=>{
    if(embedded&&event.origin===location.origin&&event.source===window.parent&&event.data?.type==='deepmixing-avatar-request-close')closeEmbedded();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&embedded&&!$('delete-dialog').open){event.preventDefault();closeEmbedded();}
  });
  async function initialize(){
    controlsEnabled(false);
    try{
      // A second editor must not overwrite a history loaded by the first tab.
      if(navigator.locks&&!lockHeld)await new Promise((resolve,reject)=>{
        navigator.locks.request('deepmixing-avatar-editor',{ifAvailable:true},lock=>{
          if(!lock){reject(Error('另一个页面正在编辑人物；请先关闭那个页面，再重试。'));return;}
          lockHeld=true;resolve();return new Promise(()=>{});
        }).catch(reject);
      });
      store=await DeepmixAvatarStore.open();const saved=await store.load();
      if(saved){if(saved.schema!==1||!Array.isArray(saved.versions))throw Error('档案版本无法读取，未覆盖原数据');state={...saved,draft:M.edit(M.createState(),saved.draft).draft};}
      const asset=state.draft.imageId?await store.image(state.draft.imageId):null;
      await setAsset(asset);render();controlsEnabled(true);
      message(state.draft.imageId&&!asset?'人物原图缺失；可以从版本恢复或重新上传。':`本地档案已就绪 · ${state.versions.length} 个版本`,Boolean(state.draft.imageId&&!asset));
    }catch(error){store=null;message(error.message,true);}
  }
  controlsEnabled(false);initialize();
})();
