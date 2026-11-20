"""Runs only inside the isolated, explicitly installed Demucs environment."""
import argparse
import json
import os
import random
from pathlib import Path
import wave
from types import SimpleNamespace


def export_model_stems(sources, source_names, output, all_stems=False, instrumental=None):
    """Export real model sources with one shared scale, without running a model.

    The default two-file path deliberately keeps prepare_stems' existing bytes.
    In four-source mode individual instruments can exceed the summed backing
    peak through cancellation, so the pair's shared scale may be reduced once
    for *all* five files. No stem is normalised or clipped independently.
    """
    import numpy as np
    from .stem_audio import prepare_stems
    if type(all_stems) is not bool:
        raise ValueError('all_stems须为布尔值')
    arrays=np.asarray(sources)
    if (not isinstance(source_names,(list,tuple)) or any(not isinstance(name,str) for name in source_names)
            or len(set(source_names))!=len(source_names) or 'vocals' not in source_names
            or arrays.ndim!=3 or arrays.shape[0]!=len(source_names) or arrays.shape[1]!=2
            or not arrays.shape[2] or arrays.dtype.kind not in 'fiu' or not np.isfinite(arrays).all()):
        raise ValueError('模型分轨须为命名一致、有限、等长双声道数据')
    if all_stems and set(source_names)!={'vocals','drums','bass','other'}:
        raise ValueError('四源导出须包含真实vocals、drums、bass、other模型源')
    index=source_names.index('vocals')
    raw_vocals=arrays[index]
    # Main supplies the exact existing Torch reduction, keeping default output
    # bytes independent of NumPy's potentially different summation order.
    raw_instrumental=(arrays[[i for i in range(len(source_names)) if i!=index]].sum(0)
                      if instrumental is None else np.asarray(instrumental))
    vocals,instrumental,scale=prepare_stems(raw_vocals,raw_instrumental)
    exports={'vocal':('vocals.wav',vocals),'instrumental':('instrumental.wav',instrumental)}
    if all_stems:
        peak=float(np.max(np.abs(arrays)))
        shared=min(scale,.98/max(peak,1e-12))
        if shared!=scale:
            vocals,instrumental=raw_vocals*shared,raw_instrumental*shared
            scale=shared
            exports={'vocal':('vocals.wav',vocals),'instrumental':('instrumental.wav',instrumental)}
        for role in ('drums','bass','other'):
            exports[role]=(role+'.wav',arrays[source_names.index(role)]*scale)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for filename,array in exports.values():
        with wave.open(str(output/filename),'wb') as stream:
            stream.setparams((2,2,44100,0,'NONE','not compressed'))
            stream.writeframes(np.round(array.T*32767).astype('<i2').tobytes())
    return {'scale':scale,'files':{role:item[0] for role,item in exports.items()}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--parent-pid',type=int)
    parser.add_argument('--all-stems',action='store_true')
    args=parser.parse_args()
    if not args.probe:
        from .worker_lifecycle import guard_parent
        guard_parent(args.parent_pid)
    import numpy as np
    import torch
    import torchaudio
    import demucs
    from demucs.apply import apply_model
    from demucs.states import load_model
    device='cuda' if torch.cuda.is_available() else 'cpu'
    if args.probe:
        print(json.dumps({'device':device,'torch':torch.__version__,'demucs':demucs.__version__}));return
    from .separation_runtime import MODEL_PATH,MODEL,PROCESSING_REVISION,INFERENCE_SHIFTS,INFERENCE_OVERLAP,INFERENCE_SEGMENT,model_digest
    from .stem_audio import normalization_stats
    from .service import save_json
    torch.set_num_threads(min(4,os.cpu_count() or 1));torch.manual_seed(0);random.seed(0)
    output=args.output
    def progress(stage,amount):
        save_json(output/'progress.json',{'stage':stage,'progress':amount})
    progress('loading',.03)
    digest=model_digest()
    # Only our fixed, hash-checked official checkpoint is accepted. No user
    # checkpoint path or arbitrary model URL is exposed by the HTTP API.
    package=torch.load(MODEL_PATH,map_location='cpu',weights_only=False)
    model=load_model(package).eval().to(device)
    with wave.open(str(args.input),'rb') as stream:
        if (stream.getnchannels(),stream.getframerate(),stream.getsampwidth())!=(2,44100,2):
            raise ValueError('模型输入须为44.1kHz双声道PCM')
        samples=np.frombuffer(stream.readframes(stream.getnframes()),dtype='<i2').astype(np.float32).reshape(-1,2).T.copy()/32768
    audio=torch.from_numpy(samples)
    mean,std=normalization_stats(samples)
    normalized=(audio-mean)/std
    progress('separating',.12)
    # Demucs' chunk progress adapter writes actual completed-chunk counts.
    import demucs.apply as apply_module
    from tqdm import tqdm as original_tqdm
    pass_index=0
    def tracked_tqdm(iterable,**kwargs):
        nonlocal pass_index
        current_pass=pass_index;pass_index+=1
        iterator=original_tqdm(iterable,**kwargs)
        total=len(iterable)
        for index,item in enumerate(iterator):
            yield item
            progress('separating',.12+.75*(current_pass+(index+1)/max(total,1))/max(1,INFERENCE_SHIFTS))
    apply_module.tqdm=SimpleNamespace(tqdm=tracked_tqdm)
    with torch.inference_mode():
        sources=apply_model(model,normalized[None],device=device,shifts=INFERENCE_SHIFTS,split=True,
            overlap=INFERENCE_OVERLAP,progress=True,num_workers=0,segment=INFERENCE_SEGMENT)[0].cpu()
    sources=sources*std+mean/len(model.sources)
    progress('writing',.9)
    vocal_index=model.sources.index('vocals')
    backing=sources[[i for i in range(len(model.sources)) if i!=vocal_index]].sum(0).numpy()
    exported=export_model_stems(sources.numpy(),model.sources,output,all_stems=args.all_stems,instrumental=backing)
    result={'model':MODEL,'model_sha256':digest,'scale':exported['scale'],'device':device,
        'processing_revision':PROCESSING_REVISION,'shifts':INFERENCE_SHIFTS,'seed':0,
        'segment_seconds':INFERENCE_SEGMENT,'overlap':INFERENCE_OVERLAP,'torch':torch.__version__}
    if args.all_stems:
        result.update(all_stems=True,stem_roles=list(exported['files']))
    save_json(output/'result.json',result)
    progress('checking',.96)


if __name__=='__main__':
    main()
