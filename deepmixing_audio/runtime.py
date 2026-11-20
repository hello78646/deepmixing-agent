"""Discover installed tools without downloading binaries or executing a shell."""
from functools import lru_cache
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def prepare_dependencies():
    local=ROOT/'reference-analysis/python-deps'
    if local.is_dir() and str(local) not in sys.path:
        # Reuse the previously installed workspace runtime, below normal packages.
        sys.path.append(str(local))


prepare_dependencies()


@lru_cache(maxsize=1)
def ffmpeg_path():
    configured=os.environ.get('DEEPMIXING_FFMPEG')
    if configured:
        if not Path(configured).is_file():
            raise RuntimeError('DEEPMIXING_FFMPEG 指向的文件不存在')
        return configured
    found=shutil.which('ffmpeg')
    if found:
        return found
    if importlib.util.find_spec('imageio_ffmpeg'):
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    raise RuntimeError('需要安装FFmpeg或 requirements-audio.txt 中的依赖')


def run_ffmpeg(args, timeout=240, loglevel='error'):
    command=[ffmpeg_path(),'-hide_banner','-nostdin','-v',loglevel,*map(str,args)]
    with ExitStack() as cleanup:
        # Windows cannot launch command lines longer than 32,767 characters.
        # Store only internally generated filter data in an owned temporary file;
        # the file is removed on success, processing failure, and timeout alike.
        script_indexes=[]
        for index, argument in enumerate(command[:-1]):
            if argument == '-filter_complex' and len(command[index+1]) > 12000:
                directory=cleanup.enter_context(tempfile.TemporaryDirectory(prefix='deepmixing-filters-'))
                script=Path(directory)/'filters.txt'
                script.write_text(command[index+1],encoding='utf-8')
                # FFmpeg 9 removed filter_complex_script; the generic option
                # file syntax loads the same graph without a long command.
                command[index]='-/filter_complex'
                command[index+1]=str(script)
                script_indexes.append(index)
        try:
            result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if (result.returncode and script_indexes and
                    b'Unrecognized option' in result.stderr and b'/filter_complex' in result.stderr):
                # Support older local binaries that predate generic file options.
                for index in script_indexes:
                    command[index]='-filter_complex_script'
                result=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('音频处理超时，请缩短素材后重试') from exc
    if result.returncode:
        detail=result.stderr.decode('utf-8',errors='replace')[-700:]
        raise ValueError('音频处理失败：'+detail)
    return result


def capabilities():
    result=run_ffmpeg(['-filters'])
    filters=result.stdout.decode(errors='replace')
    from .separation_runtime import model_status
    return {'audio_engine':True,'rubberband': ' rubberband ' in filters,
            'stem_separation':model_status()['ready'],'stem_separation_api':True,
            'llm_connected':False,'planner':'rules_and_measured_audio',
            'video_audio_extraction':True,'direct_media_url_import':True,
            'local_name_search':True,'online_song_catalog':False,'platform_page_audio_import':False,
            'melody_cue_analysis':importlib.util.find_spec('librosa') is not None,
            'automatic_phrase_detection':False,'general_prompt_understanding':False}
