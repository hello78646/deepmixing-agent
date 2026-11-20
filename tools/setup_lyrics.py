"""Explicit one-time local multilingual ASR install; never uploads audio.

Downloads happen only when this setup script is invoked without --skip-download.
The music-processing worker always uses local_files_only=True and offline mode.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deepmixing_audio.semantic_audio import (
    ENVIRONMENT, FASTER_WHISPER_VERSION, MODEL_DIRECTORY, MODEL_REPOSITORY,
    lyrics_status, worker_python,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-download', action='store_true', help='安装依赖但不下载模型')
    parser.add_argument('--skip-dependencies', action='store_true', help='复用已安装的独立运行环境')
    args = parser.parse_args()
    # Anonymous public download only. Do not load, print or depend on HF tokens.
    environment = {**os.environ, 'PYTHONUTF8': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1',
                   'HF_TOKEN': '', 'HUGGING_FACE_HUB_TOKEN': ''}
    if not args.skip_dependencies:
        if not worker_python().is_file():
            venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
        subprocess.run([str(worker_python()), '-m', 'pip', 'install',
            f'faster-whisper=={FASTER_WHISPER_VERSION}', 'huggingface-hub==0.36.0',
            '--cache-dir', str(ROOT / 'reference-analysis/lyrics-pip-cache'),
            '--disable-pip-version-check'], cwd=ROOT, env=environment, check=True)
    if not args.skip_download:
        if not worker_python().is_file():
            raise RuntimeError('请先安装本地歌词识别运行环境')
        MODEL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        download = (
            'from huggingface_hub import snapshot_download; '
            'import sys; '
            'snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2], token=False, '
            'allow_patterns=["config.json","model.bin","tokenizer.json","vocabulary.*","preprocessor_config.json"])'
        )
        subprocess.run([str(worker_python()), '-c', download, MODEL_REPOSITORY, str(MODEL_DIRECTORY)],
                       cwd=ROOT, env=environment, check=True)
    status = lyrics_status()
    print(status['reason'])
    if not args.skip_download and not status['ready']:
        raise RuntimeError('歌词识别安装未通过本地模型加载验证')


if __name__ == '__main__':
    main()
