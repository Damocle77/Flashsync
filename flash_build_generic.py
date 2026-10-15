#!/usr/bin/env python3
"""Render PAL-corrected mono ITA with chosen silence inserts (preview only).
Uses independently reviewed CSV; does NOT infer missing dialogue or guarantee sync.
"""
import argparse,csv,json,shutil,subprocess,sys
from pathlib import Path
PAL=25025/24000

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='Flash_ITA_test.flac')
    p.add_argument('--events',default='flash_edits.csv',help='CSV avi_seconds,insert_seconds')
    p.add_argument('--output',default='Flash_ITA_MAPPED_PREVIEW.flac')
    p.add_argument('--delay',type=float,required=True,help='Initial delay seconds from stable early English-English matches')
    p.add_argument('--render',action='store_true',help='Actually render; otherwise list planned edits')
    a=p.parse_args()
    if a.delay<0:p.error('negative initial delay not supported: handle explicitly in editor')
    src=Path(a.input);dst=Path(a.output)
    if not src.is_file():p.error(f'missing {src}')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):p.error('ffmpeg and ffprobe required')
    probe=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=channels,sample_rate:format=duration','-of','json',str(src)],capture_output=True,text=True,check=True)
    data=json.loads(probe.stdout)
    ch=int(data['streams'][0]['channels']);rate=int(data['streams'][0]['sample_rate']);duration=float(data['format']['duration'])
    if ch!=1:p.error('this builder expects mono source; do not downmix without review')
    events=[]
    with open(a.events,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):events.append((float(r['avi_seconds']),float(r['insert_seconds'])))
    events.sort()
    if any(x<0 or d<=0 for x,d in events):p.error('event timestamps must be >=0 and inserts >0')
    if any(x*PAL>duration for x,d in events):p.error('event past end of corrected Italian audio')
    print('Input:',src,'duration',duration,'sample rate',rate)
    print(f'Initial delay: {a.delay:.3f}s')
    for t,d in events:print(f' AVI {t:.3f}s (converted {t*PAL:.3f}s): insert {d:.3f}s')
    print(f'Preview duration predicted: {duration+a.delay+sum(d for _,d in events):.3f}s')
    if not a.render:print('Dry-run only; add --render after reviewing edits.');return
    if dst.exists():p.error(f'output exists: {dst}; rename explicitly before re-rendering')
    bps=4
    decoder=subprocess.Popen(['ffmpeg','-nostdin','-v','error','-i',str(src),'-map','0:a:0','-ac','1','-ar',str(rate),'-f','s32le','-acodec','pcm_s32le','pipe:1'],stdout=subprocess.PIPE)
    encoder=subprocess.Popen(['ffmpeg','-nostdin','-v','error','-f','s32le','-ar',str(rate),'-ac','1','-i','pipe:0','-c:a','flac','-sample_fmt','s32',str(dst)],stdin=subprocess.PIPE)
    bytepos=0
    try:
        for stop,delay in [(0,a.delay)]+[(round(t*PAL*rate)*bps,d) for t,d in events]:
            remaining=stop-bytepos
            if remaining<0:raise ValueError('overlapping/out-of-order edits')
            while remaining:
                chunk=decoder.stdout.read(min(1048576,remaining))
                if not chunk:raise RuntimeError('unexpected end of audio')
                encoder.stdin.write(chunk);bytepos+=len(chunk);remaining-=len(chunk)
            silence=round(delay*rate)*bps
            while silence:
                n=min(1048576,silence);encoder.stdin.write(bytes(n));silence-=n
        while True:
            chunk=decoder.stdout.read(1048576)
            if not chunk:break
            encoder.stdin.write(chunk)
        encoder.stdin.close()
        if decoder.wait()!=0 or encoder.wait()!=0:raise RuntimeError('ffmpeg failed')
    except Exception:
        decoder.kill();encoder.kill();decoder.wait();encoder.wait();dst.unlink(missing_ok=True);raise
    print('Wrote',dst)
if __name__=='__main__':main()
