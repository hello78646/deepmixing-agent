"""Explicit one-time install; music-processing requests never download assets."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deepmixing_audio.separation_runtime import ENVIRONMENT,MODEL_PATH,MODEL_URL,MODEL_HASH_PREFIX,worker_python


def install_model(source):
    MODEL_PATH.parent.mkdir(parents=True,exist_ok=True)
    temporary=MODEL_PATH.with_suffix('.part')
    try:
        digest=hashlib.sha256();size=0
        with temporary.open('wb') as target:
            if source:
                stream=Path(source).open('rb')
            else:
                stream=urllib.request.urlopen(MODEL_URL,timeout=30)
            with stream:
                while chunk:=stream.read(1024*1024):
                    size+=len(chunk)
                    if size>200*1024*1024:
                        raise ValueError('模型文件超过预期大小')
                    digest.update(chunk);target.write(chunk)
        checksum=digest.hexdigest()
        if not checksum.startswith(MODEL_HASH_PREFIX) or size<50*1024*1024:
            raise ValueError('模型与官方清单校验不一致，未安装')
        temporary.replace(MODEL_PATH)
        (MODEL_PATH.parent/'installation.json').write_text(json.dumps({
            'model':'htdemucs','sha256':checksum,'size':size,'official_url':MODEL_URL,
            'source':'local_import' if source else MODEL_URL},indent=2),encoding='utf-8')
        print('模型校验与安装完成：'+checksum)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-file',type=Path,help='导入已下载的官方955717e8-8726e21a.th，仍执行校验')
    parser.add_argument('--skip-dependencies',action='store_true')
    args=parser.parse_args()
    if not args.skip_dependencies:
        if not worker_python().is_file():
            venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
        subprocess.run([str(worker_python()),'-m','pip','install','-r',str(ROOT/'requirements-separation.txt'),
            '--cache-dir',str(ROOT/'reference-analysis/separation-pip-cache'),'--disable-pip-version-check'],check=True)
    if args.model_file or not MODEL_PATH.is_file():
        install_model(args.model_file)
    subprocess.run([str(worker_python()),'-m','deepmixing_audio.separation_worker','--probe'],cwd=ROOT,check=True)


if __name__=='__main__':
    main()
