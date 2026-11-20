"""Local-only settings. Windows protects newly saved keys with user-scoped DPAPI."""
import base64
import ctypes
import json
import os
from pathlib import Path
import tempfile


def _protect(raw,encrypt):
    class Blob(ctypes.Structure):
        _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    library=ctypes.WinDLL('crypt32',use_last_error=True)
    method=library.CryptProtectData if encrypt else library.CryptUnprotectData
    if not method(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ValueError('本机密钥保护不可用，请在当前Windows用户下重新配置')
    try:return ctypes.string_at(target.data,target.size)
    finally:
        free=ctypes.WinDLL('kernel32').LocalFree
        free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
        free(ctypes.cast(target.data,ctypes.c_void_p))


def read_config(path):
    path=Path(path)
    if not path.is_file():return {}
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict):raise ValueError()
        if 'api_key_protected' in data:
            if os.name!='nt' or data.get('key_storage')!='windows-dpapi':raise ValueError()
            data['api_key']=_protect(base64.b64decode(data.pop('api_key_protected'),validate=True),False).decode('utf-8')
        return data
    except (OSError,ValueError,TypeError):
        raise ValueError('本机Agent配置无法读取，请在当前系统用户下重新配置') from None


def write_config(path,data):
    """Atomic replacement. No credential backups, plaintext temp files or logs on Windows."""
    saved={k:data[k] for k in ('provider','base_url','model','billing_mode') if k in data}
    key=data.get('api_key','')
    if key and os.name=='nt':
        saved.update(api_key_protected=base64.b64encode(_protect(key.encode('utf-8'),True)).decode('ascii'),key_storage='windows-dpapi')
    else:saved.update(api_key=key,key_storage='file-permissions')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='agent-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(saved,stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)
