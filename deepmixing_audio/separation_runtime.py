"""Fixed local neural worker. No downloads occur on a music-processing request."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from .runtime import ROOT

MODEL='htdemucs'
PROCESSING_REVISION='detail-v1'
INFERENCE_SHIFTS=2
INFERENCE_OVERLAP=.5
INFERENCE_SEGMENT=7
MODEL_FILE='955717e8-8726e21a.th'
MODEL_HASH_PREFIX='8726e21a'
MODEL_URL='https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/'+MODEL_FILE
ENVIRONMENT=ROOT/'reference-analysis/separation-venv'
MODEL_PATH=ROOT/'models/demucs'/MODEL_FILE
NO_WINDOW=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def worker_python():
    return ENVIRONMENT/('Scripts/python.exe' if os.name=='nt' else 'bin/python')


@lru_cache(maxsize=4)
def checked_model(path,size,mtime):
    with Path(path).open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if not digest.startswith(MODEL_HASH_PREFIX):
        raise ValueError('模型校验失败，请重新安装官方模型')
    return digest


def model_digest():
    info=MODEL_PATH.stat()
    return checked_model(str(MODEL_PATH),info.st_size,info.st_mtime_ns)


@lru_cache(maxsize=4)
def probe_runtime(executable,mtime):
    result=subprocess.run([executable,'-m','deepmixing_audio.separation_worker','--probe'],cwd=ROOT,
        capture_output=True,timeout=40,creationflags=NO_WINDOW,env={**os.environ,'PYTHONUTF8':'1'})
    if result.returncode:
        raise RuntimeError('分轨运行环境自检失败，请运行安装脚本修复')
    return json.loads(result.stdout.decode('utf-8').strip().splitlines()[-1])


def model_status():
    info={'ready':False,'model':MODEL,'processing_revision':PROCESSING_REVISION,'local_only':True,'automatic_download':False}
    if not worker_python().is_file():
        return {**info,'message':'本地分轨环境未安装，请先运行 tools/setup_separation.py。'}
    if not MODEL_PATH.is_file():
        return {**info,'message':'本地分轨模型未安装；原混音功能可继续使用。请运行 tools/setup_separation.py。'}
    try:
        digest=model_digest()
        runtime=probe_runtime(str(worker_python()),worker_python().stat().st_mtime_ns)
        return {**info,'ready':True,'model_sha256':digest,'device':runtime['device'],
                'message':'本地细节优先分轨已就绪；处理耗时较长，纯净度仍需对比试听。'}
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        return {**info,'message':str(exc)}


def run_separation(source,directory,cancelled,progress,all_stems=False):
    if type(all_stems) is not bool:
        raise ValueError('all_stems须为布尔值')
    state=model_status()
    if not state['ready']:
        raise ValueError(state['message'])
    command=[str(worker_python()),'-m','deepmixing_audio.separation_worker',
             '--input',str(source),'--output',str(directory),'--parent-pid',str(os.getpid())]
    if all_stems:
        command.append('--all-stems')
    with (directory/'worker.log').open('wb') as log:
        process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=NO_WINDOW,env={**os.environ,'PYTHONUTF8':'1','OMP_NUM_THREADS':'4','MKL_NUM_THREADS':'4'})
        deadline=time.monotonic()+3600
        try:
            while process.poll() is None:
                if cancelled.wait(.2):
                    raise InterruptedError('已取消分轨')
                if time.monotonic()>deadline:
                    raise TimeoutError('分轨超过60分钟，已终止；请缩短素材后重试')
                try:
                    update=json.loads((directory/'progress.json').read_text(encoding='utf-8'))
                    progress(update['stage'],float(update['progress']))
                except (OSError,ValueError,KeyError):
                    pass
            if cancelled.is_set():
                raise InterruptedError('已取消分轨')
            if process.returncode:
                detail=(directory/'worker.log').read_text(encoding='utf-8',errors='replace')[-1200:]
                raise RuntimeError('本地模型处理失败：'+detail)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
    return json.loads((directory/'result.json').read_text(encoding='utf-8'))
