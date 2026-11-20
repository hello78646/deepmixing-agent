'use strict';
let planetDialogueOpen=false,planetPlayerOpen=false,planetVisitRequest=0,planetDialogueVisited=false;
function planetSafeArea(){
  const smart=typeof workbenchMode==='undefined'||workbenchMode==='smart';
  return {dialogueOpen:smart&&planetDialogueOpen,modalOpen:Boolean(document.querySelector('dialog[open]')),
    keyboardOpen:document.body.classList.contains('keyboard-open'),visit:planetVisitRequest};
}
function planetReceiveMessage(role,text){
  if(role==='assistant'){
    $('planet-caption').textContent=text;
    $('planet-presence').textContent='零氪星球';
  }else if(role==='system'&&text.length<220){$('planet-caption').textContent=text;}
  if(!planetDialogueOpen)$('immersive-unread').hidden=false;
}
function planetToggleDialogue(open=!planetDialogueOpen,focus=false){
  const messages=$('agent-messages');
  const restore=!open&&(messages.contains(document.activeElement)||document.activeElement===$('agent-jump-latest'));
  planetDialogueOpen=open;document.body.classList.toggle('dialogue-open',open);
  $('immersive-chat').setAttribute('aria-expanded',String(open));
  $('immersive-unread').hidden=true;
  if(open){
    if(!planetDialogueVisited&&typeof agentFollowLatest==='function')agentFollowLatest(true);
    else if(typeof agentUpdateJumpLatest==='function')agentUpdateJumpLatest();
    planetDialogueVisited=true;
    if(focus)messages.focus({preventScroll:true});
  }else if(restore)$('immersive-chat').focus({preventScroll:true});
}
(()=>{
  $('immersive-chat').addEventListener('click',()=>planetToggleDialogue(!planetDialogueOpen,true));
  $('immersive-settings').addEventListener('click',event=>openAgentSettings(event.currentTarget));
  $('immersive-output').addEventListener('click',()=>{
    planetPlayerOpen=!planetPlayerOpen;document.body.classList.toggle('player-open',planetPlayerOpen);
    $('immersive-output').setAttribute('aria-expanded',String(planetPlayerOpen));
    if(!$('agent-messages').childElementCount&&!project&&!setPlan)$('planet-caption').textContent='先添加音乐，告诉我想怎么混。成品会出现在对话里。';
    else if(!planetDialogueOpen)planetToggleDialogue(true);
  });
  $('immersive-visit').addEventListener('click',()=>{planetToggleDialogue(false);planetPlayerOpen=false;document.body.classList.remove('player-open');$('immersive-output').setAttribute('aria-expanded','false');planetVisitRequest++;document.activeElement?.blur();});
  $('shell-draft').addEventListener('input',()=>{
    const draft=$('shell-draft');draft.style.height='auto';draft.style.height=Math.min(112,Math.max(40,draft.scrollHeight))+'px';
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!event.defaultPrevented&&!event.isComposing&&event.keyCode!==229&&
      workbenchMode==='smart'&&!document.querySelector('dialog[open]')&&(planetDialogueOpen||planetPlayerOpen)){
      event.preventDefault();
      const playerFocused=document.querySelector('.shell-player')?.contains(document.activeElement);
      planetToggleDialogue(false);planetPlayerOpen=false;document.body.classList.remove('player-open');
      $('immersive-output').setAttribute('aria-expanded','false');
      if(playerFocused)$('immersive-output').focus({preventScroll:true});
    }
  });
  if(typeof MutationObserver!=='undefined'){
    new MutationObserver(()=>{
      const status=$('agent-progress').textContent;
      const phase=typeof getDialogueVoiceState==='function'?getDialogueVoiceState().phase:'idle';
      if(status&&['idle','thinking'].includes(phase))$('planet-presence').textContent=status;
      else if(typeof getDialogueVoiceState!=='function'||getDialogueVoiceState().phase==='idle')$('planet-presence').textContent='零氪星球 · 你的混音搭子';
    }).observe($('agent-progress'),{childList:true,characterData:true,subtree:true});
  }
  const viewport=window.visualViewport;
  const keyboard=()=>{
    const focused=document.activeElement===$('shell-draft');
    const inset=viewport?Math.max(0,window.innerHeight-viewport.height-viewport.offsetTop):0;
    document.body.classList.toggle('keyboard-open',focused&&inset>100);
    document.body.style.setProperty('--keyboard-inset',inset+'px');
    document.body.style.setProperty('--workspace-viewport-height',(viewport?.height||window.innerHeight)+'px');
    document.body.style.setProperty('--workspace-viewport-top',(viewport?.offsetTop||0)+'px');
  };
  viewport?.addEventListener('resize',keyboard);viewport?.addEventListener('scroll',keyboard);
  document.addEventListener('focusin',keyboard);document.addEventListener('focusout',keyboard);
  window.addEventListener('resize',keyboard);keyboard();
})();
