# FlashSync 1.3 — Procedura operativa AVI PAL → Blu-ray

**Ambiente di riferimento:** Windows + Git Bash, cartella `E:\Download\Flash_1080p`.  
**Obiettivo:** allineare il doppiaggio italiano di un AVI PAL 25 fps a un MKV Blu-ray 24000/1001 fps, preservando il video originale.  
**Regola:** il programma propone tempi e gap, ma **non approva automaticamente** tagli o sostituzioni di contenuto.

## 0. Preparazione e verifiche

Gli script devono risiedere nella directory principale:

```text
E:\Download\Flash_1080p\
├── flash_sync.py              (versione 1.3)
├── flash_sync_refine.py       (raffinamento 1.2)
├── flash_audio_align.py
├── flash_refine_auto.py
├── flash_build_generic.py
├── The Flash 03 Sulle Tracce Di Flash.avi
└── The Flash 03 Watching the Detectives 1080p.mkv
```

Prerequisiti: Python 3, `ffmpeg`, `ffprobe`, `numpy`, `scipy`.

```bash
python -m pip install numpy scipy
ffmpeg -version
ffprobe -version
cd /e/Download/Flash_1080p
```

Controlla la lingua effettiva delle tracce (i metadati non sono sempre affidabili):

```bash
ffprobe -v error -select_streams a \
  -show_entries stream=index,codec_name,channels,sample_rate:stream_tags=language,title \
  -of json "The Flash 03 Sulle Tracce Di Flash.avi"

ffprobe -v error -select_streams a \
  -show_entries stream=index,codec_name,channels,sample_rate:stream_tags=language,title \
  -of json "The Flash 03 Watching the Detectives 1080p.mkv"
```

**Selezione delle tracce:** i valori predefiniti sono AVI ITA `0:a:0`, AVI ENG `0:a:1`, MKV ENG `0:a:0`. `0:a:N` indica la N-esima **traccia audio**, non l'ID assoluto del flusso. Cambia `--avi-it`, `--avi-en` o `--mkv-en` se necessario.

**Presupposto da verificare:** inglese AVI e italiano AVI sincronizzati tra loro. Il cambio da 25 fps a 23,976 fps non dimostra da solo che l'audio abbia subito un PAL speed-up: se la sorgente non lo ha subito, la conversione temporale non è applicabile automaticamente.

## 1. `analyze` — Estrazione e correlazione iniziale

```bash
python flash_sync.py analyze \
  --avi "The Flash 03 Sulle Tracce Di Flash.avi" \
  --mkv "The Flash 03 Watching the Detectives 1080p.mkv"
```

Il comando crea il workspace:

`FlashSync_The_Flash_03_Watching_the_Detectives_1080p/`

E prepara:

- `ENG_AVI.wav` / `ENG_BD.wav`: inglesi mono 8 kHz per confronto NCC.
- `ITA_PAL.flac`: italiano già rallentato con `atempo=24000/25025`, mono 48 kHz, **senza ancora inserimenti**.
- `alignment.csv`: ancore e offset grossolani.
- `flash_jumps_refined.csv` e `flash_refinement_samples.csv`: regioni candidate e campioni di allineamento.
- `flash_edits.csv`: solo intestazione, se non esiste già.

**Non rallentare nuovamente `ITA_PAL.flac`.** La trasformazione PAL si applica una volta sola. Se gli intermedi sono già presenti, `analyze` tenta di riutilizzarli e controllarne approssimativamente la durata.

## 2. `refine` — Suddivisione e delimitazione dei salti

```bash
python flash_sync.py refine \
  --avi "The Flash 03 Sulle Tracce Di Flash.avi" \
  --mkv "The Flash 03 Watching the Detectives 1080p.mkv"
```

Output:

- `flash_edits_candidates.csv`: un evento per ogni salto positivo riconosciuto, con `before_s`, `after_s`, `offset_pre`, `offset_post`, `jump_s` e `status`.
- `flash_refine_local.csv`: NCC misurata sui due plateau vicino ai possibili cambi.

**Gli estremi `before_s` e `after_s` sono intervalli di incertezza, non punti di montaggio certificati.** Un evento può essere `REVIEW_REQUIRED` oppure `REVIEW_REQUIRED_COARSE`; entrambi richiedono verifica.

Per ristampare il CSV:

```bash
column -s, -t < \
  FlashSync_The_Flash_03_Watching_the_Detectives_1080p/flash_edits_candidates.csv
```

Per **rifare** il raffinamento in un workspace già elaborato, il wrapper non espone `--force` al helper: usa direttamente (dopo aver salvato le mappe se necessario):

```bash
python flash_sync_refine.py \
  --work FlashSync_The_Flash_03_Watching_the_Detectives_1080p \
  --force
```

Questo rigenera i CSV di correlazione locale, **non** `flash_edits.csv`, FLAC o MKV.

## 3. `propose` — Ricerca automatica dei minimi RMS

**Questo è lo STEP 3 integrato in FlashSync 1.3.** Non serve più copiare blocchi Python per individuare le pause nell'italiano.

```bash
python flash_sync.py propose \
  --avi "The Flash 03 Sulle Tracce Di Flash.avi" \
  --mkv "The Flash 03 Watching the Detectives 1080p.mkv"
```

Il programma legge `flash_edits_candidates.csv`, converte gli intervalli AVI nella timeline del FLAC rallentato usando `25025/24000` e cerca il minimo RMS in finestre **120 ms**, con passo **10 ms**. Genera:

`flash_edits_proposed.csv`

Colonne:

| Colonna | Significato |
|---|---|
| `avi_seconds` | Punto proposto nella timeline **AVI originale** |
| `insert_seconds` | Salto positivo da compensare con un gap |
| `event` | Identificatore dell'evento |
| `bracket_start`, `bracket_end` | Intervallo AVI considerato |
| `rms` | Energia RMS nel punto proposto |
| `status` | Indicatore di revisione |

Interpretazione degli stati:

- `REVIEW_REQUIRED`: intervallo normale, taglio da ascoltare.
- `NARROW_REVIEW`: intervallo stretto (< 0,30 s), punto da confermare con maggiore attenzione.
- `COARSE_REVIEW`: il raffinamento locale non ha delimitato la discontinuità con precisione; la proposta viene dal bracket grossolano.

**Nessuno stato equivale ad approvazione.** Il minimo RMS è il punto meno energico fra i candidati, non la prova che sia silenzio o che coincida col taglio dell'edizione.

Se `flash_edits_proposed.csv` esiste già e vuoi rigenerarlo:

```bash
python flash_sync.py propose \
  --avi "The Flash 03 Sulle Tracce Di Flash.avi" \
  --mkv "The Flash 03 Watching the Detectives 1080p.mkv" \
  --force-propose
```

Questo sovrascrive **solo** `flash_edits_proposed.csv` e non tocca la mappa manuale.

Se un evento non ha un intervallo valido o un gap positivo, il comando si interrompe: non inventare un punto di montaggio.

## 4. Validazione e preparazione di `flash_edits.csv`

Apri/ascolta i sei (o più) punti candidati e verifica almeno: parlato integro, assenza di parole troncate, andamento del labiale prima/dopo la giunzione, presenza di musica o effetti. È possibile modificare manualmente le proposte prima di procedere.

`flash_edits.csv` accetta **esattamente** due colonne:

```csv
avi_seconds,insert_seconds
```

Le colonne extra di `flash_edits_proposed.csv` non sono ammesse dal builder. Dopo **aver deciso esplicitamente di utilizzare le proposte per un test diagnostico**, esegui:

```bash
cd /e/Download/Flash_1080p/FlashSync_The_Flash_03_Watching_the_Detectives_1080p
cp -n flash_edits.csv flash_edits.csv.bak

python - <<'PY'
import csv
from pathlib import Path

source = Path("flash_edits_proposed.csv")
target = Path("flash_edits.csv")
with source.open(newline="", encoding="utf-8-sig") as file:
    proposals = list(csv.DictReader(file))

edits = [(float(r["avi_seconds"]), float(r["insert_seconds"])) for r in proposals]
if not edits or any(t <= 0 or gap <= 0 for t, gap in edits):
    raise SystemExit("Nessun evento valido: mappa non scritta")
if any(a[0] >= b[0] for a, b in zip(edits, edits[1:])):
    raise SystemExit("Tempi AVI non strettamente ordinati: mappa non scritta")

with target.open("w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(("avi_seconds", "insert_seconds"))
    writer.writerows(edits)
print(f"Mappa diagnostica pronta: {len(edits)} inserimenti")
PY

cat flash_edits.csv
```

La copia di backup è creata solo se non esiste già. Se vuoi conservare revisioni successive della mappa, crea prima backup con nomi diversi. **Questo import è un'azione manuale e non rende i tagli automaticamente corretti.**

## 5. `preview` — FLAC italiano diagnostico

Torna nella directory del progetto:

```bash
cd /e/Download/Flash_1080p
```

Per l'**episodio 03** l'offset iniziale misurato era circa `+0.86 s`:

```bash
python flash_sync.py preview \
  --avi "The Flash 03 Sulle Tracce Di Flash.avi" \
  --mkv "The Flash 03 Watching the Detectives 1080p.mkv" \
  --delay 0.86 \
  --approve-edits
```

Risultato: `Flash_ITA_MAPPED_PREVIEW.flac` nel workspace.

Il valore `--delay` va **rimisurato in ogni episodio** dai primi offset stabili della correlazione, verificandolo con i dialoghi. L'opzione `--approve-edits` conferma solo che vuoi renderizzare la mappa che hai controllato; non è un indice oggettivo di affidabilità.

Il builder applica **silenzi** ai salti positivi e non gestisce tagli negativi o ricostruzioni di contenuto. Non sovrascrive un FLAC di preview già presente.

## 6. `mux` — MKV di prova senza re-encoding video

```bash
python flash_sync.py mux \
  --avi "The Flash 03 Sulle Tracce Di Flash.avi" \
  --mkv "The Flash 03 Watching the Detectives 1080p.mkv"
```

Risultato: `SYNC_QA.mkv`. La traccia italiana FLAC viene aggiunta al MKV; gli stream esistenti sono copiati con `-c copy`. Il file Blu-ray originale non viene modificato. La nuova traccia è marcata `ita` e non viene impostata come predefinita.

Il mux rifiuta di sovrascrivere `SYNC_QA.mkv` esistente. Per una nuova revisione preserva la precedente, poi decidi come gestire i file del workspace; non ripetere semplicemente il comando aspettandoti che aggiorni la prova.

## 7. QA — Controllo prima della finalizzazione

Verifica **inizio, metà e fine** dell'episodio, tutti i cambi rilevati e le due zone immediatamente adiacenti a ogni inserimento. Controlla:

- Labiale e sincronismo dei dialoghi prima **e dopo** ogni evento.
- Parole troncate, pause anomale, cambi di volume o clic.
- Musica/effetti nei gap: se il Blu-ray ha contenuti aggiuntivi, il silenzio tecnico non è una soluzione definitiva.
- Scene differenti fra AVI e Blu-ray, tagli negativi, possibili eventi non intercettati (anche inferiori alla soglia di rilevamento).
- Errori di decodifica MP3 dall'AVI (`Header missing`) se percepisci perdite o distorsioni.

### Risultato noto: episodio 03

| Evento | Intervallo AVI stimato (s) | Gap stimato (s) |
|---|---:|---:|
| 1:1 | 209,7–211,3 | 2,231 |
| 1:2 | 269,7–269,8 | 1,456 |
| 2:1 | 780,8–781,0 | 1,464 |
| 3:1 | 1458,7–1458,9 | 5,458 |
| 4:1 | 2120,8–2121,0 | 1,783 |
| 5:1 | 2683,0–2684,0 | 0,559 (*COARSE*) |
| **Totale** | | **12,951** |

Test effettuato con sei gap, `--delay 0.86` e `SYNC_QA.mkv`; il sincronismo è stato giudicato funzionante all'ascolto. **Questo non equivale a ricostruzione completa di musiche ed effetti** e non dimostra che l'episodio successivo utilizzi gli stessi tagli.

## 8. Errori comuni e recupero

| Messaggio / situazione | Intervento |
|---|---|
| `flash_edits_candidates.csv` esiste già | Riavvia direttamente `flash_sync_refine.py --work <workspace> --force` solo se vuoi davvero ricalcolare |
| `flash_edits_proposed.csv` esiste già | Ripeti `propose` con `--force-propose`; non cambia `flash_edits.csv` |
| `preview` richiede `--approve-edits` | Verifica e compila il CSV manuale prima del render |
| `Preview già presente` / `MKV QA già presente` | Conserva prima la vecchia prova; i file non sono sovrascritti automaticamente |
| `Workspace associato a sorgenti/indici diversi` | Non riutilizzare la cartella con altre fonti; scegli un altro `--work` e riparti da `analyze` |
| `UNRESOLVED` o `COARSE_REVIEW` | Esamina NCC, audio e scena: non trattare l'intervallo come taglio certo |
| Salto negativo / scena alterata | Builder a silenzi non sufficiente: necessaria lavorazione segmentale |

## 9. Schema riassuntivo

```text
AVI PAL + MKV Blu-ray (originali intatti)
               |
               v
   flash_sync.py analyze
               |  WAV ENG, ITA_PAL.flac, CSV grossolani
               v
   flash_sync.py refine
               |  flash_edits_candidates.csv
               v
   flash_sync.py propose
               |  flash_edits_proposed.csv (RMS, da verificare)
               v
   REVISIONE UMANA + flash_edits.csv (2 colonne)
               |
               v
   flash_sync.py preview --delay ... --approve-edits
               |  Flash_ITA_MAPPED_PREVIEW.flac
               v
   flash_sync.py mux
               |  SYNC_QA.mkv
               v
   QA audio/video e eventuale ricostruzione musicale
```

Per panoramica del progetto e avvio rapido, vedi [`README.md`](README.md).
