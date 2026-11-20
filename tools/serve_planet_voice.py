"""Start the isolated API with the user's accepted v2Pro decoder recipe.

api_v2 has no noise_scale request field. Bind the real decoder after every
weight load, exactly as in the accepted auditions, including API reloads.
"""
from functools import partial, wraps
from pathlib import Path
import os
import runpy
import sys


def install_decoder_recipe(tts_class):
    original = tts_class.init_vits_weights

    @wraps(original)
    def load(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if self.configs.version != 'v2Pro':
            raise RuntimeError('Planet voice requires the accepted v2Pro model')
        model = self.vits_model
        model.decode = partial(type(model).decode.__get__(model), noise_scale=0.0)
        print('Planet voice recipe: v2Pro decoder noise_scale=0.0', flush=True)
        return result

    tts_class.init_vits_weights = load


def install_korean_mecab():
    # g2pk2's Windows path assumes obsolete eunjeon wheels. The maintained
    # python-mecab-ko exposes the same pos() interface with a bundled dictionary.
    # Use it explicitly, preventing g2pk2 from attempting a global pip install.
    import g2pk2
    from mecab import MeCab

    def check(self):
        return None

    def get(self):
        return MeCab()

    g2pk2.G2p.check_mecab = check
    g2pk2.G2p.get_mecab = get


def main():
    source = Path(__file__).resolve().parents[1] / '.local/gpt-sovits/source'
    os.chdir(source)
    sys.path[:0] = [str(source / 'GPT_SoVITS'), str(source)]
    install_korean_mecab()
    from GPT_SoVITS.TTS_infer_pack.TTS import TTS
    install_decoder_recipe(TTS)
    runpy.run_path(str(source / 'api_v2.py'), run_name='__main__')


if __name__ == '__main__':
    main()
