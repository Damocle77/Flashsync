#!/usr/bin/env python3
"""Find and refine offset jumps using the coarse alignment CSV. No audio edits."""
import argparse, csv
import numpy as np
from flash_audio_align import envelope, best_match, PAL_TO_BD, stamp


def median(v): return float(np.median(v))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--avi', default='Flash_ENG_AVI.wav')
    p.add_argument('--bd', default='Flash_ENG_BD.wav')
    p.add_argument('--coarse', default='flash_alignment.csv')
    p.add_argument('--out', default='flash_jumps_refined.csv')
    p.add_argument('--samples', default='flash_refinement_samples.csv')
    p.add_argument('--threshold', type=float, default=0.75)
    p.add_argument('--step', type=float, default=1.0)
    p.add_argument('--window', type=float, default=6.0)
    p.add_argument('--min-jump', type=float, default=0.35)
    a=p.parse_args()
    with open(a.coarse,encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
    valid=[r for r in rows if r['status']=='OK']
    if len(valid)<3: p.error('not enough valid coarse matches')
    regions=[]
    for prev,cur in zip(valid, valid[1:]):
        x,y=float(prev['avi_seconds']),float(cur['avi_seconds'])
        if abs(float(cur['offset_seconds'])-float(prev['offset_seconds']))>=a.min_jump:
            regions.append((max(0,x-15),y+15))
    if not regions: print('No candidate jumps found.');return
    regions.sort();merged=[]
    for lo,hi in regions:
        if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
        else:merged.append((lo,hi))
    avi,dt=envelope(a.avi); bd,dtb=envelope(a.bd)
    if abs(dt-dtb)>1e-6:p.error('WAV sample timestep mismatch')
    allrows=[];events=[]
    for ix,(lo,hi) in enumerate(merged,1):
        rr=[]
        cap=min(hi,len(avi)*dt-a.window/(2*PAL_TO_BD)-.1)
        for t in np.arange(lo,cap,a.step):
            m=best_match(avi,bd,dt,float(t),a.window,30,PAL_TO_BD)
            if m is None:continue
            t,estimated,matched,offset,ncc=m
            row={'region':ix,'avi_seconds':round(t,3),'bd_matched_seconds':round(matched,3),'offset_seconds':round(offset,3),'ncc':round(ncc,3)}
            allrows.append(row)
            if ncc>=a.threshold: rr.append(row)
        span=hi-lo
        edge=max(8,min(15,span*.20))
        left=[r['offset_seconds'] for r in rr if r['avi_seconds']<lo+edge]
        right=[r['offset_seconds'] for r in rr if r['avi_seconds']>cap-edge]
        if len(left)<3 or len(right)<3:
            print(f'Region {ix}: insufficient reliable edge matches; inspect manually');continue
        before,after=median(left),median(right); jump=after-before
        if abs(jump)<a.min_jump:
            print(f'Region {ix}: no stable jump ({jump:+.3f}s)');continue
        tol=max(.2,min(.45,abs(jump)*.2))
        pre=[r for r in rr if abs(r['offset_seconds']-before)<=tol]
        post=[r for r in rr if abs(r['offset_seconds']-after)<=tol]
        choices=[(x,y) for x in pre for y in post if 0<y['avi_seconds']-x['avi_seconds']<=30]
        if choices:
            x,y=min(choices,key=lambda v:v[1]['avi_seconds']-v[0]['avi_seconds'])
            low,high=x['avi_seconds'],y['avi_seconds'];status='BRACKETED'
        else:
            low,high='','';status='REVIEW'
        events.append({'region':ix,'last_before_avi':low,'first_after_avi':high,'offset_before_s':round(before,3),'offset_after_s':round(after,3),'jump_s':round(jump,3),'status':status})
        print(f'Region {ix}: +{jump:.3f}s, AVI bracket {low}..{high}, {status}')
    def write(fn,items,headers):
        with open(fn,'w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows(items)
    write(a.samples,allrows,['region','avi_seconds','bd_matched_seconds','offset_seconds','ncc'])
    write(a.out,events,['region','last_before_avi','first_after_avi','offset_before_s','offset_after_s','jump_s','status'])
    print('Saved',a.out,'and',a.samples)
    print('Candidate regions only. CHECK all detected cuts and any flat/small shifts before render.')
if __name__=='__main__':main()
