"""Optional, loopback-only GPT-SoVITS api_v2 speech provider.

Configuration is an operator-owned local file, never accepted from chat.
Model loading/training stays in an isolated process and environment.
"""
import hashlib
import json
import re
from pathlib import Path
import time
from urllib.request import Request, ProxyHandler, build_opener, HTTPRedirectHandler

from .runtime import ROOT

CONFIG = ROOT / '.local/planet-voice.json'
LANGUAGES = {'zh': 'zh', 'en': 'en', 'yue': 'yue', 'ja': 'ja', 'ko': 'ko'}
MAX_AUDIO = 12 * 1024 * 1024
# Version both the synthesis recipe and the final conversation pacing.
RECIPE_REVISION = 'chenhao-v2pro-noise0-temp065-gap012-tempo108-mp3-32k160-v3'


# Keep the displayed punctuation; only soften brief Mandarin acknowledgments in TTS.
# cut0 still breaks on newlines, even immediately after a comma.
def conversational_text(text, language):
    text = re.sub(r'([,，])[ \t]*[\r\n]+[ \t\r\n]*',
                  lambda match: ', ' if match[1] == ',' else '，', text)
    if language == 'zh':
        text = re.sub(r'^(好的|好啊|好呀|行啊|可以啊|可以呀|明白了)[ \t]*[,，][ \t]*(?=[\u3400-\u9fff])', r'\1', text)
    return text


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('本机语音服务返回了重定向，请检查服务配置')


class GptSovitsVoice:
    def __init__(self, config_path=CONFIG):
        self.config_path = Path(config_path)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self._health = (0, None, False)

    def configuration(self):
        if not self.config_path.is_file():
            return None
        try:
            body = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            if not isinstance(body, dict):
                raise ValueError()
            if body.get('enabled') is not True:
                return None
            port = body.get('port', 9880)
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError()
            reference = Path(body['reference_audio']).resolve(strict=True)
            if not reference.is_relative_to((ROOT / '.local/gpt-sovits').resolve()) or not reference.is_file():
                raise ValueError()
            if reference.suffix.lower() not in ('.wav', '.flac') or reference.stat().st_size > MAX_AUDIO:
                raise ValueError()
            prompt = body['prompt_text']
            revision = body['model_revision']
            language = body.get('prompt_language', 'zh')
            if not isinstance(prompt, str) or not 1 <= len(prompt) <= 500:
                raise ValueError()
            if language not in LANGUAGES or not isinstance(revision, str) or not 1 <= len(revision) <= 200:
                raise ValueError()
            label = body.get('voice_label', '零氪 · 定制多语言音色')
            if not isinstance(label, str) or not 1 <= len(label) <= 100:
                raise ValueError()
            return dict(port=port, reference_audio=str(reference), prompt_text=prompt,
                        prompt_language=LANGUAGES[language], model_revision=revision, voice_label=label)
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError('GPT-SoVITS 本机配置不完整；需有效的参考录音、台词和权重版本') from None

    def ready(self, config):
        now = time.monotonic()
        if self._health[1] == config['port'] and now - self._health[0] < 3:
            return self._health[2]
        ready = False
        try:
            with self.opener.open(f"http://127.0.0.1:{config['port']}/openapi.json", timeout=1) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) <= 2 * 1024 * 1024:
                    schema = json.loads(raw)
                    ready = 'post' in schema.get('paths', {}).get('/tts', {})
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._health = (now, config['port'], ready)
        return ready

    def cache_key(self, config, text, language):
        reference_digest = hashlib.sha256(Path(config['reference_audio']).read_bytes()).hexdigest()
        payload = json.dumps([RECIPE_REVISION, config, reference_digest, language, text], sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def synthesize(self, config, text, language, destination):
        if language not in LANGUAGES:
            raise ValueError('GPT-SoVITS 当前不支持该语言播报，文字回复已保留')
        payload = dict(text=conversational_text(text, language), text_lang=LANGUAGES[language],
                       ref_audio_path=config['reference_audio'], prompt_text=config['prompt_text'],
                       prompt_lang=config['prompt_language'], media_type='wav', streaming_mode=False,
                       text_split_method='cut0', fragment_interval=0.12, batch_size=1, parallel_infer=False,
                       use_cuda_graph=False, seed=1234, top_k=5, temperature=0.65, speed_factor=1.0)
        request = Request(f"http://127.0.0.1:{config['port']}/tts",
                          data=json.dumps(payload, ensure_ascii=True).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self.opener.open(request, timeout=120) as response:
                raw = response.read(MAX_AUDIO + 1)
            if len(raw) > MAX_AUDIO or len(raw) < 44 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
                raise ValueError('本机语音服务未返回有效 WAV，文字回复已保留')
            Path(destination).write_bytes(raw)
        except OSError:
            raise ValueError('GPT-SoVITS 本机合成失败或超时，文字回复已保留') from None
