"""Localhost-only workbench API with explicit file routes and write protection."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import socket
from urllib.parse import urlsplit,parse_qs,quote
import uuid

from .runtime import ROOT, capabilities
from .service import MixingService, MAX_UPLOAD
from .separation_runtime import model_status
from .stage_assets import STAGE_ASSETS, MIME
from .text_agent import TextAgent, AgentError


VISION_ASSETS={f'/assets/mix-vision/{name}':mime for name,mime in (
    ('mediapipe.js','text/javascript; charset=utf-8'),
    ('vision_wasm_nosimd_internal.js','text/javascript; charset=utf-8'),
    ('vision_wasm_nosimd_internal.wasm','application/wasm'),
    ('gesture_recognizer.task','application/octet-stream'),
)}


class LocalServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False

    def server_bind(self):
        # Windows SO_REUSEADDR permits two listeners to share one port. Use
        # exclusive binding so a restart cannot leave requests on an old engine.
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server_version='deepmixing/0.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self,*args):
        pass

    def _headers(self,status,length,content_type,extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(length))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        ancestors="'self'" if urlsplit(self.path).path=='/avatar' or urlsplit(self.path).path in STAGE_ASSETS else "'none'"
        # Allow WASM compilation only inside the dedicated local video worker.
        if urlsplit(self.path).path=='/mix-vision-worker.js':
            policy="default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; worker-src 'none'; frame-ancestors 'none'"
        else:
            policy="default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors "+ancestors
        self.send_header('Content-Security-Policy',policy)
        for key,value in (extra or {}).items():
            self.send_header(key,value)
        self.end_headers()

    def _json(self,status,data):
        body=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self._headers(status,len(body),'application/json; charset=utf-8')
        if self.command!='HEAD':
            self.wfile.write(body)

    def _guard(self,write=False):
        hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.headers.get('Host','').lower() not in hosts:
            raise PermissionError('仅允许从本机工作台访问')
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+host for host in hosts}:
            raise PermissionError('不接受其他网站的请求')
        if write and not secrets.compare_digest(self.headers.get('X-Deepmixing-Token',''),self.server.token):
            raise PermissionError('请刷新本机工作台后再操作')

    def _length(self,maximum):
        raw=self.headers.get('Content-Length','')
        if not raw.isdigit() or self.headers.get('Transfer-Encoding'):
            raise ValueError('需要明确的Content-Length，不接受分块请求')
        length=int(raw)
        if not 0<length<=maximum:
            raise ValueError('请求内容为空或超过大小限制')
        return length

    def _body(self):
        if self.headers.get_content_type()!='application/json':
            raise ValueError('请求须为application/json')
        length=self._length(256*1024)
        raw=self.rfile.read(length)
        if len(raw)!=length:
            raise ValueError('请求不完整')
        body=json.loads(raw.decode('utf-8'),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('JSON数字不能是NaN或Infinity')))
        if not isinstance(body,dict):
            raise ValueError('请求须为JSON对象')
        return body

    def _file(self,path,content_type,download=False):
        size=path.stat().st_size
        start,end=0,size-1
        status=200
        extra={'Accept-Ranges':'bytes'}
        requested=self.headers.get('Range')
        if requested:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
            if not match or not any(match.groups()):
                self._headers(416,0,content_type,{'Content-Range':f'bytes */{size}'})
                return
            left,right=match.groups()
            if left:
                start=int(left)
                end=min(int(right),end) if right else end
            else:
                start=max(0,size-int(right))
            if start>end or start>=size:
                self._headers(416,0,content_type,{'Content-Range':f'bytes */{size}'})
                return
            status=206
            extra['Content-Range']=f'bytes {start}-{end}/{size}'
        if download:
            extra['Content-Disposition']=f'attachment; filename="{path.name}"'
        self._headers(status,end-start+1,content_type,extra)
        if self.command=='HEAD':
            return
        with path.open('rb') as source:
            source.seek(start)
            remaining=end-start+1
            while remaining:
                data=source.read(min(65536,remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining-=len(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._guard()
            parsed=urlsplit(self.path)
            path=parsed.path
            if path=='/api/status':
                self._json(200,{'ready':True,'capabilities':self.server.capabilities})
            elif path=='/api/tracks':
                self._json(200,self.server.service.list_tracks())
            elif path=='/api/agent/status':
                self._json(200,self.server.text_agent.status())
            elif path=='/api/voice/status':
                self._json(200,self.server.service.voice.status())
            elif re.fullmatch(r'/voice-audio/[0-9a-f]{32}\.mp3',path):
                self._file(self.server.service.voice.audio(path.rsplit('/',1)[1]),'audio/mpeg')
            elif path=='/api/agent/config':
                self._json(200,self.server.text_agent.public_config())
            elif re.fullmatch(r'/api/agent/requests/[0-9a-f]{32}',path):
                self._json(200,self.server.text_agent.request_status(path.rsplit('/',1)[1]))
            elif path=='/api/separation/status':
                self._json(200,model_status())
            elif re.fullmatch(r'/api/separations/[0-9a-f]{32}',path):
                self._json(200,self.server.service.separation.job(path.rsplit('/',1)[1]))
            elif path=='/api/library/search':
                query=parse_qs(parsed.query).get('q',[''])[0]
                matches=self.server.service.search_tracks(query)
                encoded=quote(query,safe='')
                self._json(200,{'scope':'local_only','tracks':matches,'online_catalog_connected':False,
                    'platform_search_links':[
                        {'id':'netease','name':'网易云音乐','url':'https://music.163.com/#/search/m/?s='+encoded+'&type=1'},
                        {'id':'qqmusic','name':'QQ音乐','url':'https://y.qq.com/n/ryqq/search?w='+encoded}],
                    'message':'目前搜索本机已导入素材；平台按钮在新页面搜索歌曲，不会自动导入完整音频。'})
            elif re.fullmatch(r'/api/jobs/[0-9a-f]{32}',path):
                self._json(200,self.server.service.job(path.rsplit('/',1)[1]))
            elif re.fullmatch(r'/api/automation/[0-9a-f]{32}',path):
                self._json(200,self.server.service.automation.job(path.rsplit('/',1)[1]))
            elif re.fullmatch(r'/automation-outputs/[0-9a-f]{32}/[^/]+',path):
                _,_,identifier,name=path.split('/')
                content_type={'audio.wav':'audio/wav','audio.mp3':'audio/mpeg','project.json':'application/json; charset=utf-8','report.json':'application/json; charset=utf-8'}.get(name,'audio/mpeg')
                self._file(self.server.service.automation.output_file(identifier,name),content_type,
                           name.endswith('.json') or parse_qs(parsed.query).get('download')==['1'])
            elif path.startswith('/media/'):
                self._file(self.server.service.track_file(path[7:]),'audio/wav',parse_qs(parsed.query).get('download')==['1'])
            elif re.fullmatch(r'/outputs/[0-9a-f]{32}/[^/]+',path):
                _,_,identifier,name=path.split('/')
                content_type={'audio.wav':'audio/wav','audio.mp3':'audio/mpeg','project.json':'application/json; charset=utf-8'}.get(name,'application/octet-stream')
                self._file(self.server.service.output_file(identifier,name),content_type,
                           name=='project.json' or parse_qs(parsed.query).get('download')==['1'])
            elif path=='/assets/forest-room-reference.jpg':
                self._file(ROOT/'web'/'assets'/'forest-room-reference.jpg','image/jpeg')
            elif path=='/assets/headphone-mark.svg':
                self._file(ROOT/'web'/'assets'/'headphone-mark.svg','image/svg+xml')
            elif path in VISION_ASSETS:
                self._file(ROOT/'web'/path[1:],VISION_ASSETS[path])
            elif path in STAGE_ASSETS:
                asset=STAGE_ASSETS[path]
                self._file(asset,MIME.get(asset.suffix,'application/octet-stream'))
            elif path in ('/','/workbench','/classic','/workbench-shell.js','/workbench-shell.css','/agent-actions.js','/agent-beginner.js','/agent-chat.js','/agent-chat.css','/app.js','/style.css','/imports.js','/imports.css','/stems.js','/stems.css','/cues.js','/live-stage.js','/live-stage.css',
                          '/music-guides.js','/music-guides.css','/music-guide-math.js','/guide-corrections.js','/continuous.js','/continuous.css',
                          '/avatar','/avatar.css','/avatar.js','/avatar-model.js','/avatar-image.js','/avatar-store.js',
                          '/avatar-entry.js','/avatar-entry.css','/agent-settings.js','/automation.js',
                          '/immersive-shell.js','/immersive-shell.css','/agent-voice.js','/workbench-opening.js','/workbench-opening.css',
                          '/mix-vision.js','/mix-vision.css','/mix-vision-rules.js','/mix-vision-worker.js'):
                filename='workbench.html' if path in ('/','/workbench') else 'index.html' if path=='/classic' else 'avatar.html' if path=='/avatar' else path[1:]
                body=(ROOT/'web'/filename).read_bytes()
                if path in ('/','/workbench','/classic'):
                    body=body.replace(b'__TOKEN__',self.server.token.encode())
                content_type='text/html; charset=utf-8' if filename.endswith('.html') else 'text/javascript; charset=utf-8' if filename.endswith('.js') else 'text/css; charset=utf-8'
                self._headers(200,len(body),content_type)
                if self.command!='HEAD':
                    self.wfile.write(body)
            else:
                self._json(404,{'error':'页面或接口不存在'})
        except (BrokenPipeError,ConnectionResetError):
            pass
        except PermissionError as exc:
            self._json(403,{'error':str(exc)})
        except AgentError as exc:
            self._json(exc.http_status,exc.as_dict())
        except (ValueError,KeyError,UnicodeError) as exc:
            self._json(400,{'error':str(exc)})
        except OSError:
            self._json(404,{'error':'文件不可用'})

    def do_POST(self):
        try:
            self._guard(write=True)
            parsed=urlsplit(self.path)
            if parsed.path=='/api/tracks':
                length=self._length(MAX_UPLOAD)
                names=parse_qs(parsed.query).get('name',[])
                if len(names)!=1:
                    raise ValueError('请提供一个音频文件名')
                temporary=self.server.service.root/'incoming'/uuid.uuid4().hex
                try:
                    with temporary.open('xb') as target:
                        remaining=length
                        while remaining:
                            part=self.rfile.read(min(65536,remaining))
                            if not part:
                                raise ValueError('上传中断，请重新选择文件')
                            target.write(part)
                            remaining-=len(part)
                    self._json(201,self.server.service.ingest(temporary,names[0]))
                finally:
                    temporary.unlink(missing_ok=True)
            elif parsed.path=='/api/automation':
                self._json(202,self.server.service.automation.submit(self._body()))
            elif re.fullmatch(r'/api/automation/[0-9a-f]{32}/cancel',parsed.path):
                self._body()
                self._json(200,self.server.service.automation.cancel(parsed.path.split('/')[3]))
            elif parsed.path=='/api/recommendations':
                body=self._body()
                self._json(200,self.server.service.recommend(body.get('a_id'),body.get('b_id'),
                    body.get('mode'),body.get('prompt',''),body.get('a_role','full_mix'),body.get('b_role','full_mix'),
                    body.get('arrangement','together'),styles=body.get('styles'),features=body.get('features')))
            elif parsed.path=='/api/agent/chat':
                self._json(200,self.server.text_agent.turn(self._body()))
            elif parsed.path=='/api/voice/speech':
                self._json(200,self.server.service.voice.speech(self._body()))
            elif parsed.path=='/api/voice/transcribe':
                from .voice import MAX_RECORDING
                length=self._length(MAX_RECORDING)
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError('录音上传不完整')
                language=self.headers.get('X-Deepmixing-ASR-Language','auto')
                self._json(200,self.server.service.voice.transcribe(raw, language))
            elif parsed.path=='/api/agent/check':
                self._body()
                self._json(200,self.server.text_agent.check_connection())
            elif parsed.path=='/api/agent/config':
                self._json(200,self.server.text_agent.configure(self._body()))
            elif parsed.path=='/api/agent/config/forget':
                self._body()
                self._json(200,self.server.text_agent.forget_key())
            elif parsed.path=='/api/sets/recommendations':
                body=self._body()
                self._json(200,self.server.service.recommend_set(body.get('track_ids'),body.get('style','journey')))
            elif parsed.path=='/api/sets/renders':
                body=self._body()
                self._json(202,self.server.service.submit_set(body.get('project'),body.get('preview',True),body.get('approved_preview_id'),body.get('request_id')))
            elif parsed.path=='/api/music/inspect':
                body=self._body()
                self._json(200,self.server.service.inspect_music(body.get('project')))
            elif parsed.path=='/api/renders':
                body=self._body()
                self._json(202,self.server.service.submit(body.get('project'),preview=body.get('preview',True),
                                                          approved_preview_id=body.get('approved_preview_id'),request_id=body.get('request_id')))
            elif parsed.path=='/api/separations':
                body=self._body()
                if set(body)-{'track_id','all_stems'}:raise ValueError('分轨请求含未知参数')
                self._json(202,self.server.service.separation.submit(body.get('track_id'),all_stems=body.get('all_stems',False)))
            elif re.fullmatch(r'/api/separations/[0-9a-f]{32}/cancel',parsed.path):
                self._body()
                self._json(200,self.server.service.separation.cancel(parsed.path.split('/')[3]))
            elif parsed.path=='/api/imports/link':
                body=self._body()
                result=self.server.service.import_link(body.get('text'))
                self._json(201 if result['audio_imported'] else 200,result)
            else:
                self._json(404,{'error':'接口不存在'})
        except (BrokenPipeError,ConnectionResetError):
            pass
        except PermissionError as exc:
            self._json(403,{'error':str(exc)})
        except AgentError as exc:
            self._json(exc.http_status,exc.as_dict())
        except (ValueError,KeyError,TypeError,UnicodeError,socket.timeout) as exc:
            self._json(400,{'error':str(exc)})
        except (OSError,RuntimeError) as exc:
            self._json(500,{'error':str(exc)})


def make_server(port=8765,root=None):
    capability=capabilities()
    if not capability['rubberband']:
        raise RuntimeError('当前FFmpeg缺少rubberband，请配置支持该滤镜的版本')
    server=LocalServer(('127.0.0.1',port),Handler)
    server.token=secrets.token_urlsafe(32)
    server.service=MixingService(root or ROOT/'data')
    server.text_agent=TextAgent(server.service)
    server.capabilities=capability
    return server
