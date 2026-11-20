"""Offline ASR evidence, timestamp filtering and exact-text Word Play candidates.

No model download occurs here. Whisper is a speech ASR model, so singing,
overlapping voices and accompaniment can yield unknown or incorrect results.
"""
import argparse
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
import wave

from .analysis import INPUT_OPTIONS
from .runtime import ROOT, run_ffmpeg

ENVIRONMENT = ROOT / 'reference-analysis/lyrics-venv'
MODEL_DIRECTORY = ROOT / 'models/lyrics/whisper-tiny'
MODEL_REPOSITORY = 'Systran/faster-whisper-tiny'
FASTER_WHISPER_VERSION = '1.2.1'
MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
WORD_CONFIDENCE = .75
WORDPLAY_CONFIDENCE = .85


def worker_python():
    return ENVIRONMENT / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _offline_environment():
    return {**os.environ, 'PYTHONUTF8': '1', 'HF_HUB_OFFLINE': '1',
            'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1',
            'HF_TOKEN': '', 'HUGGING_FACE_HUB_TOKEN': '',
            'OMP_NUM_THREADS': '4', 'MKL_NUM_THREADS': '4'}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _window(start, end):
    if not _finite(start) or not _finite(end) or start < 0 or not .01 <= end - start <= 1200:
        raise ValueError('歌词识别窗口须为有效的0.01秒至20分钟区域')
    return float(start), float(end)


def _empty(status, start, end, reason):
    return {'status': status, 'start': round(start, 6), 'end': round(end, 6),
            'segments': [], 'words': [], 'phrase_candidates': [], 'review_segments': [], 'review_words': [],
            'unresolved_ranges': [], 'reason': reason,
            'local_only': True, 'model': MODEL_REPOSITORY,
            'limitations': [
                '本地语音ASR用于唱歌时可能漏字、幻觉或错配；置信分数不是识别正确率。',
                '文本分段与停顿只提供歌词乐句候选，不确认音乐乐句、强拍或副歌。',
                '低置信文字与无效词级时间戳会被排除；没有可靠结果就保持未知。',
                '有文本但缺少词级时间戳或置信依据的有效分段范围仍属未决，不可当作无歌词空隙。',
            ]}


@lru_cache(maxsize=4)
def _probe(executable, executable_mtime, model_signature):
    result = subprocess.run([executable, '-m', 'deepmixing_audio.semantic_audio', '--worker', '--probe'],
        cwd=ROOT, capture_output=True, timeout=120, creationflags=NO_WINDOW, env=_offline_environment())
    if result.returncode:
        return False
    try:
        payload = json.loads(result.stdout.decode('utf-8').strip().splitlines()[-1])
        return payload.get('ready') is True
    except (ValueError, IndexError, AttributeError):
        return False


def lyrics_status():
    """Check actual local files and an offline model-load probe, without paths."""
    state = {'ready': False, 'reason': '本地歌词识别环境尚未安装', 'local_only': True,
             'automatic_download': False, 'model': MODEL_REPOSITORY,
             'runtime_version': FASTER_WHISPER_VERSION, 'device': 'cpu', 'compute_type': 'int8'}
    try:
        executable = worker_python()
        if not executable.is_file():
            return state
        if not all((MODEL_DIRECTORY / name).is_file() and (MODEL_DIRECTORY / name).stat().st_size > 0 for name in MODEL_FILES):
            return {**state, 'reason': '本地多语言歌词模型尚未完整安装'}
        signature = tuple((name, (MODEL_DIRECTORY / name).stat().st_size,
                           (MODEL_DIRECTORY / name).stat().st_mtime_ns) for name in MODEL_FILES)
        if not _probe(str(executable), executable.stat().st_mtime_ns, signature):
            return {**state, 'reason': '本地歌词模型或运行环境自检失败'}
        return {**state, 'ready': True, 'reason': '本地多语言ASR已就绪，唱歌结果仍须核对'}
    except Exception:
        return {**state, 'reason': '本地歌词识别依赖检查失败'}


def _text(value):
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if not unicodedata.category(c).startswith('C')).strip()[:500]


def _join_words(words):
    values = [word['text'] for word in words]
    separator = '' if all(any('\u3400' <= c <= '\u9fff' for c in text) for text in values) else ' '
    return separator.join(values)


def normalize_transcript(raw, start, end):
    """Filter actual worker-shaped ASR output; input timestamps are relative.

    This pure function does not recognize audio. Calling it on a fixture tests
    normalization only, and must not be presented as successful model inference.
    """
    start, end = _window(start, end)
    report = _empty('unknown', start, end, '没有达到置信阈值的有效词级识别结果')
    if not isinstance(raw, dict) or not isinstance(raw.get('segments'), list):
        return report
    length, previous_end = end - start, 0.
    for segment in raw['segments'][:1000]:
        if not isinstance(segment, dict):
            continue
        left, right = segment.get('start'), segment.get('end')
        logprob, no_speech = segment.get('avg_logprob'), segment.get('no_speech_prob')
        has_text=bool(_text(segment.get('text')) or segment.get('words'))
        if not _finite(left) or not _finite(right) or not 0 <= left < right <= length + 1e-6:
            # Malformed or crop-crossing text is uncertainty, not an empty
            # interval after a good word. Retain its in-window intersection
            # when locatable; otherwise protect the entire requested window.
            if has_text:
                lo,hi=0.,length
                if _finite(left) and _finite(right) and left<right:
                    a,b=max(0.,left),min(length,right)
                    if a<b:lo,hi=a,b
                report['unresolved_ranges'].append({'start':round(start+lo,6),'end':round(start+hi,6),
                                                   'reason':'invalid_segment_timestamps'})
            continue
        if (not _finite(logprob) or not _finite(no_speech) or not 0 <= no_speech <= .5 or logprob > 0):
            if has_text:
                report['unresolved_ranges'].append({'start':round(start+left,6),'end':round(min(end,start+right),6),
                                                   'reason':'rejected_segment'})
            continue
        segment_confidence = math.exp(max(-1000, logprob)) * (1 - no_speech)
        if segment_confidence < .65 or not isinstance(segment.get('words'), list):
            if segment_confidence >= .25 and isinstance(segment.get('words'), list):
                review=[]
                for word in segment['words'][:5000]:
                    if not isinstance(word,dict):continue
                    word_left,word_right=word.get('start'),word.get('end')
                    probability,text=word.get('probability'),_text(word.get('word'))
                    if (text and all(_finite(value) for value in (word_left,word_right,probability))
                            and 0<=word_left<word_right<=length+1e-6 and WORD_CONFIDENCE<=probability<=1):
                        review.append({'start':round(start+word_left,6),'end':round(start+word_right,6),
                                       'text':text,'confidence':round(float(probability),6)})
                if review:
                    report['review_words'].extend(review)
                report['review_segments'].append({'start':round(start+left,6),'end':round(start+right,6),
                    'text':_text(segment.get('text')),'confidence':round(segment_confidence,6),
                    'words':review,'review_required':True,'confirmed_phrase':False,
                    'safe_for_trim':False,'reason':'低置信或不完整唱词候选，仅供人工复核'})
            if has_text:
                report['unresolved_ranges'].append({'start':round(start+left,6),'end':round(min(end,start+right),6),
                                                   'reason':'rejected_segment'})
            continue
        accepted = []
        raw_words = segment['words'][:5000]
        for index, word in enumerate(raw_words):
            if not isinstance(word, dict):
                continue
            word_left, word_right = word.get('start'), word.get('end')
            probability, text = word.get('probability'), _text(word.get('word'))
            if not text or not all(_finite(value) for value in (word_left, word_right, probability)):
                continue
            if not WORD_CONFIDENCE <= probability <= 1 or not left <= word_left < word_right <= right + 1e-6:
                continue
            if word_left < previous_end - 1e-6:
                continue
            accepted.append((index, {'start': round(start + word_left, 6), 'end': round(start + word_right, 6),
                                    'text': text, 'confidence': round(float(probability), 6)}))
            previous_end = word_right
        complete=bool(raw_words) and len(accepted)==len(segment['words']) and len(segment['words'])<=5000
        if not complete and has_text:
            report['unresolved_ranges'].append({'start':round(start+left,6),'end':round(min(end,start+right),6),
                                               'reason':'filtered_or_truncated_words'})
        if not accepted:
            continue
        words = [word for _, word in accepted]
        confidence = min(segment_confidence, sum(word['confidence'] for word in words) / len(words))
        segment_report = {'start': words[0]['start'], 'end': words[-1]['end'], 'text': _join_words(words),
                          'confidence': round(confidence, 6), 'words_complete':complete,
                          'boundary_start_reliable': accepted[0][0] == 0,
                          'boundary_end_reliable': accepted[-1][0] == len(raw_words) - 1}
        report['segments'].append(segment_report)
        report['words'].extend(words)
        if complete and len(words) >= 2 and confidence >= .65 and segment_report['boundary_start_reliable'] and segment_report['boundary_end_reliable']:
            report['phrase_candidates'].append({'start': words[0]['start'], 'end': words[-1]['end'],
                'review_required':True,'confirmed_phrase':False,
                'reason': 'ASR文本分段及高置信词级时间戳候选，不代表已确认的音乐乐句'})
    if report['words']:
        report['status'], report['reason'] = 'estimated', '取得局部ASR文字与词级时间戳候选'
    language = raw.get('language')
    if isinstance(language, str) and re.fullmatch('[a-z]{2,3}', language):
        report['language'] = language
    return report


def analyze_lyrics(path, start, end):
    """Recognize a bounded local audio crop using the installed offline worker."""
    start, end = _window(start, end)
    state = lyrics_status()
    if not state.get('ready'):
        return _empty('unavailable', start, end, state.get('reason', '本地歌词识别依赖尚未就绪'))
    try:
        with tempfile.TemporaryDirectory(prefix='deepmixing-lyrics-') as temporary:
            cropped = Path(temporary) / 'region.wav'
            run_ffmpeg([*INPUT_OPTIONS, '-ss', f'{start:.9f}', '-i', Path(path), '-t', f'{end-start:.9f}',
                        '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-y', cropped], timeout=240)
            with wave.open(str(cropped),'rb') as audio:
                if ((audio.getnchannels(),audio.getframerate(),audio.getsampwidth())!=(1,16000,2) or
                        abs(audio.getnframes()-round((end-start)*16000))>2):
                    return _empty('unknown',start,end,'指定唱词区域解码截短或格式无效，没有完整覆盖证据')
            result = subprocess.run([str(worker_python()), '-m', 'deepmixing_audio.semantic_audio',
                '--worker', '--input', str(cropped)], cwd=ROOT, capture_output=True,
                timeout=min(1800, max(120, (end - start) * 20)), creationflags=NO_WINDOW, env=_offline_environment())
            if result.returncode or len(result.stdout) > 8_000_000:
                return _empty('unavailable', start, end, '本地歌词识别进程未完成有效输出')
            raw = json.loads(result.stdout.decode('utf-8').strip().splitlines()[-1])
            if not isinstance(raw, dict) or raw.get('status') == 'unavailable':
                return _empty('unavailable', start, end, '本地歌词识别失败，未生成文字证据')
            return normalize_transcript(raw, start, end)
    except Exception:
        return _empty('unavailable', start, end, '本地歌词识别失败或超时，未生成文字证据')


def _word_token(text):
    # Exact Unicode letters/numbers only: this does not invent phonetic rhymes.
    text = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(char for char in text if unicodedata.category(char)[0] in ('L', 'N'))


def _boundary_word(report, tail):
    if not isinstance(report, dict) or report.get('status') != 'estimated':
        return None
    words, segments = report.get('words'), report.get('segments')
    if not isinstance(words, list) or not words or not isinstance(segments, list) or not segments:
        return None
    word, segment = (words[-1], segments[-1]) if tail else (words[0], segments[0])
    if not isinstance(word, dict) or not isinstance(segment, dict):
        return None
    key = 'boundary_end_reliable' if tail else 'boundary_start_reliable'
    if segment.get(key) is not True or segment.get('words_complete') is False:
        return None
    left, right, confidence = word.get('start'), word.get('end'), word.get('confidence')
    if not all(_finite(value) for value in (left, right, confidence, report.get('start'), report.get('end'))):
        return None
    if not report['start'] <= left < right <= report['end'] or not WORDPLAY_CONFIDENCE <= confidence <= 1:
        return None
    unresolved=report.get('unresolved_ranges',[])
    if not isinstance(unresolved,list):return None
    for row in unresolved[:1000]:
        if not isinstance(row,dict):return None
        a,b=row.get('start'),row.get('end')
        if not _finite(a) or not _finite(b) or not report['start']<=a<b<=report['end']:return None
        if (tail and b>left) or (not tail and a<right):return None
    text = _text(word.get('text'))
    if not text:
        return None
    return {**word, 'token': _word_token(text)}


def find_wordplay(a, b):
    """Return an exact high-confidence end/start word match, or no candidates."""
    left, right = _boundary_word(a, True), _boundary_word(b, False)
    if not left or not right or not left['token'] or left['token'] != right['token']:
        return []
    return [{'text': left['token'], 'a_start': left['start'], 'a_end': left['end'],
             'b_start': right['start'], 'b_end': right['end'],
             'confidence': min(left['confidence'], right['confidence']),
             'reason': '高置信ASR词尾与词首文字相同，仅供Word Play试听核对'}]


def _worker(args):
    try:
        from importlib.metadata import version
        if version('faster-whisper') != FASTER_WHISPER_VERSION:
            return {'ready': False, 'status': 'unavailable', 'reason': '本地ASR运行环境版本不匹配'}
        from faster_whisper import WhisperModel
        model = WhisperModel(str(MODEL_DIRECTORY), device='cpu', compute_type='int8',
                             cpu_threads=4, num_workers=1, local_files_only=True)
        if args.probe:
            return {'ready': True, 'local_only': True}
        if not args.input:
            return {'status': 'unavailable', 'reason': '缺少本地识别输入'}
        # The parent already creates bounded 16kHz mono PCM. Passing samples
        # avoids PyAV's version-dependent file-open keyword API entirely.
        import numpy as np
        with wave.open(str(args.input),'rb') as source:
            if (source.getnchannels(),source.getframerate(),source.getsampwidth())!=(1,16000,2):
                raise ValueError('ASR worker requires canonical 16kHz mono PCM')
            frames=source.getnframes()
            if not 0<frames<=1200*16000:raise ValueError('ASR input is empty or too long')
            samples=np.frombuffer(source.readframes(frames),dtype='<i2').astype(np.float32)/32768
            if len(samples)!=frames:raise ValueError('ASR input is truncated')
        # Singing often has sustained vowels and accompaniment that confuse
        # VAD. Keep the extra output review-only; normalization still requires
        # the existing high-confidence evidence before any boundary action.
        segments, info = model.transcribe(samples, word_timestamps=True, beam_size=5,
            vad_filter=False, condition_on_previous_text=False)
        result = {'segments': [], 'language': info.language}
        for segment in segments:
            result['segments'].append({'start': float(segment.start), 'end': float(segment.end),
                'text': segment.text, 'avg_logprob': float(segment.avg_logprob),
                'no_speech_prob': float(segment.no_speech_prob),
                'words': [{'start': float(word.start), 'end': float(word.end), 'word': word.word,
                           'probability': float(word.probability)} for word in (segment.words or [])]})
        return result
    except Exception:
        return {'ready': False, 'status': 'unavailable', 'reason': '本地歌词模型或运行环境不可用'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--input')
    args = parser.parse_args()
    if not args.worker:
        parser.error('仅提供本地worker入口')
    os.environ.update({key: value for key, value in _offline_environment().items() if key.startswith(('HF_', 'HUGGING_', 'TRANSFORMERS_'))})
    try:
        encoded = json.dumps(_worker(args), ensure_ascii=True, allow_nan=False)
    except Exception:
        encoded = json.dumps({'ready': False, 'status': 'unavailable', 'reason': '本地识别未产生有效JSON结果'})
    print(encoded)


if __name__ == '__main__':
    main()
