"""Bounded decoding and pulse estimates; no lyric, key or source-role inference."""
import math
from pathlib import Path
import wave

from .runtime import run_ffmpeg
import numpy as np

INPUT_OPTIONS=['-protocol_whitelist','file,pipe','-format_whitelist','wav,mp3,mov,flac,ogg,aac,aiff,matroska,webm']


def canonicalize(source, target):
    run_ffmpeg([*INPUT_OPTIONS,'-i',source,'-map','0:a:0','-vn','-t','1200.05',
                '-ac','2','-ar','44100','-c:a','pcm_s16le','-y',target])
    with wave.open(str(target)) as audio:
        duration=audio.getnframes()/audio.getframerate()
    if not .5<=duration<=1200:
        raise ValueError('素材时长须在0.5秒至20分钟之间')
    return duration


def analyze(path):
    with wave.open(str(path)) as audio:
        duration=audio.getnframes()/audio.getframerate()
    result=run_ffmpeg([*INPUT_OPTIONS,'-i',path,'-t','45','-ac','2','-ar','11025',
                       '-f','f32le','-acodec','pcm_f32le','pipe:1'])
    stereo=np.frombuffer(result.stdout,dtype='<f4').reshape(-1,2)
    if not len(stereo):
        raise ValueError('素材没有可解码音频')
    # A mono remix can boost correlated channels or cancel opposite polarity.
    # Measure channel peaks directly and follow the strongest channel's pulse.
    peak=float(np.max(abs(stereo)))
    samples=stereo[:,int(np.argmax(np.mean(stereo.astype(float)**2,axis=0)))].copy()
    report={'duration':round(duration,6),'analyzed_seconds':round(len(samples)/11025,3),
        'sample_peak_dbfs_excerpt':round(20*math.log10(max(peak,1e-12)),2),
        'bpm_estimate':None,'beat_offset_estimate':None,
        'grid_status':'unknown','limitations':['仅前45秒脉冲估计；可能有半拍/倍拍及变速误差。',
            '没有识别强拍、乐句、调性、歌词或人声/伴奏角色；请试听确认。']}
    if peak<1e-5 or len(samples)<11025*4:
        return report
    hop=128
    frames=np.lib.stride_tricks.sliding_window_view(samples,1024)[::hop]
    spectrum=np.abs(np.fft.rfft(frames*np.hanning(1024),axis=1))
    flux=np.maximum(np.diff(spectrum,axis=0),0).sum(axis=1)
    if not len(flux) or flux.max()<1e-5 or np.std(flux)<np.mean(flux)*.15:
        return report
    # Gate relative onset energy BEFORE normalizing it. Tiny FFT leakage in
    # steady notes otherwise becomes an apparently strong periodic pulse.
    relative_onset=float(np.percentile(flux,95)/max(float(spectrum.sum(axis=1).mean()),1e-9))
    if relative_onset<.01:
        return report
    onset=np.maximum(flux-np.median(flux),0)
    if onset.max()<=1e-8:
        return report
    onset/=onset.max()
    size=1<<(2*len(onset)-1).bit_length()
    transformed=np.fft.rfft(onset,n=size)
    ac=np.fft.irfft(transformed*np.conj(transformed),n=size)[:len(onset)]
    frame_rate=11025/hop
    low=round(frame_rate*60/180)
    high=min(round(frame_rate*60/65),len(ac)-2)
    if high<=low or ac[0]<=1e-9:
        return report
    lag=low+int(np.argmax(ac[low:high+1]))
    support=float(ac[lag]/ac[0])
    if support<.2:
        return report
    denom=ac[lag-1]-2*ac[lag]+ac[lag+1]
    refined=lag+float(.5*(ac[lag-1]-ac[lag+1])/denom) if abs(denom)>1e-9 else float(lag)
    bpm=60*frame_rate/refined
    period=round(refined)
    phase=max(range(period),key=lambda phase:sum(onset[phase::period]))
    report.update(bpm_estimate=round(float(bpm),2),
                  beat_offset_estimate=round(float((phase*hop+512)/11025 % (60/bpm)),4),
                  grid_status='estimated_review_required',periodicity_support=round(support,3))
    return report
