"""Server-only configurable chat provider; no audio upload or model code execution."""
from dataclasses import dataclass
import copy
import hashlib
import json
from functools import lru_cache as _prompt_cache
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

from .agent_tools import AgentTools, TOOLS
from .continuous import set_layout
from .runtime import ROOT
from .agent_settings import read_config, write_config


class AgentError(RuntimeError):
    """Only safe, curated messages may cross the local API boundary."""
    def __init__(self,message,*,code='agent_error',http_status=503,delivery='unknown',retryable=False,request_id=None):
        super().__init__(message)
        self.code=code;self.http_status=http_status;self.delivery=delivery
        self.retryable=retryable;self.request_id=request_id

    def as_dict(self):
        return {'error':str(self),'code':self.code,'delivery':self.delivery,
                'retryable':self.retryable,'request_id':self.request_id}


def provider_error(exc):
    if isinstance(exc,urllib.error.HTTPError):
        code,message,status={
            401:('provider_auth_failed','模型鉴权失败，请在本机检查API Key',502),
            402:('provider_balance_low','模型账号余额不足，请在供应商平台检查',502),
            403:('provider_forbidden','模型权限或地域不匹配',502),
            404:('provider_not_found','模型或Base URL不可用，请核对接口地址和模型名称',502),
            429:('provider_rate_limited','模型限流，请稍后手动重试并检查账号额度',429),
        }.get(exc.code,('provider_unavailable','模型服务暂不可用，请稍后手动重试',502))
        return AgentError(message,code=code,http_status=status,delivery='response_received',retryable=exc.code==429 or exc.code>=500)
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(reason,PermissionError):
        # urllib.open also reads response headers before returning. A denied
        # socket operation alone cannot prove the POST was never delivered.
        return AgentError('本机服务的网络访问被系统或沙箱拦截。请以获准联网的方式重启服务，再检查连接；本轮未自动重发，未应用修改。',
                          code='network_access_denied',retryable=True)
    if isinstance(reason,TimeoutError):
        return AgentError('模型网络连接或响应超时，无法确认是否已送达；未自动重发，未应用修改。',
                          code='provider_timeout',http_status=504)
    if isinstance(exc,(ValueError,KeyError,IndexError,TypeError,UnicodeError)):
        return AgentError('模型回复格式不正确，未应用修改；未自动重发。',code='provider_protocol_error',http_status=502,delivery='response_received')
    return AgentError('模型网络连接中断，无法确认是否已送达；未自动重发，未应用修改。',code='network_error')


def provider_json(config,path,timeout,payload=None):
    request=urllib.request.Request(config.base_url.rstrip('/')+path,
        data=None if payload is None else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+config.api_key})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=timeout) as response:
            raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('oversized response')
        data=strict_json(raw)
        if not isinstance(data,dict):raise ValueError('expected object')
        return data
    except (OSError,ValueError,TypeError) as exc:raise provider_error(exc) from None


def provider_message(data):
    try:
        message=data['choices'][0]['message']
        if not isinstance(message,dict):raise ValueError('expected message object')
        return message
    except (ValueError,KeyError,IndexError,TypeError) as exc:raise provider_error(exc) from None


def strict_json(raw):
    return json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('工具参数不能含非有限数字')))


@dataclass
class AgentConfig:
    api_key:str=''
    base_url:str=''
    model:str='deepseek-flash'
    provider:str='deepseek'

    @classmethod
    def load(cls,path=None):
        try:data=read_config(path or ROOT/'.local'/'agent.json')
        except ValueError as exc:raise AgentError(str(exc),code='invalid_config',delivery='not_sent') from None
        return cls(**{k:os.environ.get('DEEPMIXING_AGENT_'+k.upper(),data.get(k,default)) for k,default in
                      [('api_key',''),('base_url',''),('model',cls.model),('provider',cls.provider)]})

    def ready(self):
        if not all(isinstance(v,str) for v in (self.api_key,self.base_url,self.model,self.provider)) or any(c in self.api_key for c in ('\r','\n')):
            raise AgentError('本机模型配置字段格式无效，请重新运行配置工具')
        if not self.api_key or not self.base_url:return False
        try:u=urlsplit(self.base_url)
        except ValueError:raise AgentError('模型Base URL格式无效') from None
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise AgentError('模型Base URL须为不含凭证或查询参数的HTTPS接口地址')
        if not isinstance(self.model,str) or not self.model or len(self.model)>120:raise AgentError('模型名称无效')
        if self.provider not in ('deepseek','qwen','compatible'):raise AgentError('provider请选择deepseek、qwen或compatible')
        return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def complete(config,payload,timeout):
    return provider_message(provider_json(config,'/chat/completions',timeout,payload))




def smart_dialogue_reply(reply):
    """Keep only one decision when the provider tries to ask several."""
    parts = re.split(r'(?<=[。！？?!])\s*|(?<=\.)\s+(?=[A-Z])', reply.strip())
    questions = [part for part in parts if part.endswith(('?', '？'))]
    if len(questions) > 1:
        return re.sub(r'^(?:或者|或是|又或者|Or\s+)', '', questions[-1], flags=re.IGNORECASE).lstrip()
    return reply




def _operator_prompt(smart):
    prompt, smart_dialogue = _operator_prompt_parts()
    return prompt + smart_dialogue if smart else prompt


@_prompt_cache(maxsize=1)
def _operator_prompt_parts():
    path = Path(os.environ.get('DEEPMIXING_AGENT_PROMPT_FILE', ROOT / '.local' / 'agent-prompt.json'))
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        parts = tuple(payload[key] for key in ('SYSTEM', 'SMART_DIALOGUE'))
        if not all(isinstance(part, str) and part.strip() for part in parts):
            raise ValueError()
        return parts
    except (OSError, ValueError, TypeError, KeyError):
        raise AgentError('Agent 对话配置尚未安装，请联系部署方。', code='missing_prompt', delivery='not_sent') from None


def set_plan_explanation(project,assets,commands):
    """Describe the final validated set, never a model's invented timings.

    Creation explanations may be stale after later edits in the same turn.
    Rebuild both visible text surfaces from the exact plan sent to the client.
    """
    layout=set_layout(project)
    lines=[]
    for i,clip in enumerate(project['clips']):
        name=assets[clip['track_id']]['name']
        lines.append(f"第{i+1}段：{name}，素材{clip['start']:.2f}–{clip['end']:.2f}秒，音量{clip['gain_db']:+.2f}dB，移调{clip['semitones']:+.2f}半音。")
    reasons=[]
    for i,join in enumerate(project['joins']):
        method,why={
            'smooth':('顺滑交接','等功率淡入淡出，减少交接时音量塌陷；叠加段仍可能发生人声或低频冲突。'),
            'cut':('直接切入','利落换段，仅保留防爆音短淡变；节奏和能量落差需试听确认。'),
            'echo':('尾段回声','前段尾部加入节奏回声再交接；需留意尾音与下一段重叠。')
        }[join['kind']]
        reasons.append(f"第{i+1}→{i+2}段：{method}，{join['seconds']:.2f}秒；{why}")
    speed='保持原速' if project['target_bpm'] is None else f"目标速度{project['target_bpm']:.2f} BPM"
    ending=f"预计完整时长{layout['duration']:.2f}秒；{speed}。未自动识别歌词、副歌或全曲情绪，实际听感需试听。"
    pending='\n已排队生成试听，是否成功见下方任务结果。' if 'preview' in commands else ''
    if 'export' in commands:pending+='\n已提交导出请求；如无当前试听，先试听并等待确认，成品以任务结果为准。'
    return '\n'.join(['本轮串烧方案（校验通过后由工作台应用）：',*lines,*reasons,ending])+pending,reasons


class TextAgent:
    def __init__(self,service,config=None,transport=None,config_path=None):
        self.service=service;self.config=config;self.transport=transport or complete
        self.config_path=Path(config_path) if config_path is not None else ROOT/'.local'/'agent.json'
        self.configuration_lock=threading.Lock()
        self.lock=threading.BoundedSemaphore(1)
        self.requests={};self.requests_lock=threading.RLock()
        self.connection=None

    def settings(self):return self.config or AgentConfig.load(self.config_path)

    def personal_api_enabled(self):
        try:return read_config(self.config_path).get('billing_mode')=='personal'
        except ValueError:return False

    def public_config(self):
        error=None
        try:
            c=self.settings();c.ready()
        except AgentError as exc:c=AgentConfig();error=str(exc)
        return {'provider':c.provider,'base_url':c.base_url,'model':c.model,'has_api_key':bool(c.api_key),
                'billing_mode':'personal' if self.personal_api_enabled() else 'development',
                'configuration_error':error,
                'environment_managed':any('DEEPMIXING_AGENT_'+k in os.environ for k in ('API_KEY','BASE_URL','MODEL','PROVIDER'))}

    def _configuration_guard(self):
        if not self.configuration_lock.acquire(blocking=False):
            raise AgentError('正在处理请求或配置，请完成后再修改接口。',code='agent_busy',http_status=409,delivery='not_sent')

    def configure(self,body):
        if body.get('acknowledge_billing') is not True:
            raise AgentError('请先确认使用自己的API账号并承担供应商调用费用。',code='billing_ack_required',http_status=400,delivery='not_sent')
        self._configuration_guard()
        try:
            if self.public_config()['environment_managed']:
                raise AgentError('接口由本机环境变量管理，请先移除环境变量再使用页面配置。',code='environment_managed',http_status=409,delivery='not_sent')
            try:old=self.settings()
            except AgentError:old=AgentConfig()
            provider=body.get('provider');base=body.get('base_url');model=body.get('model');key=body.get('api_key')
            if not all(isinstance(v,str) for v in (provider,base,model)):
                raise AgentError('请填写供应商、HTTPS接口地址与模型。',code='invalid_config',http_status=400,delivery='not_sent')
            if len(base)>1000 or any(ord(ch)<32 for ch in base):
                raise AgentError('接口地址含无效字符或过长。',code='invalid_config',http_status=400,delivery='not_sent')
            base=base.strip().rstrip('/');model=model.strip()
            if key is None or key=='':
                if provider!=old.provider or base!=old.base_url.rstrip('/'):
                    raise AgentError('更换供应商或接口地址时，请重新输入对应API Key。',code='key_required',http_status=400,delivery='not_sent')
                key=old.api_key
            if not isinstance(key,str) or len(key)>8192:
                raise AgentError('API Key格式无效。',code='invalid_config',http_status=400,delivery='not_sent')
            c=AgentConfig(api_key=key.strip(),base_url=base,model=model,provider=provider)
            if not c.ready():raise AgentError('请填写API Key和接口地址。',code='invalid_config',http_status=400,delivery='not_sent')
            u=urlsplit(base)
            if u.path.rstrip('/').endswith(('/chat/completions','/models')):
                raise AgentError('请填写Base URL，不包含 /chat/completions 或 /models。',code='invalid_config',http_status=400,delivery='not_sent')
            if provider=='deepseek' and base not in ('https://api.deepseek.com','https://api.deepseek.com/v1'):
                raise AgentError('DeepSeek请选择官方接口地址；自定义服务请选择兼容接口并填写对应密钥。',code='invalid_config',http_status=400,delivery='not_sent')
            try:write_config(self.config_path,{**c.__dict__,'billing_mode':'personal'})
            except (OSError,ValueError):raise AgentError('保存本机接口配置失败，请检查目录权限与系统密钥保护。',code='config_save_failed',delivery='not_sent') from None
            self.config=None;self.transport=complete;self.connection=None
            return self.public_config()
        finally:self.configuration_lock.release()

    def forget_key(self):
        self._configuration_guard()
        try:
            if self.public_config()['environment_managed']:
                raise AgentError('接口由环境变量管理，请在本机环境变量中移除密钥。',code='environment_managed',http_status=409,delivery='not_sent')
            try:c=self.settings()
            except AgentError:c=AgentConfig()
            try:write_config(self.config_path,{**c.__dict__,'api_key':'','billing_mode':'personal'})
            except (OSError,ValueError):raise AgentError('移除密钥失败，请检查本机目录权限。',code='config_save_failed',delivery='not_sent') from None
            self.config=None;self.transport=complete;self.connection=None
            return self.public_config()
        finally:self.configuration_lock.release()

    def status(self):
        try:
            c=self.settings();ready=c.ready()
            connection=self.connection or {}
            return {'configured':ready,'provider':c.provider,'model':c.model,
                    'message':connection.get('message','已配置 · 尚未验证连接' if ready else '文字Agent未配置 · 可继续使用调音台'),
                    'connected':connection.get('connected'),'checked_at':connection.get('checked_at'),
                    'audio_uploaded':False,'billing_mode':'personal' if self.personal_api_enabled() else 'development'}
        except AgentError as exc:return {'configured':False,'message':str(exc),'audio_uploaded':False}

    def check_connection(self):
        """Explicit free model-list check; does not run chat or upload audio."""
        self._configuration_guard()
        try:return self._check_connection()
        finally:self.configuration_lock.release()

    def _check_connection(self):
        try:
            c=self.settings()
            if not c.ready():raise AgentError('文字Agent尚未配置',code='not_configured',delivery='not_sent')
            data=provider_json(c,'/models',12)
            models=data.get('data')
            if not isinstance(models,list) or any(not isinstance(m,dict) or not isinstance(m.get('id'),str) for m in models):
                raise provider_error(ValueError())
            if c.model not in {m['id'] for m in models}:
                raise AgentError('接口已连接，但当前模型不在账号可用列表中，请在本机配置正确模型。',code='model_unavailable',http_status=502,delivery='response_received')
        except AgentError as exc:
            self.connection={'connected':False,'checked_at':int(time.time()),'message':str(exc)}
            raise
        self.connection={'connected':True,'checked_at':int(time.time()),'message':'接口与模型可用 · 连接检查通过（未调用推理）'}
        return self.status()

    def request_status(self,request_id):
        if not isinstance(request_id,str) or not re.fullmatch(r'[0-9a-f]{32}',request_id):raise ValueError('请求编号无效')
        with self.requests_lock:
            entry=self.requests.get(request_id)
            if not entry or (entry['state']!='pending' and time.monotonic()-entry['updated']>600):
                return {'request_id':request_id,'state':'not_found','message':'请求记录已过期或服务已重启，未自动重发。'}
            return copy.deepcopy({k:v for k,v in entry.items() if k not in ('fingerprint','updated')})

    def turn(self,body):
        request_id=body.get('request_id',uuid.uuid4().hex)
        if not isinstance(request_id,str) or not re.fullmatch(r'[0-9a-f]{32}',request_id):raise ValueError('请求编号须为32位小写十六进制')
        fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        with self.requests_lock:
            self.requests={k:v for k,v in self.requests.items() if v['state']=='pending' or time.monotonic()-v['updated']<=600}
            entry=self.requests.get(request_id)
            if entry:
                if entry['fingerprint']!=fingerprint:raise AgentError('同一请求编号对应不同内容，请重新发送。',code='request_conflict',http_status=409,request_id=request_id,delivery='not_sent')
                if entry['state']=='pending':raise AgentError('本轮仍在处理，请取回结果，不要重复发送。',code='request_pending',http_status=409,request_id=request_id)
                if entry['state']=='completed':return copy.deepcopy(entry['result'])
                error=entry['failure']
                raise AgentError(error['error'],**{k:v for k,v in error.items() if k!='error'})
            if len(self.requests)>=32:
                oldest=next((k for k,v in self.requests.items() if v['state']!='pending'),None)
                if oldest is None:raise AgentError('请求过多，请稍候',code='agent_busy',http_status=409,delivery='not_sent')
                del self.requests[oldest]
            entry={'request_id':request_id,'state':'pending','updated':time.monotonic(),'fingerprint':fingerprint}
            self.requests[request_id]=entry
        try:
            self._configuration_guard()
            try:result={**self._turn(body),'request_id':request_id}
            finally:self.configuration_lock.release()
        except Exception as exc:
            if not isinstance(exc,AgentError):
                exc=AgentError(str(exc) if isinstance(exc,ValueError) else '本轮处理异常，未应用修改；未自动重发。',
                               code='invalid_request' if isinstance(exc,ValueError) else 'agent_internal_error',http_status=400 if isinstance(exc,ValueError) else 500)
            exc.request_id=request_id
            failure={**exc.as_dict(),'http_status':exc.http_status}
            with self.requests_lock:entry.update(state='failed',failure=failure,updated=time.monotonic())
            raise exc from None
        with self.requests_lock:entry.update(state='completed',result=copy.deepcopy(result),updated=time.monotonic())
        return result

    def _turn(self,body):
        c=self.settings()
        if not c.ready():raise AgentError('文字Agent尚未配置。请在本机运行 tools/configure_text_agent.py；密钥不要发到聊天里。')
        message=body.get('message');kind=body.get('kind');history=body.get('history',[])
        mode=body.get('mode','pro')
        if mode not in ('smart','pro'):raise ValueError('请选择智能模式或开发者模式')
        smart=mode=='smart'
        if not isinstance(message,str) or not 0<len(message.strip())<=2000:raise ValueError('请输入1–2000字的混音想法')
        if kind not in ('pair','set'):raise ValueError('请选择双轨或串烧作品')
        if not isinstance(history,list) or len(history)>16:raise ValueError('对话历史过长')
        for m in history:
            if not isinstance(m,dict) or set(m)!={'role','content'} or m['role'] not in ('user','assistant') or not isinstance(m['content'],str) or len(m['content'])>4000:raise ValueError('对话历史格式无效')
        t=AgentTools(self.service,kind,body.get('project'),body.get('workspaces'))
        selected=body.get('selected_track_ids',[])
        if not isinstance(selected,list) or len(selected)>8 or any(not isinstance(k,str) or k not in t.assets for k in selected):raise ValueError('请重新选择本轮本机素材（最多8份）')
        t.selected_track_ids=list(dict.fromkeys(selected))
        if t.project:t.validate(t.project)
        # Capability gaps are product facts, independent of model eloquence or
        # retrieved recipes. Do not silently turn them into unrequested effects.
        missing=[];missing_sources=[]
        for match in re.finditer(r'鸟鸣|水滴(?:音|声)?|3[dD](?:\s*环绕)?|三维环绕|空间音频',message):
            prefix=re.split(r'[，,。;；\n]|但是|而是|然后|只要',message[max(0,match.start()-18):match.start()])[-1]
            if re.search(r'不要|不需要|不用|不做|不加|别|取消|去掉|关闭|移除',prefix):continue
            label='3D环绕' if re.match(r'3[dD]|三维|空间',match.group()) else match.group()
            if label!='3D环绕':
                source_pattern=r'鸟鸣|鸟叫|bird|chirp' if label=='鸟鸣' else r'水滴|water|drop'
                if any(re.search(source_pattern,t.get('name',''),re.I) for t in t.assets.values()):continue
                if label not in missing_sources:missing_sources.append(label)
                continue
            if label not in missing:missing.append(label)
        if missing or missing_sources:
            explanation=('当前还未接入'+'、'.join(missing)+'。' if missing else '')
            if missing_sources:explanation+='当前未找到'+'、'.join(missing_sources)+'本机音效，请先上传对应素材；已有音效可按指定时间取样叠加，不能凭空生成。'
            return {'reply':explanation+'本轮没有改工程或生成音频。',
                    'project':t.project,'kind':kind,'commands':[],'events':[],'reasons':[],'source':'capability_guard'}
        # Deterministic guard for the most common destructive ambiguity, not a fake model reply.
        if re.search(r'人声.{0,6}(?:高|低)(?:一?点|一些)',message) and not re.search(r'音量|音高|分贝|dB|db|半音|升调|降调|响|轻',message):
            return {'reply':('你想把人声的音量调一调，还是让唱的调子变一变？' if smart else '你说的人声高/低一点，是调整音量（更响/更轻），还是音高（升调/降调）？'),'project':t.project,'kind':kind,'commands':[],'events':[],'reasons':[],'source':'clarification_guard'}
        if not self.lock.acquire(blocking=False):raise AgentError('正在处理上一条灵感，请稍候',code='agent_busy',http_status=409,delivery='not_sent',retryable=True)
        try:
            messages=[{'role':'system','content':_operator_prompt(smart)},*history,
                      {'role':'user','content':json.dumps({'request':message,'workbench':t.context()},ensure_ascii=False,allow_nan=False)}]
            deadline=time.monotonic()+65;count=0
            active_tools=TOOLS
            for round_index in range(5):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise AgentError('模型处理超时，未应用修改；请缩小这次请求')
                # Always leave one bounded round for the final answer; a model
                # must not lose a valid prepared plan by endlessly adding tools.
                payload={'model':c.model,'messages':messages,'tools':active_tools,
                         'tool_choice':'none' if round_index==4 else 'auto','max_tokens':(180 if smart and round_index==4 else 2200)}
                if c.provider=='qwen':payload['enable_thinking']=False
                elif c.provider=='deepseek':payload['thinking']={'type':'disabled'}
                response=self.transport(c,payload,min(25,remaining))
                if not isinstance(response,dict):raise AgentError('模型回复格式无效，未应用修改')
                calls=response.get('tool_calls') or []
                if calls and round_index==4:raise AgentError('模型未按要求结束工具操作，未应用修改；请重试')
                if not calls:
                    reply=response.get('content')
                    if not isinstance(reply,str) or not reply.strip():raise AgentError('模型未返回文字，请重试')
                    if t.kind=='set' and t.project and t.project!=t.original:
                        details,t.reasons=set_plan_explanation(t.project,t.assets,t.commands)
                        reply=("串烧方案准备好了，试听后再看合不合你的口味。" if smart else f"已准备{len(t.project['clips'])}段串烧，预计{set_layout(t.project)['duration']:.2f}秒。保持当前选段与调音；交接依据和片段细节可展开查看。实际听感需试听确认。")
                        t.reasons.append(details)
                    if smart and not t.events:
                        reply=smart_dialogue_reply(reply)
                    return {'reply':reply[:4000],'kind':t.kind,'project':t.project,'commands':t.commands,'events':t.events,'reasons':t.reasons,'source':'model'}
                if not isinstance(calls,list) or len(calls)>8:raise AgentError('模型一次请求过多工具，未应用修改')
                clean=[]
                for call in calls:
                    if not isinstance(call,dict) or not isinstance(call.get('id'),str) or not isinstance(call.get('function'),dict):raise AgentError('模型工具调用格式无效')
                    f=call['function']
                    if not isinstance(f.get('name'),str) or not isinstance(f.get('arguments'),str):raise AgentError('模型工具参数格式无效')
                    clean.append({'id':call['id'],'type':'function','function':{'name':f['name'],'arguments':f['arguments']}})
                if len({v['id'] for v in clean})!=len(clean):raise AgentError('模型工具编号重复，未应用修改')
                messages.append({'role':'assistant','content':response.get('content') if isinstance(response.get('content'),str) else None,'tool_calls':clean})
                for call in clean:
                    count+=1
                    if count>12:raise AgentError('本次操作过多，未应用修改；请分两次完成')
                    try:args=strict_json(call['function']['arguments'])
                    except (ValueError,TypeError):result={'ok':False,'error':'工具参数不是有效JSON'}
                    else:result=t.call(call['function']['name'],args)
                    t.events.append({'tool':call['function']['name'],'result':result})
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False,allow_nan=False)})
            raise AgentError('模型未在限定步骤内完成，未应用修改；请简化请求')
        finally:self.lock.release()
