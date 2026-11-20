"""Actual filtered drum-Cue coverage, never a vocal-purity or groove judge."""
import copy
import math
from pathlib import Path
import tempfile
import wave

import numpy as np

from .automatic_analysis import _decode_stereo,RATE
from .continuous import validate_set
from .drum_coverage import inspect_drum_coverage
from .performance_audio import mix_performance


def review_filtered_drum_bridge(proposal,tracks,drum_id):
    """Render a bounded native drum cue with its actual filter/envelope/gain.

    A quiet stem cannot pass by boosting a listening copy. Source decoding must
    cover the full event; renderer padding cannot substitute for missing audio.
    """
    result=dict(status='unknown',eligible=False,drum_path_rendered=False,
        vocal_purity_confirmed=False,confirmed_groove=False,
        reason='滤波后鼓预滚证据不足，保留当前工程')
    try:
        p=validate_set(proposal['project'],tracks);track=tracks[drum_id]
        events=[e for e in p.get('performance',{}).get('events',[]) if e['track_id']==drum_id]
        if len(events)!=1 or track.get('stem_role')!='drums':return result
        event=copy.deepcopy(events[0]);length=event['end']-event['start']
        if event['repeats']!=1 or event['semitones'] or 'lowpass_hz' not in event or not .5<=length<=6.25:
            return result
        raw=_decode_stereo(track['path'],event['start'],length)
        if len(raw)<round(length*RATE)-2 or not np.isfinite(raw).all():
            return dict(result,reason='鼓预滚源音频截短，不能以补零结果代替完整证据')
        event['time']=0
        with tempfile.TemporaryDirectory(prefix='filtered-drum-review-') as tmp:
            folder=Path(tmp);silence=folder/'silence.wav';output=folder/'drums.wav'
            with wave.open(str(silence),'wb') as audio:
                audio.setparams((2,2,44100,0,'NONE',''));audio.writeframes(bytes(round(length*44100)*4))
            rendered=mix_performance(silence,dict(events=[event],drums=[]),tracks,output)
            peak=rendered.get('true_peak_dbtp')
            result.update(drum_path_rendered=True,true_peak_dbtp=peak,lowpass_hz=event['lowpass_hz'])
            if peak is None or not math.isfinite(peak) or peak>-.5:return result
            measured=inspect_drum_coverage(dict(path=str(output),duration=round(length*44100)/44100,stem_role='drums'),
                0,round(length*44100)/44100,maximum_gap_seconds=.75)
            result.update(status=measured['status'],eligible=measured['eligible'],rendered_coverage=measured,
                source_start=event['start'],source_end=event['end'],gain_db=event['gain_db'],
                reason='实际滤波后的鼓活动保护通过，分轨纯净度与听感待核' if measured['eligible'] else
                       '实际滤波后的鼓预滚有长空档或近静音，保留当前工程',
                limitations=['低通降低中高频，也可能削弱镲片、军鼓或改变鼓击边缘。',
                    '鼓活动、频谱或峰值不确认残留唱词已消除，也不评价鼓型兼容。'])
    except (KeyError,TypeError,ValueError,RuntimeError,OSError,OverflowError):
        pass
    return result
