"""Run in *the TTS environment*, separate from the studio's Python environment.
Contract: python tts_worker.py <job.json> <result.json>
Each job uses a single model/engine and contains multiple utterances.
Based on the request/persistent runtime pattern in voice_pipeline_v28.py.
"""
import importlib.util
import inspect
import json
import sys
import traceback
from pathlib import Path


def install_tokenizer_compat(tokenizer):
    """Fallback from voice_pipeline_v28: batch tokenizers of mixed versions."""
    if tokenizer is None or not hasattr(tokenizer,'batch_encode'):
        return
    old=tokenizer.batch_encode
    def patched(texts,max_length=None):
        try:
            return old(texts,max_length=max_length)
        except TypeError as exc:
            if 'TextEncodeInput must be' not in str(exc):raise
            import torch
            ids=[]
            for text in texts:
                item=tokenizer.encode(str(text))
                ids.append(torch.as_tensor(item,dtype=torch.long).flatten())
            length=max_length or max(len(x) for x in ids)
            pad=int(getattr(tokenizer,'pad_token_id',0) or 0)
            batch=torch.full((len(ids),length),pad,dtype=torch.long)
            mask=torch.zeros((len(ids),length),dtype=torch.bool)
            for index,item in enumerate(ids):
                n=min(length,len(item));batch[index,:n]=item[:n];mask[index,:n]=True
            return batch,mask
    tokenizer.batch_encode=patched


def irodori_run(job):
    root=Path(job['irodori_dir']).expanduser().resolve()
    if not (root/'infer.py').is_file():raise RuntimeError(f'Irodori infer.py not found: {root}')
    sys.path.insert(0,str(root))
    from irodori_tts import inference_runtime as ir
    hf_model=job['model']
    precision='bf16' if any('/'+q in hf_model.lower() for q in ('int8','int4','float8')) else 'fp32'
    checkpoint=ir.download_hf_checkpoint(hf_model)
    device='cuda'
    key_cls=ir.RuntimeKey
    kwargs={"checkpoint":str(checkpoint),"model_device":device,
        "codec_repo":"Aratako/Semantic-DACVAE-Japanese-32dim",
        "model_precision":precision,"codec_device":device,"codec_precision":"fp32",
        "codec_deterministic_encode":True,"codec_deterministic_decode":True,
        "compile_model":False,"compile_dynamic":False}
    sig=inspect.signature(key_cls)
    key=key_cls(**{k:v for k,v in kwargs.items() if k in sig.parameters})
    runtime=ir.InferenceRuntime.from_key(key)
    install_tokenizer_compat(getattr(runtime,'tokenizer',None))
    install_tokenizer_compat(getattr(runtime,'caption_tokenizer',None))
    spec=importlib.util.spec_from_file_location('vds_irodori_infer',root/'infer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class CachedRuntime:
        @classmethod
        def from_key(cls,_key):return runtime
    module.InferenceRuntime=CachedRuntime
    results=[]
    original=list(sys.argv)
    for line in job['items']:
        try:
            output=Path(line['output']).resolve();output.parent.mkdir(parents=True,exist_ok=True)
            steps=int(line.get('num_steps',40))
            args=[str(root/'infer.py'),'--checkpoint',str(checkpoint),'--text',line['text'],
                '--output-wav',str(output),'--num-steps',str(steps),
                '--t-schedule-mode',line.get('schedule','linear'),
                '--sway-coeff',str(line.get('sway_coeff',-1.0)),
                '--model-device',device,'--codec-device',device,
                '--model-precision',precision]
            if line.get('caption'):args+=['--caption',line['caption']]
            refs=line.get('refs') or []
            if len(refs)>1:args+=['--ref-wavs',*refs]
            elif len(refs)==1:args+=['--ref-wav',refs[0]]
            else:args+=['--no-ref']
            sys.argv=args
            try:module.main()
            except SystemExit as exc:
                if exc.code not in (0,None):raise RuntimeError(f'infer.py exited {exc.code}')
            if not output.exists():raise RuntimeError('No WAV was generated')
            results.append({'id':line['id'],'ok':True,'output':str(output)})
        except Exception as exc:
            results.append({'id':line['id'],'ok':False,'error':str(exc)})
        finally:sys.argv=original
    return results


def qwen_run(job):
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    model_name=job['model']
    dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32
    model=Qwen3TTSModel.from_pretrained(model_name,device_map='cuda:0' if torch.cuda.is_available() else 'cpu',dtype=dtype,attn_implementation='sdpa')
    results=[]
    for item in job['items']:
        try:
            mode=item.get('mode','clone')
            if mode=='design':
                if 'VoiceDesign' not in model_name:
                    raise ValueError('Qwen Voice Design requires a VoiceDesign model')
                waves,sr=model.generate_voice_design(text=item['text'],instruct=item.get('caption') or item.get('description') or '自然な話し方',language=item.get('language','Japanese'))
            else:
                refs=item.get('refs') or []
                if not refs:raise ValueError('Qwen Base voice cloning requires reference WAV. Use Voice Design to create one first.')
                ref_text=item.get('ref_text','')
                kw={'text':item['text'],'language':item.get('language','Japanese'),
                    'ref_audio':refs[0],'x_vector_only_mode':not bool(ref_text)}
                if ref_text:kw['ref_text']=ref_text
                waves,sr=model.generate_voice_clone(**kw)
            output=Path(item['output']);output.parent.mkdir(parents=True,exist_ok=True)
            sf.write(str(output),waves[0],sr)
            results.append({'id':item['id'],'ok':True,'output':str(output)})
        except Exception as exc:results.append({'id':item['id'],'ok':False,'error':str(exc)})
    return results


def main():
    job_path=Path(sys.argv[1]); result_path=Path(sys.argv[2]);job=json.loads(job_path.read_text(encoding='utf-8'))
    try:
        results=irodori_run(job) if job['engine']=='irodori' else qwen_run(job)
        response={'ok':True,'items':results}
    except Exception as exc:
        response={'ok':False,'error':f'{type(exc).__name__}: {exc}', 'traceback':traceback.format_exc()[-3500:]}
    result_path.write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding='utf-8')
    if not response['ok']:sys.exit(1)

if __name__=='__main__':main()
