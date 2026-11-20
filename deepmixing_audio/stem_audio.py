"""Signal checks describe alignment/reconstruction, never subjective purity."""
from contextlib import ExitStack
import math
import wave
import numpy as np


def normalization_stats(audio):
    if audio.ndim!=2 or audio.shape[0]!=2 or audio.shape[1]<2 or not np.isfinite(audio).all():
        raise ValueError('模型输入须为有效双声道音频')
    mean=float(np.mean(audio,dtype=np.float64))
    std=float(np.std(audio.mean(axis=0),ddof=1,dtype=np.float64))
    if std<1e-7:
        # Opposite-polarity channels can be audible yet cancel in a mono sum.
        std=float(np.std(audio,ddof=1,dtype=np.float64))
    if not math.isfinite(std) or std<1e-7:
        raise ValueError('素材接近静音，无法进行有意义的分离')
    return mean,std


def prepare_stems(vocal, instrumental):
    if vocal.shape!=instrumental.shape or vocal.ndim!=2 or vocal.shape[0]!=2 or not vocal.shape[1]:
        raise ValueError('分轨结果须为等长双声道')
    if not np.isfinite(vocal).all() or not np.isfinite(instrumental).all():
        raise ValueError('模型返回了非有限音频数值')
    peak=max(float(np.max(abs(vocal))),float(np.max(abs(instrumental))))
    scale=min(1.,.98/max(peak,1e-12))
    return vocal*scale,instrumental*scale,scale


def validate_stems(source,vocals,instrumental,scale=1):
    if not isinstance(scale,(int,float)) or not math.isfinite(scale) or not 0<scale<=1:
        raise ValueError('无效的分轨共同缩放系数')
    try:
        with ExitStack() as stack:
            streams=[stack.enter_context(wave.open(str(path),'rb')) for path in (source,vocals,instrumental)]
            frames=streams[0].getnframes()
            if not 22050<=frames<=1200*44100:
                raise ValueError('分轨时长超出范围')
            for stream in streams:
                if (stream.getnchannels(),stream.getframerate(),stream.getsampwidth(),stream.getnframes())!=(2,44100,2,frames):
                    raise ValueError('分轨长度、声道或采样格式与原曲不匹配')
            signal=error=0.;peaks=[0.,0.];energies=[0.,0.];count=0
            while True:
                chunks=[stream.readframes(65536) for stream in streams]
                if not chunks[0]:
                    break
                if any(len(chunk)!=len(chunks[0]) for chunk in chunks):
                    raise ValueError('分轨文件存在截断数据')
                x,v,b=[np.frombuffer(chunk,dtype='<i2').astype(np.float64)/32768 for chunk in chunks]
                residual=x*scale-v-b
                signal+=float(np.dot(x*scale,x*scale));error+=float(np.dot(residual,residual));count+=len(x)
                for i,stem in enumerate((v,b)):
                    peaks[i]=max(peaks[i],float(np.max(abs(stem))));energies[i]+=float(np.dot(stem,stem))
            if count!=frames*2:
                raise ValueError('分轨文件的实际采样数不完整')
    except (wave.Error,EOFError,OSError) as exc:
        raise ValueError('无法读取完整分轨文件') from exc
    warnings=['人声残留、漏音、混响和金属感须对比试听，机器检查不能判断纯净度。']
    if signal<1e-10 or any(value<1e-10 for value in energies):
        warnings.append('原曲或某个分轨接近静音，请检查素材是否适合分离。')
    residual_db=10*math.log10(max(error,1e-20)/max(signal,1e-20))
    if residual_db>-12:
        warnings.append('分轨叠加与原曲差异较大，请重点复核是否遗漏声音。')
    return {'status':'listening_required','frames':frames,'sample_rate':44100,'channels':2,
            'reconstruction_error_db':round(residual_db,3),'shared_scale':scale,
            'sample_peaks_dbfs':{role:round(20*math.log10(max(value,1e-12)),3) for role,value in zip(('vocal','instrumental'),peaks)},
            'warnings':warnings}
