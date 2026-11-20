"""Full exported PCM sum-to-mono measurements, never a taste certificate."""
import math
import wave
import numpy as np


def audit_stereo(path):
    energy=mono=cross=side=0.;count=0;worst=None
    with wave.open(str(path)) as f:
        if (f.getnchannels(),f.getsampwidth())!=(2,2):
            raise ValueError('立体声检查需要双声道PCM16音频')
        rate=f.getframerate();expected=f.getnframes()
        while raw:=f.readframes(max(1,round(rate*.25))):
            x=np.frombuffer(raw,dtype='<i2').astype(np.float64).reshape(-1,2)/32768
            l,r=x[:,0],x[:,1];e=float(np.sum((l*l+r*r)/2));m=float(np.sum(((l+r)/2)**2))
            energy+=e;mono+=m;cross+=float(np.sum(l*r));side+=float(np.sum(((l-r)/2)**2));count+=len(x)
            if e/len(x)>1e-8:
                loss=10*math.log10(max(m/e,1e-15));worst=loss if worst is None else min(worst,loss)
    if count!=expected:raise ValueError('立体声检查解码不完整')
    loss=10*math.log10(max(mono/energy,1e-15)) if energy>0 else None
    return dict(status='silent' if energy==0 else 'measured',frames=count,
        mono_sum='(L+R)/2, same amplitude reference as per-channel stereo RMS',
        mono_energy_change_db=loss,worst_active_250ms_change_db=worst,
        side_energy_fraction=side/energy if energy else None,
        cancellation_review=bool(worst is not None and worst<-10),
        limitations='求和抵消诊断不识别人声，不证明手机听感或主观适配。')
