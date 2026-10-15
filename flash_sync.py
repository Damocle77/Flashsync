#!/usr/bin/env python3
"""FlashSync 1.3 - wrapper non distruttivo per AVI PAL -> MKV Blu-ray.

Analisi: richiede flash_audio_align.py e flash_refine_auto.py nella stessa
cartella di questo programma. Preview: richiede flash_build_generic.py.
Le correzioni non sono approvate automaticamente. Vedere --help.
"""

import argparse
from array import array
import csv
import json
import math
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PAL_TEMPO = "24000/25025"
PAL_FACTOR = 25025 / 24000


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", check=False
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe non riesce a leggere {path}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Risposta ffprobe non valida per {path}") from exc


def media_identity(path):
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def execute(command, work, label):
    print(f"\n[{label}]", flush=True)
    result = subprocess.run(command, cwd=str(work), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", check=False)
    with (work / "operations.log").open("a", encoding="utf-8") as log:
        log.write("\n[" + label + "]\n")
        log.write("$ " + " ".join(map(str, command)) + "\n")
        log.write(result.stdout)
        log.write(f"\nexit_code={result.returncode}\n")
    if result.returncode:
        print(result.stdout[-5000:])
        raise RuntimeError(f"{label} terminato con codice {result.returncode}; vedere operations.log")
    print("OK", flush=True)


def audio_streams(info):
    return [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]


def video_streams(info):
    return [s for s in info.get("streams", []) if s.get("codec_type") == "video"]


def extract(source, audio_index, dest, work, expected_duration, italian=False):
    if dest.exists():
        try:
            got = float(probe(dest)["format"]["duration"])
        except (KeyError, ValueError):
            raise RuntimeError(f"File intermedio illeggibile: {dest}") from None
        expected = expected_duration * (PAL_FACTOR if italian else 1.0)
        if abs(got - expected) > max(4.0, expected * 0.005):
            raise RuntimeError(
                f"Intermedio {dest.name} probabilmente incompleto: "
                f"durata {got:.3f}s, attesa ~{expected:.3f}s. "
                "Spostarlo altrove prima di ripetere analyze."
            )
        print(f"Gia' presente: {dest.name} ({got:.1f}s)")
        return

    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-n", "-v", "warning",
           "-i", str(source), "-map", f"0:a:{audio_index}", "-vn"]
    if italian:
        cmd += ["-af", f"atempo={PAL_TEMPO}", "-ar", "48000", "-ac", "1", "-c:a", "flac"]
    else:
        cmd += ["-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le"]
    cmd.append(str(dest))
    execute(cmd, work, f"Estrazione {dest.name}")


def read_edits(path, duration_avi):
    if not path.is_file():
        raise RuntimeError(f"Mappa non trovata: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["avi_seconds", "insert_seconds"]:
            raise RuntimeError("Il CSV deve contenere esattamente: avi_seconds,insert_seconds")
        events = []
        for row in reader:
            try:
                t = float(row["avi_seconds"])
                gap = float(row["insert_seconds"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Evento non valido: {row}") from exc
            if not (math.isfinite(t) and math.isfinite(gap) and 0 < t < duration_avi and gap > 0):
                raise RuntimeError(f"Evento fuori limiti: {row}")
            events.append((t, gap))
    if not events:
        raise RuntimeError("Mappa vuota: approvare manualmente gli eventi prima del render")
    if any(a[0] >= b[0] for a, b in zip(events, events[1:])):
        raise RuntimeError("Eventi non strettamente ordinati o duplicati")
    return events



def propose_edits(work, ita_pal, duration_avi, force=False):
    """Suggerisce punti di inserimento a minima energia; non approva la mappa."""
    source = work / "flash_edits_candidates.csv"
    output = work / "flash_edits_proposed.csv"
    if not source.is_file() or not ita_pal.is_file():
        raise RuntimeError("Prima eseguire analyze e refine: CSV candidati o ITA_PAL.flac assente")
    if output.exists() and not force:
        raise RuntimeError(f"{output} esiste gia': usare --force-propose SOLO per rigenerare le proposte")

    with source.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        expected = {"event", "before_s", "after_s", "jump_s", "status"}
        if not expected.issubset(reader.fieldnames or []):
            raise RuntimeError("CSV candidati privo delle colonne necessarie")
        events = list(reader)
    if not events:
        raise RuntimeError("Nessun evento candidato")

    sr = 8000
    half_window = round(0.06 * sr)
    proposed = []
    for event in events:
        name = event["event"]
        try:
            before, after, jump = (float(event[field]) for field in ("before_s", "after_s", "jump_s"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Evento {name} senza intervallo completo: intervenire manualmente") from exc
        if not (all(map(math.isfinite, (before, after, jump)))
                and 0 < before < after < duration_avi and jump > 0):
            raise RuntimeError(f"Intervallo non valido per {name}")
        start, end = before * PAL_FACTOR, after * PAL_FACTOR
        extraction_start = max(0.0, start - 0.30)
        extraction_end = end + 0.30
        command = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", str(extraction_start),
                   "-i", str(ita_pal), "-t", str(extraction_end - extraction_start),
                   "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if process.returncode:
            raise RuntimeError(f"FFmpeg non riesce a leggere ITA_PAL.flac per {name}: "
                               + process.stderr.decode("utf-8", "replace")[-1000:])
        if len(process.stdout) % 4:
            raise RuntimeError(f"Campioni float32 corrotti per {name}")
        pcm = array("f")
        pcm.frombytes(process.stdout)
        if sys.byteorder != "little":
            pcm.byteswap()
        # Cumulative squared energy: one-pass, avoids extra Python scans for each candidate.
        cumulative = [0.0]
        energy = 0.0
        for sample in pcm:
            energy += float(sample) ** 2
            cumulative.append(energy)
        guard = min(0.15, (after - before) * PAL_FACTOR * 0.15)
        search_start, search_end = start + guard, end - guard
        if search_start > search_end:
            raise RuntimeError(f"Intervallo troppo stretto per {name}")
        points = int((search_end - search_start) / 0.01 + 1e-7) + 1
        best = None
        for index in range(points):
            t = search_start + index * 0.01
            center = round((t - extraction_start) * sr)
            lo, hi = center - half_window, center + half_window
            if lo < 0 or hi > len(pcm):
                continue
            rms = math.sqrt(max(0.0, (cumulative[hi] - cumulative[lo]) / (hi - lo)))
            if best is None or rms < best[0]:
                best = (rms, t)
        if best is None:
            raise RuntimeError(f"Nessuna finestra RMS utilizzabile per {name}")
        rms, t = best
        status = "NARROW_REVIEW" if (after - before) < 0.30 else "REVIEW_REQUIRED"
        if event["status"].startswith("UNRESOLVED") or "COARSE" in event["status"]:
            status = "COARSE_REVIEW"
        proposed.append({
            "avi_seconds": f"{t / PAL_FACTOR:.4f}",
            "insert_seconds": f"{jump:.4f}",
            "event": name,
            "bracket_start": f"{before:.3f}",
            "bracket_end": f"{after:.3f}",
            "rms": f"{rms:.6f}",
            "status": status,
        })
        print(f"{name:>4} | AVI {t / PAL_FACTOR:9.3f}s | gap {jump:6.3f}s | "
              f"RMS {rms:.5f} | {status}")
    # Fail before writing if any event is incomplete; never modify approved edit map.
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(proposed[0]))
        writer.writeheader()
        writer.writerows(proposed)
    print(f"\nProposte generate: {len(proposed)} -> {output}")
    print("flash_edits.csv NON modificato. Proposte DA VERIFICARE prima del render.")


def main():
    parser = argparse.ArgumentParser(
        description="FlashSync: analisi coppia AVI PAL/BD, preview previa approvazione, mux QA"
    )
    parser.add_argument("stage", choices=("analyze", "refine", "propose", "preview", "mux"))
    parser.add_argument("--avi", required=True, help="AVI PAL originale")
    parser.add_argument("--mkv", required=True, help="MKV Blu-ray originale")
    parser.add_argument("--work", help="Cartella lavoro; predefinita: FlashSync_<nome MKV>")
    parser.add_argument("--avi-it", type=int, default=0, help="Indice relativo AUDIO italiano AVI (default 0)")
    parser.add_argument("--avi-en", type=int, default=1, help="Indice relativo AUDIO inglese AVI (default 1)")
    parser.add_argument("--mkv-en", type=int, default=0, help="Indice relativo AUDIO inglese MKV (default 0)")
    parser.add_argument("--delay", type=float, help="Offset iniziale misurato in secondi (per preview)")
    parser.add_argument("--approve-edits", action="store_true", help="Conferma esplicita del CSV manuale")
    parser.add_argument("--force-propose", action="store_true", help="Rigenera soltanto flash_edits_proposed.csv")
    args = parser.parse_args()

    for exe in ("ffmpeg", "ffprobe"):
        if not shutil.which(exe):
            parser.error(f"{exe} non disponibile nel PATH")
    avi, mkv = Path(args.avi).resolve(), Path(args.mkv).resolve()
    if not avi.is_file() or not mkv.is_file():
        parser.error("AVI o MKV inesistente (controllare nomi e directory)")
    if args.avi_it == args.avi_en or min(args.avi_it, args.avi_en, args.mkv_en) < 0:
        parser.error("Indici audio non validi: IT ed EN AVI devono essere distinti")

    ai, bi = probe(avi), probe(mkv)
    av, bv = video_streams(ai), video_streams(bi)
    aa, ba = audio_streams(ai), audio_streams(bi)
    if not av or not bv:
        raise RuntimeError("Stream video assente")
    if max(args.avi_it, args.avi_en) >= len(aa) or args.mkv_en >= len(ba):
        raise RuntimeError("Indice audio non presente; usare ffprobe per controllare")

    fps_avi = Fraction(av[0]["r_frame_rate"])
    fps_mkv = Fraction(bv[0]["r_frame_rate"])
    if (fps_avi, fps_mkv) != (Fraction(25, 1), Fraction(24000, 1001)):
        raise RuntimeError(f"FPS diversi dal caso supportato: AVI={fps_avi}, MKV={fps_mkv}")

    duration_avi = float(ai["format"]["duration"])
    duration_mkv = float(bi["format"]["duration"])
    if not math.isfinite(duration_avi) or not math.isfinite(duration_mkv):
        raise RuntimeError("Durata sorgente non valida")

    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", mkv.stem)
    work = Path(args.work or ("FlashSync_" + safe_stem)).resolve()
    if work == avi.parent or work == mkv.parent:
        raise RuntimeError("La cartella di lavoro deve essere separata dai file originali")
    work.mkdir(parents=True, exist_ok=True)

    manifest = work / "manifest.json"
    identity = {
        "avi": media_identity(avi), "mkv": media_identity(mkv),
        "avi_it": args.avi_it, "avi_en": args.avi_en, "mkv_en": args.mkv_en,
        "pal_tempo": PAL_TEMPO
    }
    if manifest.exists():
        existing = json.loads(manifest.read_text(encoding="utf-8"))
        if existing != identity:
            raise RuntimeError("Workspace associato a sorgenti/indici diversi: scegliere un altro --work")
    else:
        manifest.write_text(json.dumps(identity, indent=2), encoding="utf-8")

    eng_avi = work / "ENG_AVI.wav"
    eng_bd = work / "ENG_BD.wav"
    ita_pal = work / "ITA_PAL.flac"
    alignment = work / "alignment.csv"
    edits = work / "flash_edits.csv"
    preview = work / "Flash_ITA_MAPPED_PREVIEW.flac"

    print(f"\nAVI: {avi.name} ({duration_avi:.3f}s, {fps_avi} fps)")
    print(f"MKV: {mkv.name} ({duration_mkv:.3f}s, {fps_mkv} fps)")
    print(f"Indici audio: AVI IT={args.avi_it}, AVI ENG={args.avi_en}, MKV ENG={args.mkv_en}")
    print(f"Workspace: {work}")
    print("ATTENZIONE: FPS 25->23.976 non prova da solo il PAL speed-up audio.")

    if args.stage == "analyze":
        required = [SCRIPTS_DIR / "flash_audio_align.py", SCRIPTS_DIR / "flash_refine_auto.py"]
        absent = [p.name for p in required if not p.is_file()]
        if absent:
            raise RuntimeError("Script ausiliari mancanti accanto a flash_sync.py: " + ", ".join(absent))
        extract(avi, args.avi_en, eng_avi, work, duration_avi)
        extract(mkv, args.mkv_en, eng_bd, work, duration_mkv)
        extract(avi, args.avi_it, ita_pal, work, duration_avi, italian=True)
        if not alignment.exists():
            execute([sys.executable, str(required[0]), "--avi", str(eng_avi),
                     "--bd", str(eng_bd), "--out", str(alignment)], work, "Allineamento grossolano")
        refined = work / "flash_jumps_refined.csv"
        if not refined.exists():
            execute([sys.executable, str(required[1]), "--coarse", str(alignment),
                     "--avi", str(eng_avi), "--bd", str(eng_bd)], work, "Raffinamento")
        if not edits.exists():
            edits.write_text("avi_seconds,insert_seconds\n", encoding="utf-8")
        print("\nAnalisi terminata. Esaminare:")
        for name in ("alignment.csv", "flash_jumps_refined.csv", "flash_refinement_samples.csv"):
            print(" -", work / name)
        print("CSV manuale per i tagli:", edits)
        print("NESSUN TAGLIO APPROVATO AUTOMATICAMENTE.")

    elif args.stage == "refine":
        helper = SCRIPTS_DIR / "flash_sync_refine.py"
        if not helper.is_file():
            raise RuntimeError("Script mancante: flash_sync_refine.py")
        for name in ("flash_refinement_samples.csv", "ENG_AVI.wav", "ENG_BD.wav"):
            if not (work / name).is_file():
                raise RuntimeError(f"Prima eseguire analyze: assente {name}")
        execute([sys.executable, str(helper), "--work", str(work)], work, "Raffinamento locale candidati")
        print("Mappa NON approvata:", work / "flash_edits_candidates.csv")

    elif args.stage == "propose":
        propose_edits(work, ita_pal, duration_avi, force=args.force_propose)

    elif args.stage == "preview":
        builder = SCRIPTS_DIR / "flash_build_generic.py"
        if not builder.is_file():
            raise RuntimeError("Script mancante accanto a flash_sync.py: flash_build_generic.py")
        if not args.approve_edits:
            raise RuntimeError("Preview richiede --approve-edits dopo verifica manuale delle giunzioni")
        if args.delay is None or not math.isfinite(args.delay) or args.delay < 0 or args.delay >= 120:
            raise RuntimeError("Indicare --delay con l'offset iniziale misurato (0 <= delay < 120)")
        if not ita_pal.is_file():
            raise RuntimeError("Audio ITA_PAL.flac mancante: eseguire prima analyze")
        events = read_edits(edits, duration_avi)
        if preview.exists():
            raise RuntimeError("Preview già presente: non sovrascrivere, usare un altro workspace")
        print(f"Eventi approvati manualmente: {len(events)}; durata gap={sum(v for _, v in events):.3f}s")
        execute([sys.executable, str(builder), "--input", str(ita_pal), "--events", str(edits),
                 "--delay", str(args.delay), "--render"], work, "Render FLAC diagnostico")
        if not preview.is_file():
            raise RuntimeError("Builder terminato ma output Flash_ITA_MAPPED_PREVIEW.flac mancante")
        print("Preview generata:", preview)

    elif args.stage == "mux":
        if not preview.is_file():
            raise RuntimeError("Preview FLAC assente: eseguire prima preview")
        output = work / "SYNC_QA.mkv"
        if output.exists():
            raise RuntimeError("MKV QA già presente: non sovrascrivere")
        new_audio_index = len(ba)
        execute(["ffmpeg", "-hide_banner", "-nostdin", "-n", "-i", str(mkv),
                 "-i", str(preview), "-map", "0", "-map", "1:a:0", "-c", "copy",
                 f"-metadata:s:a:{new_audio_index}", "language=ita",
                 f"-disposition:a:{new_audio_index}", "0", str(output)], work, "Mux MKV QA")
        print("MKV QA generato:", output)
        print("Non definitivo: controllare dialoghi, salti, eventuali sezioni musicali e sigle.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print("\nERRORE:", exc, file=sys.stderr)
        sys.exit(1)
