#!/usr/bin/env python3
"""FlashSync local refinement: candidates only, no edits/renders.
Reads flash_refinement_samples.csv plus complete ENG_AVI.wav / ENG_BD.wav.
"""
import argparse, csv, math
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate

FACTOR = 25025 / 24000

def load_samples(path):
    groups = defaultdict(list)
    with path.open(newline='',encoding='utf-8-sig') as f:
        r = csv.DictReader(f)
        required = {'region','avi_seconds','offset_seconds','ncc'}
        if not required.issubset(r.fieldnames or []):
            raise ValueError('CSV dei campioni privo delle colonne richieste')
        for row in r:
            region = int(row['region']); t=float(row['avi_seconds']); off=float(row['offset_seconds']); ncc=float(row['ncc'])
            if all(map(math.isfinite,(t,off,ncc))) and 0 <= ncc <= 1.0001:
                groups[region].append((t,off,ncc))
    return {k:sorted(v) for k,v in groups.items()}

def plateau_segments(samples, min_ncc, tolerance, min_stable):
    # Consecutive good samples grouped into stable offset runs.
    runs = []
    for t,o,n in samples:
        if n < min_ncc:
            continue
        if runs and abs(o - np.median([x[1] for x in runs[-1][-7:]])) <= tolerance:
            runs[-1].append((t,o,n))
        else:
            runs.append([(t,o,n)])
    # Merge adjacent coherent short runs, preserving chronology.
    merged = []
    for run in runs:
        if merged and abs(np.median([x[1] for x in merged[-1]]) - np.median([x[1] for x in run])) <= tolerance:
            merged[-1].extend(run)
        else:
            merged.append(run)
    return [r for r in merged if len(r) >= min_stable]

def local_score(avi,bd,sr,t,offset,win=1.0,radius=0.45):
    n = round(win*sr)
    positions=t*sr+np.arange(n,dtype=np.float64)/FACTOR
    lo=max(0,int(np.floor(positions[0]))-2); hi=min(len(avi),int(np.ceil(positions[-1]))+3)
    if hi-lo<3:return np.nan,np.nan
    x=np.interp(positions,np.arange(lo,hi),avi[lo:hi]).astype(np.float64)
    x-=np.mean(x); ex=float(np.dot(x,x))
    if ex<1e-5:return np.nan,np.nan
    pred=t*FACTOR+offset
    begin=max(0,int((pred-radius)*sr)); end=min(len(bd),int((pred+win+radius)*sr))
    y=np.asarray(bd[begin:end],dtype=np.float64)
    if len(y)<n:return np.nan,np.nan
    corr=correlate(y,x,mode='valid',method='fft')
    # Derive local energy from windows around a local reference mean: avoids
    # catastrophic cancellation for near-constant (quiet) audio windows.
    yy=y-float(np.mean(y))
    c=np.r_[0.0,np.cumsum(yy,dtype=np.float64)]
    c2=np.r_[0.0,np.cumsum(yy*yy,dtype=np.float64)]
    ss=c[n:]-c[:-n]; sq=c2[n:]-c2[:-n]
    ey=sq-ss*ss/n
    # Reject near-silent windows rather than artificially clipping invalid NCC.
    floor=max(1e-7, n*25.0)
    valid=ey>floor
    if not np.any(valid):return np.nan,np.nan
    scores=np.full(len(corr),-np.inf,dtype=np.float64)
    scores[valid]=corr[valid]/np.sqrt(ey[valid]*ex)
    valid &= np.isfinite(scores) & (np.abs(scores)<=1.0001)
    if not np.any(valid):return np.nan,np.nan
    scores[~valid]=-np.inf
    idx=int(np.argmax(scores))
    return float(scores[idx]), (begin+idx)/sr-t*FACTOR

def run(args):
    w=Path(args.work).resolve()
    samples=load_samples(w/'flash_refinement_samples.csv')
    sra,avi=wavfile.read(w/'ENG_AVI.wav'); srb,bd=wavfile.read(w/'ENG_BD.wav')
    if sra!=srb or avi.ndim!=1 or bd.ndim!=1:raise ValueError('Servono WAV mono della stessa frequenza')
    candidates=[]; details=[]
    for region,rr in sorted(samples.items()):
        runs=plateau_segments(rr,args.min_ncc,args.tolerance,args.min_stable)
        print(f'Regione {region}: {len(runs)} plateau stabili')
        if len(runs)<2:
            candidates.append(dict(region=region,event=f'{region}:?',before_s='',after_s='',offset_pre='',offset_post='',jump_s='',status='UNRESOLVED_PLATEAUS'))
            continue
        for index,(left,right) in enumerate(zip(runs,runs[1:]),1):
            o1=float(np.median([x[1] for x in left]));o2=float(np.median([x[1] for x in right]));jump=o2-o1
            if jump < args.min_jump:
                continue
            low=left[-1][0]; high=right[0][0]
            if not low < high or high-low > args.max_bracket:
                candidates.append(dict(region=region,event=f'{region}:{index}',before_s=low,after_s=high,offset_pre=round(o1,4),offset_post=round(o2,4),jump_s=round(jump,4),status='UNRESOLVED_WIDE_BRACKET'))
                continue
            # For small jumps do not let PRE/POST search bands overlap.
            # Retain a minimum practical radius; ambiguous short jumps remain unresolved.
            effective_radius = min(args.radius, max(0.03, abs(jump) * 0.35))
            last_pre=None; first_post=None; reversed_seen=False; had_pre=False; had_post=False
            count=round((high-low+2*args.context)/args.step)
            for k in range(count+1):
                t=round(max(0,low-args.context)+k*args.step,5)
                if t>high+args.context+1e-5:break
                pre,m1=local_score(avi,bd,sra,t,o1,args.window,effective_radius)
                post,m2=local_score(avi,bd,sra,t,o2,args.window,effective_radius)
                state='UNCERTAIN'
                if np.isfinite(pre) and np.isfinite(post) and max(pre,post)>=args.min_local_ncc and abs(pre-post)>=args.margin:
                    state='BEFORE' if pre>post else 'AFTER'
                details.append(dict(region=region,event=f'{region}:{index}',avi_s=t,ncc_pre=pre,ncc_post=post,offset_pre_measured=m1,offset_post_measured=m2,state=state))
                if state=='BEFORE':
                    had_pre=True
                    if had_post:reversed_seen=True
                    last_pre=t
                elif state=='AFTER':
                    had_post=True
                    if first_post is None:first_post=t
            ok=had_pre and had_post and last_pre<first_post and not reversed_seen
            # When local NCC fails, the two stable, ordered plateaus still
            # supply a valid *coarse* bracket. Never represent it as an exact cut.
            if ok:
                before_s, after_s, status = last_pre, first_post, 'REVIEW_REQUIRED'
            else:
                before_s, after_s, status = low, high, 'REVIEW_REQUIRED_COARSE'
            candidates.append(dict(region=region,event=f'{region}:{index}',before_s=before_s,after_s=after_s,offset_pre=round(o1,4),offset_post=round(o2,4),jump_s=round(jump,4),status=status))
    if not candidates:raise RuntimeError('Nessun evento: controllare campioni/soglie, non inferire una sincronizzazione perfetta')
    for filename,rows in [('flash_edits_candidates.csv',candidates),('flash_refine_local.csv',details)]:
        if not rows:continue
        output=w/filename
        if output.exists() and not args.force:
            raise RuntimeError(f'{output} esiste gia: usa --force per rigenerare SOLO i candidati')
        with output.open('w',newline='',encoding='utf-8') as f:
            wr=csv.DictWriter(f,fieldnames=list(rows[0]));wr.writeheader();wr.writerows(rows)
        print('Salvato:',output)
    for r in candidates:print(r['event'],r['before_s'],r['after_s'],r['jump_s'],r['status'])
    print('Le finestre COARSE non sono punti di taglio approvati.')
    print('Nessuna modifica a flash_edits.csv, audio o video.')

def main():
    p=argparse.ArgumentParser(description='Raffinamento locale automatico, candidati NON approvati')
    p.add_argument('--work',required=True)
    p.add_argument('--min-ncc',type=float,default=0.85)
    p.add_argument('--tolerance',type=float,default=0.20)
    p.add_argument('--min-stable',type=int,default=3)
    p.add_argument('--min-jump',type=float,default=0.35)
    p.add_argument('--max-bracket',type=float,default=30)
    p.add_argument('--step',type=float,default=0.10)
    p.add_argument('--context',type=float,default=1.0)
    p.add_argument('--window',type=float,default=1.0)
    p.add_argument('--radius',type=float,default=0.45)
    p.add_argument('--min-local-ncc',type=float,default=0.60)
    p.add_argument('--margin',type=float,default=0.15)
    p.add_argument('--force',action='store_true')
    run(p.parse_args())
if __name__=='__main__':main()
