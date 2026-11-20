'use strict';
let agentSettingsBusy=false,agentSettingsOpener=null,agentSavedSettings=null;
const agentProviderDefaults={deepseek:{base_url:'https://api.deepseek.com',model:'deepseek-flash'},qwen:{base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:''},compatible:{base_url:'',model:''}};
function setAgentSettingsBusy(value){
  agentSettingsBusy=value;
  for(const id of ['agent-provider','agent-base-url','agent-model-name','agent-api-key','agent-billing-ack','agent-settings-save','agent-settings-check','agent-settings-forget','agent-settings-close'])$(id).disabled=value;
}
function displayAgentSettings(info){
  agentSavedSettings=info;
  $('agent-provider').value=info.provider||'deepseek';
  $('agent-base-url').value=info.base_url||agentProviderDefaults[$('agent-provider').value]?.base_url||'';
  $('agent-model-name').value=info.model||'';
  $('agent-api-key').value='';$('agent-billing-ack').checked=false;
  $('agent-api-key').placeholder=info.has_api_key?'已保存密钥；保留原服务时留空可继续使用':'输入你自己的 API Key';
  $('agent-key-state').textContent=info.has_api_key?'本机已有密钥，内容不会回显；更换供应商或地址时请重新输入。':'尚未保存密钥。请在这里输入，不要发到聊天里。';
  $('agent-settings-mode').textContent=info.configuration_error?'旧配置无法读取，请重新填写接口和API Key后保存。':info.environment_managed?'接口目前由本机环境变量管理，请先移除环境变量再在页面配置。':info.billing_mode==='personal'?'个人接口 · 使用你自己的 API 账号结算':'当前尚未启用个人接口。保存后将使用你自己的模型服务。';
}
async function openAgentSettings(opener=null){
  if(agentSettingsBusy)return;
  agentSettingsOpener=opener;
  if(!$('agent-settings').open)$('agent-settings').showModal();
  $('agent-settings-status').textContent='正在读取本机接口状态…';setAgentSettingsBusy(true);
  try{displayAgentSettings(await api('/api/agent/config'));$('agent-settings-status').textContent='填写后保存即可启用；保存不会自动发送聊天。';}
  catch(error){$('agent-settings-status').textContent=error.message;}
  finally{setAgentSettingsBusy(false);}
}
function closeAgentSettings(){
  if(agentSettingsBusy)return;
  $('agent-api-key').value='';$('agent-billing-ack').checked=false;
  $('agent-settings').close();agentSettingsOpener?.focus();
}
async function saveAgentSettings(event){
  event?.preventDefault();if(agentSettingsBusy)return;
  if(typeof agentPending!=='undefined'&&agentPending){$('agent-settings-status').textContent='先等待本轮对话完成，再修改接口。';return;}
  if(!$('agent-billing-ack').checked){$('agent-settings-status').textContent='请先阅读并勾选个人API费用说明。';return;}
  const body={provider:$('agent-provider').value,base_url:$('agent-base-url').value.trim(),model:$('agent-model-name').value.trim(),acknowledge_billing:true};
  const key=$('agent-api-key').value.trim();if(key)body.api_key=key;
  setAgentSettingsBusy(true);$('agent-settings-status').textContent='正在保存个人接口…';
  try{
    const info=await api('/api/agent/config',body);displayAgentSettings(info);
    $('agent-settings-status').textContent='个人接口已启用。可以检查连接，然后返回聊天开始混音。';
    if(typeof checkTextAgent==='function')await checkTextAgent();
  }catch(error){$('agent-settings-status').textContent='未保存：'+error.message;}
  finally{$('agent-api-key').value='';setAgentSettingsBusy(false);}
}
async function checkSavedAgentSettings(){
  if(agentSettingsBusy)return;
  if($('agent-api-key').value||$('agent-provider').value!==agentSavedSettings?.provider||$('agent-base-url').value.replace(/\/$/,'')!==agentSavedSettings?.base_url?.replace(/\/$/,'')||$('agent-model-name').value!==agentSavedSettings?.model){
    $('agent-settings-status').textContent='表单有未保存的改动，请先保存，再检查接口。';return;
  }
  setAgentSettingsBusy(true);$('agent-settings-status').textContent='正在验证已保存的接口与模型…';
  try{const info=await api('/api/agent/check',{});$('agent-settings-status').textContent=info.message;}
  catch(error){$('agent-settings-status').textContent='检查未通过：'+error.message;}
  finally{if(typeof checkTextAgent==='function')await checkTextAgent();setAgentSettingsBusy(false);}
}
async function forgetAgentKey(){
  if(agentSettingsBusy)return;
  setAgentSettingsBusy(true);
  try{
    displayAgentSettings(await api('/api/agent/config/forget',{}));
    $('agent-settings-status').textContent='本机密钥已移除，聊天暂停；音乐工程和调音台可继续使用。';
    if(typeof checkTextAgent==='function')await checkTextAgent();
  }catch(error){$('agent-settings-status').textContent=error.message;}
  finally{setAgentSettingsBusy(false);}
}
$('agent-settings-form').addEventListener('submit',saveAgentSettings);
for(const id of ['agent-settings-nav','agent-settings-open'])$(id).addEventListener('click',()=>openAgentSettings($(id)));
$('agent-settings-close').addEventListener('click',closeAgentSettings);
$('agent-settings-check').addEventListener('click',checkSavedAgentSettings);
$('agent-settings-forget').addEventListener('click',forgetAgentKey);
$('agent-settings').addEventListener('cancel',event=>{event.preventDefault();closeAgentSettings();});
$('agent-settings').addEventListener('close',()=>{$('agent-api-key').value='';$('agent-billing-ack').checked=false;});
$('agent-provider').addEventListener('change',()=>{
  const defaults=agentProviderDefaults[$('agent-provider').value];
  $('agent-base-url').value=defaults.base_url;$('agent-model-name').value=defaults.model;$('agent-api-key').value='';
  $('agent-api-key').placeholder='请填写该供应商对应的 API Key';
});
