"""Multi-track, chunked offline mixdown. Playback in GUI uses Web Audio."""
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
import numpy as np
import soundfile as sf

SR=44100

def ffmpeg_bin():
    return shutil.which('ffmpeg')


def duration_of(path):
    path=Path(path)
    try:
        return float(sf.info(str(path)).duration)
    except Exception:
        if not ffmpeg_bin():
            raise ValueError('音声の長さを読み取れません。FFmpegをPATHへ追加してください。')
        # ffprobe ships with the usual FFmpeg distribution.
        probe=shutil.which('ffprobe')
        if not probe: raise ValueError('ffprobeが必要です')
        p=subprocess.run([probe,'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True)
        if p.returncode or not p.stdout.strip():raise ValueError('音声ファイルが読み取れません: '+p.stderr[-300:])
        return float(p.stdout.strip())


def to_pcm(src,out):
    if ffmpeg_bin():
        cp=subprocess.run([ffmpeg_bin(),'-hide_banner','-loglevel','error','-y','-i',str(src),'-ac','2','-ar',str(SR),'-c:a','pcm_f32le',str(out)],capture_output=True,text=True)
        if cp.returncode:raise RuntimeError(f'FFmpeg decode error: {cp.stderr[-600:]}')
        return
    data,rate=sf.read(str(src),dtype='float32',always_2d=True)
    if data.shape[1]==1:data=np.repeat(data,2,axis=1)
    if data.shape[1]>2:data=data[:,:2]
    if rate!=SR:
        from scipy.signal import resample_poly
        gcd=math.gcd(rate,SR)
        data=resample_poly(data,SR//gcd,rate//gcd,axis=0).astype('float32')
    sf.write(str(out),data,SR,subtype='FLOAT')


def render_project(s,project_root,out_path,format_name='wav',scene_id=None):
    """Mix independent tracks by placing decoded segments into a disk-backed float32 buffer."""
    clips=[dict(c) for c in s['clips'] if scene_id is None or c.get('sceneId')==scene_id]
    # 'All scenes' means concatenate scenes, not overlay them at t=0.
    if scene_id is None:
        cursor=0.0
        for scene in s['scenes']:
            sc=[c for c in clips if c.get('sceneId')==scene['id']]
            for c in sc:c['start']=max(0,float(c.get('start',0)))+cursor
            cursor+=max([float(c.get('start',0))+float(c.get('duration',0)) for c in s['clips'] if c.get('sceneId')==scene['id']] or [0])+0.5
    if not clips:raise ValueError('書き出す音声クリップがありません')
    clips=[c for c in clips if float(c.get('duration',0))>0]
    if not clips:raise ValueError('音声の長さがありません')
    if len(clips)>4000:raise ValueError('クリップ数が多すぎます（最大4000）')
    end=max(float(c['start'])+float(c.get('duration',0)) for c in clips)
    if end>7200:raise ValueError('1回の書き出しは2時間までです')
    asset_map={a['id']:a for a in s['assets']}
    length=math.ceil(end*SR)
    out_path=Path(out_path)
    out_path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vds_mix_') as folder:
        tmp=Path(folder)
        pcm_cache={}
        mm=np.memmap(tmp/'mix.raw',mode='w+',dtype='float32',shape=(length,2))
        mm[:]=0
        for clip in clips:
            asset=asset_map.get(clip.get('assetId'))
            if not asset:continue
            aid=asset['id']
            if aid not in pcm_cache:
                f=tmp/(aid+'.wav')
                to_pcm(project_root/'assets'/asset['filename'],f)
                pcm_cache[aid]=f
            with sf.SoundFile(str(pcm_cache[aid])) as wav:
                src_length=len(wav)
                trim_start=max(0,int(float(clip.get('trimStart',0))*SR))
                trim_start=min(trim_start,src_length)
                trim_end=max(trim_start,src_length-int(float(clip.get('trimEnd',0))*SR))
                available=trim_end-trim_start
                if available<=0:continue
                start=max(0,int(float(clip.get('start',0))*SR))
                clip_samples=max(0,int(float(clip.get('duration',0))*SR))
                clip_samples=min(clip_samples,length-start)
                gain=max(0.0,min(8.0,float(clip.get('gain',1))))
                fade_in=max(0,int(float(clip.get('fadeIn',0))*SR))
                fade_out=max(0,int(float(clip.get('fadeOut',0))*SR))
                wrote=0
                while wrote<clip_samples:
                    if wrote>=available and not clip.get('loop'):break
                    offset=(wrote%available) if clip.get('loop') else wrote
                    count=min(131072,clip_samples-wrote,available-offset)
                    wav.seek(trim_start+offset)
                    audio=wav.read(count,dtype='float32',always_2d=True)
                    if len(audio)==0:break
                    n=len(audio)
                    audio*=gain
                    if fade_in or fade_out:
                        pos=np.arange(wrote,wrote+n)
                        if fade_in:audio*=np.minimum(1,np.maximum(0,pos/fade_in))[:,None]
                        if fade_out:audio*=np.minimum(1,np.maximum(0,(clip_samples-pos)/fade_out))[:,None]
                    mm[start+wrote:start+wrote+n]+=audio
                    wrote+=n
        peak=0.0
        for i in range(0,length,SR*10):
            chunk=np.asarray(mm[i:i+SR*10]);peak=max(peak,float(np.abs(chunk).max(initial=0)))
        scale=min(1.0,0.98/peak) if peak>0 else 1.0
        wav_out=tmp/'complete.wav' if format_name=='mp3' else out_path
        with sf.SoundFile(str(wav_out),mode='w',samplerate=SR,channels=2,format='WAV',subtype='PCM_16') as f:
            for i in range(0,length,SR*10):f.write(np.asarray(mm[i:i+SR*10])*scale)
        del mm
        if format_name=='mp3':
            if not ffmpeg_bin():raise RuntimeError('MP3書き出しにはFFmpegが必要です')
            cp=subprocess.run([ffmpeg_bin(),'-hide_banner','-loglevel','error','-y','-i',str(wav_out),'-codec:a','libmp3lame','-b:a','192k',str(out_path)],capture_output=True,text=True)
            if cp.returncode:raise RuntimeError(cp.stderr[-600:])
    return {"duration":round(end,2),"clips":len(clips),"peak":round(peak,3)}
