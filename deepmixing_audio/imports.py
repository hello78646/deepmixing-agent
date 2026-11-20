"""Public media-file import, not a platform downloader or access-control bypass."""
import http.client
import ipaddress
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.parse import urlsplit,urlunsplit,urljoin,quote,unquote

MAX_BYTES=80*1024*1024
VIDEO_SUFFIXES={'.mp4','.mov','.mkv','.webm'}
MEDIA_SUFFIXES={'.wav','.mp3','.m4a','.aac','.flac','.ogg','.opus','.aif','.aiff',*VIDEO_SUFFIXES}
CONTENT_SUFFIX={'audio/mpeg':'.mp3','audio/mp3':'.mp3','audio/wav':'.wav','audio/x-wav':'.wav',
    'audio/flac':'.flac','audio/x-flac':'.flac','audio/mp4':'.m4a','audio/aac':'.aac',
    'audio/ogg':'.ogg','video/mp4':'.mp4','video/quicktime':'.mov','video/webm':'.webm'}


def _public_address(value):
    address=ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address,ipaddress.IPv6Address) and (address.ipv4_mapped or address.sixtofour or address.teredo):
        return False
    return True


def classify_link(text):
    if not isinstance(text,str) or len(text)>8192:
        raise ValueError('请粘贴一条链接或包含一条链接的分享文案')
    urls=re.findall(r'https?://[^\s<>"\u201c\u201d]+',text)
    if len(urls)!=1:
        raise ValueError('请一次提供一条明确的HTTPS链接')
    url=urls[0].rstrip('.,;!。，；！）)')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('链接须为不含账户凭证的HTTPS地址')
    host=parsed.hostname.lower()
    providers={'douyin.com':'douyin','iesdouyin.com':'douyin','bilibili.com':'bilibili',
               'youtube.com':'youtube','youtu.be':'youtube','kuaishou.com':'kuaishou',
               'music.163.com':'netease','y.qq.com':'qqmusic','music.qq.com':'qqmusic'}
    provider=next((value for domain,value in providers.items() if host==domain or host.endswith('.'+domain)),None)
    if provider:
        return {'status':'needs_media_file','provider':provider,'url':url,'audio_imported':False,
                'message':'已识别平台分享链接，尚未取得音频。本版未接入该平台的音频导入接口；可上传已保存的音频或视频提取音轨。'}
    return {'status':'direct_media_candidate','provider':'direct','url':url,'audio_imported':False}


def validate_remote_url(url):
    if not isinstance(url,str) or len(url)>8192 or re.search(r'[\x00-\x20\x7f\\]',url):
        raise ValueError('无效的远程链接')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('仅支持不含账户凭证、使用标准端口的公网HTTPS文件链接')
    host=parsed.hostname.encode('idna').decode('ascii').lower()
    try:
        literal=ipaddress.ip_address(host)
    except ValueError:
        literal=None
    if literal is not None and not _public_address(host):
        raise ValueError('不允许访问本机或内网地址')
    try:
        resolved=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError('无法解析文件服务器地址') from exc
    addresses=[row[4][0] for row in resolved]
    if not addresses or any(not _public_address(address) for address in addresses):
        raise ValueError('远程地址解析到本机、内网或非公开地址，已停止导入')
    path=quote(parsed.path or '/',safe="/%:@!$&'()*+,;=-._~")
    query=quote(parsed.query,safe="%:@!$&'()*+,;=/?-._~")
    return {'host':host,'address':addresses[0],'path':path+('?' + query if query else ''),'url':url}


def _open_public(url):
    target=validate_remote_url(url)
    context=ssl.create_default_context()
    connection=http.client.HTTPSConnection(target['host'],443,timeout=15,context=context)
    try:
        raw=socket.create_connection((target['address'],443),timeout=15)
        try:
            connection.sock=context.wrap_socket(raw,server_hostname=target['host'])
        except Exception:
            raw.close()
            raise
        # Pin the already validated IP. HTTPSConnection sends Host and verifies
        # the certificate against the original hostname without a second lookup.
        connection.request('GET',target['path'],headers={'User-Agent':'deepmixing-local-import/0.2',
            'Accept':'audio/*, video/*, application/octet-stream','Accept-Encoding':'identity'})
        return connection,connection.getresponse()
    except (OSError,http.client.HTTPException) as exc:
        connection.close()
        raise ValueError('无法读取远程音视频文件，请检查链接是否可直接访问') from exc


def download_media(url,target):
    target=Path(target)
    current=url
    started=time.monotonic()
    created=False
    try:
        for attempt in range(4):
            connection,response=_open_public(current)
            try:
                if response.status in (301,302,303,307,308):
                    location=response.getheader('Location')
                    if not location or attempt==3:
                        raise ValueError('文件重定向过多或缺少目标地址')
                    current=urljoin(current,location)
                    # Each destination is validated and pinned on the next pass.
                    continue
                if response.status!=200:
                    raise ValueError(f'文件服务器返回{response.status}，没有导入音频')
                content_type=response.getheader('Content-Type','').split(';')[0].strip().lower()
                filename=Path(unquote(urlsplit(current).path)).name
                suffix=Path(filename).suffix.lower()
                media_type=content_type.startswith(('audio/','video/'))
                if not media_type and not (content_type=='application/octet-stream' and suffix in MEDIA_SUFFIXES):
                    raise ValueError('链接返回的是网页或不支持的内容，不是可直接导入的音视频文件。可改为上传已保存的视频。')
                if suffix not in MEDIA_SUFFIXES:
                    suffix=CONTENT_SUFFIX.get(content_type)
                    if not suffix:
                        raise ValueError('无法确定该音视频文件格式，请上传本地文件')
                    filename='链接素材'+suffix
                length=response.getheader('Content-Length')
                if length and (not length.isdigit() or not 0<int(length)<=MAX_BYTES):
                    raise ValueError('远程文件为空或超过80MiB')
                total=0
                with target.open('xb') as output:
                    created=True
                    while True:
                        if time.monotonic()-started>60:
                            raise ValueError('链接导入超过60秒，请下载后从本机上传')
                        try:
                            data=response.read(65536)
                        except (http.client.HTTPException,OSError) as exc:
                            raise ValueError('远程文件传输中断，请重试或下载后上传本地文件') from exc
                        if not data:
                            break
                        total+=len(data)
                        if total>MAX_BYTES:
                            raise ValueError('远程文件超过80MiB')
                        output.write(data)
                if total==0 or (length and total!=int(length)):
                    raise ValueError('远程文件内容不完整')
                parsed=urlsplit(current)
                filename=Path(filename).stem[:160]+suffix
                return {'name':filename,'bytes':total,
                        'source_url':urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'','')),
                        'content_type':content_type}
            finally:
                connection.close()
        raise ValueError('没有取得可用文件')
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
