"""Bounded local dialogue speech. Never uploads audio or downloads a model."""
import argparse
import hashlib
from functools import lru_cache
import json
import math
import os
import re
import queue
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import wave

from .runtime import ROOT, run_ffmpeg
from .semantic_audio import worker_python, MODEL_DIRECTORY, MODEL_FILES, _offline_environment
from .gpt_sovits import GptSovitsVoice

MAX_RECORDING = 8 * 1024 * 1024
ASR_LANGUAGES = ('auto', 'zh', 'en', 'ja', 'ko')
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


@lru_cache(maxsize=1)
def installed_voice_cultures():
    if os.name != 'nt' or not shutil.which('powershell.exe'):
        return ()
    try:
        result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
            '-File', str(ROOT / 'tools/voice_synth.ps1'), '-ListVoices'],
            capture_output=True, timeout=15, creationflags=NO_WINDOW)
        cultures = json.loads(result.stdout.decode('utf-8-sig')) if result.returncode == 0 else []
        return tuple(c for c in cultures if isinstance(c, str)) if isinstance(cultures, list) else ()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ()


def supports_voice(language, cultures):
    if language == 'yue':
        return any(c.lower() in ('zh-hk', 'zh-mo', 'yue-hk', 'yue-cn') for c in cultures)
    if language == 'zh':
        return any(c.lower() in ('zh-cn', 'zh-tw', 'zh-sg') for c in cultures)
    return any(c.split('-')[0].lower() == language for c in cultures)


class LocalRecognitionWorker:
    """Keep the offline model resident between push-to-talk turns."""
    def __init__(self):
        self.process = None
        self.output = None
        self.io_lock = threading.Lock()
        self.closed = False

    def close(self):
        with self.io_lock:
            self.closed = True
            self._close()

    def _close(self):
        process, self.process = self.process, None
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

    @staticmethod
    def _read(stream, output):
        try:
            with stream:
                for line in stream:
                    output.put(line)
        finally:
            output.put(None)

    def warm(self):
        with self.io_lock:
            if self.closed or (self.process is not None and self.process.poll() is None):
                return
            try:
                self._request(None)
            except ValueError:
                pass  # A later real recording may retry a failed warm-up.

    def recognize(self, pcm, language):
        with self.io_lock:
            if self.closed:
                raise ValueError('语音识别已关闭')
            return self._request({'path': str(pcm), 'language': language})

    def _request(self, payload):
        try:
            if self.process is None or self.process.poll() is not None:
                self._close()
                process = subprocess.Popen([str(worker_python()), '-u', '-m', 'deepmixing_audio.voice', '--transcribe-loop'],
                    cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding='utf-8', creationflags=NO_WINDOW, env=_offline_environment())
                self.process, self.output = process, queue.Queue()
                threading.Thread(target=self._read, args=(process.stdout, self.output), daemon=True).start()
            self.process.stdin.write(json.dumps(payload) + '\n')
            self.process.stdin.flush()
            line = self.output.get(timeout=22)
            data = json.loads(line) if line is not None else None
            if not isinstance(data, dict) or 'text' not in data:
                raise ValueError('语音识别未返回有效内容')
            return data
        except (OSError, ValueError, queue.Empty):
            self._close()
            raise ValueError('本机语音识别未完成，请重录或继续打字') from None



class VoiceService:
    def __init__(self, root):
        self.root = Path(root) / 'dialogue-voice'
        self.root.mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self.asr_lock = threading.Lock()
        self.recognizer = LocalRecognitionWorker()
        self.sovits = GptSovitsVoice()

    @staticmethod
    def asr_installed():
        return worker_python().is_file() and all((MODEL_DIRECTORY / name).is_file() for name in MODEL_FILES)

    def prewarm_recognition(self):
        if self.asr_installed():
            threading.Thread(target=self.recognizer.warm, daemon=True, name='deepmixing-asr-warm').start()

    def status(self):
        asr = self.asr_installed()
        cultures = installed_voice_cultures()
        result = dict(asr_ready=asr, asr_model='Whisper tiny multilingual', asr_language='auto',
            asr_languages=list(ASR_LANGUAGES), asr_note='录音前可选语种；方言和混说需要核对文字。',
            tts_ready=bool(cultures), tts_cultures=list(cultures), local_only=True, max_seconds=45,
            voice_label='零氪 · 低沉颗粒 / 原创系统试听声',
            voice_note='Windows中文声音经降调与轻度音色处理；不是角色原声或已定制音色。')
        try:
            config = self.sovits.configuration()
            if config:
                result.update(tts_ready=self.sovits.ready(config), tts_provider='gpt-sovits',
                    tts_cultures=['zh-CN','en-US','zh-HK','ja-JP','ko-KR'],
                    voice_label=config['voice_label'],
                    voice_note='同一音色支持普通话、英语、粤语、日语、韩语；CPU合成需要等待。德语及其他未支持语种保留文字回复。')
        except ValueError as error:
            result.update(tts_ready=False, tts_provider='gpt-sovits', voice_note=str(error))
        return result

    def speech(self, body):
        if not isinstance(body, dict) or set(body) - {'text', 'language'} or 'text' not in body:
            raise ValueError('语音回复只接受文字')
        text = body['text']
        language=body.get('language','zh')
        if not isinstance(language, str) or not re.fullmatch('[a-z]{2,3}|auto', language):
            language='auto'
        if language=='auto':
            language='zh' if any('\u3400'<=c<='\u9fff' for c in str(text)) else 'en'
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 700:
            raise ValueError('单次语音回复须为1–700字')
        status = self.status()
        if not status['tts_ready']:
            if status.get('tts_provider') == 'gpt-sovits':
                raise ValueError('GPT-SoVITS 本机服务尚未就绪；文字回复已保留，请先启动定制语音服务')
            raise ValueError('当前系统没有可用的本地语音，文字回复仍可使用')
        if not self.lock.acquire(blocking=False):
            raise ValueError('语音正在处理，请稍候再试')
        try:
            config = self.sovits.configuration()
            if config:
                return self._sovits_speech(config, text.strip(), language)
            if not supports_voice(language, status.get('tts_cultures', [])):
                raise ValueError('本机尚未安装'+{'yue':'粤语','de':'德语'}.get(language, language)+'声音包；该语言的文字回复已保留')
            key = hashlib.sha256(('lingke-original-v2:' + language + ':' + text.strip()).encode()).hexdigest()[:32]
            destination = self.root / (key + '.mp3')
            if not destination.is_file():
                with tempfile.TemporaryDirectory(dir=self.root, prefix='synth-') as folder:
                    raw = Path(folder) / 'raw.wav'
                    result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive',
                        '-File', str(ROOT / 'tools/voice_synth.ps1')],
                        input=json.dumps(dict(text=text.strip(), language=language, output=str(raw)), ensure_ascii=True).encode(),
                        capture_output=True, timeout=55, creationflags=NO_WINDOW)
                    if result.returncode or not raw.is_file():
                        raise ValueError('本机缺少该语种的声音或合成失败；文字回复保留，可安装对应系统语音包后重试')
                    temporary = Path(folder) / 'voice.mp3'
                    # Preserve word timing while lowering register. Soft saturation
                    # adds restraint, not an imitation or a claim of custom TTS.
                    run_ffmpeg(['-y', '-i', raw, '-af',
                        'rubberband=pitch=0.80,highpass=f=85,lowpass=f=6200,'
                        'asoftclip=type=tanh:threshold=0.88:output=0.92,loudnorm=I=-20:TP=-2:LRA=7',
                        '-ar', '24000', '-ac', '1', '-codec:a', 'libmp3lame', '-b:a', '96k', temporary], timeout=60)
                    temporary.replace(destination)
                # Bound this independent speech cache. Music assets never enter it.
                files = sorted(self.root.glob('*.mp3'), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files[32:]:
                    if old != destination:
                        old.unlink(missing_ok=True)
            return dict(url=f'/voice-audio/{key}.mp3', **self.status())
        except (subprocess.TimeoutExpired, OSError, RuntimeError):
            raise ValueError('本机声音合成超时或失败，文字回复已保留，可稍后重试') from None
        finally:
            self.lock.release()

    def _sovits_speech(self, config, text, language):
        key = self.sovits.cache_key(config, text, language)
        destination = self.root / (key + '.mp3')
        if not destination.is_file():
            with tempfile.TemporaryDirectory(dir=self.root, prefix='sovits-') as folder:
                raw = Path(folder) / 'raw.wav'
                self.sovits.synthesize(config, text, language, raw)
                temporary = Path(folder) / 'voice.mp3'
                # Slightly tighten spoken timing without shifting pitch or changing the trained voice.
                run_ffmpeg(['-y','-protocol_whitelist','file,pipe','-f','wav','-i',raw,'-t','120','-ar','32000','-ac','1',
                    '-af','atempo=1.08,loudnorm=I=-20:TP=-2:LRA=7','-codec:a','libmp3lame','-b:a','160k',temporary], timeout=45)
                temporary.replace(destination)
            files = sorted(self.root.glob('*.mp3'), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[32:]:
                if old != destination:
                    old.unlink(missing_ok=True)
        return dict(url=f'/voice-audio/{key}.mp3', **self.status())

    def transcribe(self, raw, language='auto'):
        if language not in ASR_LANGUAGES:
            raise ValueError('不支持这种录音语言，请重新选择')
        if not self.asr_installed():
            raise ValueError('本地语音识别模型未安装，可继续打字')
        if not 0 < len(raw) <= MAX_RECORDING:
            raise ValueError('录音为空或超过8 MiB')
        if raw.startswith(b'RIFF') and raw[8:12]==b'WAVE':
            container='wav'
        elif raw.startswith(b'OggS'):
            container='ogg'
        elif raw.startswith(b'\x1a\x45\xdf\xa3'):
            container='matroska,webm'
        elif len(raw)>12 and raw[4:8]==b'ftyp':
            container='mov,mp4,m4a,3gp,3g2,mj2'
        else:
            raise ValueError('请使用浏览器录制的WAV、WebM、Ogg或MP4音频')
        if not self.asr_lock.acquire(blocking=False):
            raise ValueError('语音正在处理，请稍候再试')
        try:
            with tempfile.TemporaryDirectory(dir=self.root, prefix='record-') as folder:
                source = Path(folder) / 'recording'; source.write_bytes(raw)
                pcm = Path(folder) / 'speech.wav'
                # No remote protocols. Decode only a bounded local recording.
                run_ffmpeg(['-y', '-protocol_whitelist', 'file,pipe', '-format_whitelist', container,
                    '-i', source, '-t', '46',
                    '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', pcm], timeout=7)
                with wave.open(str(pcm)) as audio:
                    seconds = audio.getnframes() / audio.getframerate()
                    if not .25 <= seconds <= 45.05:
                        raise ValueError('请录制0.25–45秒的语音')
                data = self.recognizer.recognize(pcm, language)
                if not data.get('text'):
                    raise ValueError('没有识别到清楚的语音，请靠近麦克风重录')
                return dict(text=data['text'][:2000], language=data.get('language','auto'),
                    language_probability=data.get('language_probability'), local_only=True, needs_review=True)
        except (subprocess.TimeoutExpired, OSError, RuntimeError):
            raise ValueError('本机语音识别超时或失败，原有文字已保留，可缩短录音重试') from None
        finally:
            self.asr_lock.release()

    def close(self):
        with self.asr_lock:
            self.recognizer.close()

    def audio(self, name):
        if not re.fullmatch('[0-9a-f]{32}\\.mp3', name):
            raise ValueError('无效语音文件')
        path = self.root / name
        if not path.is_file():
            raise ValueError('语音已过期，请重新播放回复')
        return path


@lru_cache(maxsize=1)
def _dialogue_model():
    from faster_whisper import WhisperModel
    return WhisperModel(str(MODEL_DIRECTORY), device='cpu', compute_type='int8', cpu_threads=4,
        num_workers=1, local_files_only=True)


def transcribe_worker(path, language='auto'):
    import numpy as np
    with wave.open(str(path)) as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
    if math.sqrt(float(np.mean(samples ** 2))) < .002:
        return {'text': ''}
    segments, info = _dialogue_model().transcribe(samples, language=None if language == 'auto' else language, beam_size=3, vad_filter=False,
        condition_on_previous_text=False)
    separator = '' if info.language in ('zh','yue','ja') else ' '
    text = separator.join(s.text.strip() for s in segments if s.no_speech_prob < .65 and s.avg_logprob > -1.2)
    return {'text': text[:2000], 'language': info.language, 'language_probability': float(info.language_probability)}


if __name__ == '__main__':
    import sys
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--transcribe-worker', type=Path)
    mode.add_argument('--transcribe-loop', action='store_true')
    args = parser.parse_args()
    if args.transcribe_loop:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if request is None:
                    _dialogue_model()
                    result = {'text': ''}
                else:
                    result = transcribe_worker(Path(request['path']), request['language'])
                print(json.dumps(result, ensure_ascii=True), flush=True)
            except (ValueError, RuntimeError, OSError):
                print('{"error":true}', flush=True)
    else:
        print(json.dumps(transcribe_worker(args.transcribe_worker), ensure_ascii=True))
