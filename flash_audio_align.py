#!/usr/bin/env python3
"""Misura l'allineamento temporale fra due WAV inglesi: PAL AVI e Blu-ray.
Non modifica i file originali. Richiede numpy e scipy.
"""
import argparse
import csv
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate
from scipy.ndimage import gaussian_filter1d

PAL_TO_BD = 25 * 1001 / 24000


def envelope(path, step_ms=20):
    rate, x = wavfile.read(path, mmap=True)
    if x.ndim != 1:
        raise ValueError(f"{path}: atteso WAV mono")
    hop = round(rate * step_ms / 1000)
    if hop <= 0:
        raise ValueError('Sample rate non valido')
    n = len(x) // hop
    if n < 1:
        raise ValueError(f"{path}: file vuoto")
    # Chunked: evita grandi array float64 per audio da 90 minuti.
    energy = np.empty(n, dtype=np.float32)
    for start in range(0, n, 10000):
        end = min(n, start + 10000)
        block = np.asarray(x[start * hop:end * hop], dtype=np.float32)
        block = block.reshape(end-start, hop)
        energy[start:end] = np.sqrt(np.mean(block * block, axis=1))
    # Compressione dinamica e smussamento; utile con master audio differenti.
    env = np.log1p(energy)
    return gaussian_filter1d(env, 1.5).astype(np.float32), hop / rate


def best_match(avi, bd, dt, t_avi, window_s, radius_s, ratio):
    # Entrambi i vettori in scala temporale BD; l'AVI viene allungato virtualmente.
    t_expected = t_avi * ratio
    half = window_s / 2
    if t_avi - half / ratio < 0 or t_avi + half / ratio > len(avi)*dt:
        return None
    length = round(window_s / dt)
    rel = (np.arange(length) - length//2) * dt
    avi_positions = (t_expected + rel) / ratio / dt
    template = np.interp(avi_positions, np.arange(len(avi)), avi).astype(np.float64)
    template -= template.mean()
    tnorm = np.linalg.norm(template)
    if tnorm < 1e-6:
        return None
    # Confronta template con tutte le finestre BD nel raggio richiesto.
    start = max(0, int(round((t_expected - radius_s - half)/dt)))
    stop = min(len(bd), int(round((t_expected + radius_s + half)/dt)) + 1)
    segment = np.asarray(bd[start:stop], dtype=np.float64)
    if len(segment) < length:
        return None
    corr = correlate(segment, template, mode='valid', method='fft')
    sums = np.cumsum(np.r_[0.0, segment])
    sums2 = np.cumsum(np.r_[0.0, segment * segment])
    ss = sums[length:] - sums[:-length]
    ss2 = sums2[length:] - sums2[:-length]
    variance = np.maximum(ss2 - ss*ss/length, 0)
    scores = corr / np.maximum(np.sqrt(variance)*tnorm, 1e-9)
    k = int(np.argmax(scores))
    match_center = (start + k + length//2) * dt
    return (t_avi, t_expected, match_center, match_center-t_expected, float(scores[k]))


def stamp(t):
    if t < 0:
        return '-' + stamp(-t)
    return f'{int(t//3600):02}:{int(t%3600//60):02}:{t%60:06.3f}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--avi', default='Flash_ENG_AVI.wav')
    parser.add_argument('--bd', default='Flash_ENG_BD.wav')
    parser.add_argument('--out', default='flash_alignment.csv')
    parser.add_argument('--window', type=float, default=35, help='finestra correlazione in secondi BD')
    parser.add_argument('--radius', type=float, default=30, help='ricerca +/- secondi rispetto alla previsione PAL')
    parser.add_argument('--threshold', type=float, default=0.35, help='NCC minima per risultato attendibile')
    args = parser.parse_args()
    if args.window <= 0 or args.radius < 0:
        parser.error('window deve essere >0, radius >=0')
    avi, dt_a = envelope(args.avi)
    bd, dt_b = envelope(args.bd)
    if abs(dt_a-dt_b) > 1e-6:
        parser.error('I WAV devono avere la stessa frequenza di campionamento (es. 8000 Hz)')
    da, db = len(avi)*dt_a, len(bd)*dt_b
    print(f'AVI inglese: {stamp(da)}; BD inglese: {stamp(db)}; fattore PAL: {PAL_TO_BD:.9f}')
    max_anchor = da - args.window / (2*PAL_TO_BD) - 1
    anchors = list(np.arange(60, max_anchor, 90))
    # Densità maggiore negli ultimi otto minuti.
    anchors += list(np.arange(max(60, max_anchor-480), max_anchor, 15))
    anchors = sorted({round(a, 3) for a in anchors if a > args.window/(2*PAL_TO_BD)})
    rows = []
    print('AVI tempo      | BD stimato     | BD trovato     | scarto BD  | NCC   | esito')
    print('-'*84)
    for a in anchors:
        r = best_match(avi, bd, dt_a, a, args.window, args.radius, PAL_TO_BD)
        if r is None:
            continue
        t, expected, found, shift, score = r
        status = 'OK' if score >= args.threshold else 'INCERTO'
        rows.append(dict(avi_seconds=round(t,3), bd_expected_seconds=round(expected,3),
                         bd_matched_seconds=round(found,3), offset_seconds=round(shift,3),
                         ncc=round(score,3), status=status))
        print(f'{stamp(t)} | {stamp(expected)} | {stamp(found)} | {shift:+9.3f}s | {score:5.3f} | {status}')
    with open(args.out, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['avi_seconds','bd_expected_seconds','bd_matched_seconds','offset_seconds','ncc','status'])
        writer.writeheader()
        writer.writerows(rows)
    valid = [r for r in rows if r['status'] == 'OK']
    print(f'\nCSV salvato: {Path(args.out).resolve()}')
    print(f'Risultati attendibili: {len(valid)}/{len(rows)} (soglia NCC {args.threshold})')
    if len(valid) >= 2:
        early = [r['offset_seconds'] for r in valid if r['avi_seconds'] < max_anchor-480]
        late = [r['offset_seconds'] for r in valid if r['avi_seconds'] >= max_anchor-480]
        if early and late:
            print(f'Offset mediano prima degli ultimi 8 min: {np.median(early):+.3f} s')
            print(f'Offset mediano negli ultimi 8 min: {np.median(late):+.3f} s')
        jumps = [(p, q) for p,q in zip(valid, valid[1:]) if abs(q['offset_seconds']-p['offset_seconds']) >= 1]
        if jumps:
            print('Possibili discontinuità (da verificare, possono essere falsi match):')
            for p,q in jumps:
                print(f"  tra AVI {stamp(p['avi_seconds'])} e {stamp(q['avi_seconds'])}: "
                      f"scarto {p['offset_seconds']:+.2f} -> {q['offset_seconds']:+.2f} s")
        else:
            print('Nessun salto >=1 s fra punti attendibili consecutivi.')
    else:
        print('ATTENZIONE: correlazione poco affidabile; non applicare correzioni automatiche.')


if __name__ == '__main__':
    main()
