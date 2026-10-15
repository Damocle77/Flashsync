# FlashSync

**Sincronizzazione assistita di tracce audio tra edizioni video PAL e BD, senza ricodificare il video.**

FlashSync è un progetto Python che aiuta ad adattare una traccia audio proveniente da un'**edizione AVI PAL a 25 fps** a un **Blu-ray MKV a 24000/1001 fps**. Usa una traccia audio comune alle due edizioni come riferimento per stimare differenze temporali, trovare possibili tagli e creare una **traccia di prova** da verificare in ascolto.

È nato per recuperare doppiaggi italiani presenti in vecchie edizioni televisive o home video, mantenendo il video HD dell'edizione Blu-ray.

> [!IMPORTANT]
> **FlashSync 1.3 è uno strumento sperimentale di allineamento assistito, non un editor automatico definitivo.** Le correzioni richiedono controllo umano. Inserire silenzio può ripristinare l'allineamento temporale, ma **non ricrea dialoghi, musica o effetti** eventualmente mancanti nell'edizione sorgente.

## Come funziona

1. Confronta **l'audio inglese dell'AVI** con **l'audio inglese del Blu-ray** attraverso correlazione normalizzata (NCC).
2. Corregge la velocità dell'audio italiano per il passaggio da 25 a 24000/1001 fps, **solo nel caso di effettivo PAL speed-up**.
3. Individua gli scarti temporali e raffina le discontinuità candidate.
4. Suggerisce punti di inserimento a energia RMS relativamente bassa, evitando di approvarli automaticamente.
5. Dopo revisione, genera un **FLAC diagnostico** con i gap indicati e lo inserisce in un **MKV QA**, copiando i flussi originali senza ricodificare il video.

```text
AVI PAL (ITA + ENG) ──┐
                     ├─ analyze ─ refine ─ propose ─ revisione umana ─ preview ─ mux ─ QA
Blu-ray MKV (ENG) ──┘
```

## Requisiti

- **Python 3**, con `numpy` e `scipy`.
- **FFmpeg** e **FFprobe** disponibili nel `PATH`.
- Windows con **Git Bash** (ambiente utilizzato per i test), oppure una shell compatibile.
- AVI con una traccia audio italiana e una inglese **già sincronizzate fra loro**.
- MKV Blu-ray con traccia inglese di riferimento sufficientemente simile.
- Video AVI a **25 fps** e video MKV a **24000/1001 fps**: gli altri rapporti non sono gestiti dal controller attuale.

Installazione delle dipendenze Python:

```bash
python -m pip install numpy scipy
```

Verifica gli eseguibili:

```bash
python --version
ffmpeg -version
ffprobe -version
```

> [!NOTE]
> Il frame rate a 25 fps non dimostra, da solo, che l'audio sia stato accelerato per il PAL. Verifica il materiale prima di applicare il fattore di conversione: FlashSync non lo riconosce automaticamente.

## Struttura del repository

```text
FlashSync/
├── flash_sync.py          # Controller: analyze / refine / propose / preview / mux
├── flash_sync_refine.py   # Raffinamento locale NCC e plateau
├── flash_audio_align.py   # Analisi grossolana tra i riferimenti inglesi
├── flash_refine_auto.py   # Campionamento e regioni candidate
├── flash_build_generic.py# Costruzione FLAC diagnostico con gap di silenzio
├── flashsync.md           # Guida operativa dettagliata
└── README.md
```

I file multimediali possono essere collocati accanto agli script oppure indicati mediante percorso. I file intermedi vengono salvati in una cartella `FlashSync_<nome_MKV>/` separata dagli originali.

## Guida rapida

Negli esempi seguenti, sostituisci i nomi `episodio_pal.avi` ed `episodio_bluray.mkv` con quelli dei tuoi file.

### 1. Analisi iniziale

```bash
python flash_sync.py analyze \
  --avi "episodio_pal.avi" \
  --mkv "episodio_bluray.mkv"
```

Genera, tra gli altri, `ENG_AVI.wav`, `ENG_BD.wav`, `ITA_PAL.flac`, `alignment.csv` e `flash_refinement_samples.csv` nel workspace dedicato.

### 2. Raffinamento delle discontinuità

```bash
python flash_sync.py refine \
  --avi "episodio_pal.avi" \
  --mkv "episodio_bluray.mkv"
```

Produce `flash_edits_candidates.csv` e `flash_refine_local.csv`. Gli stati `REVIEW_REQUIRED` e `REVIEW_REQUIRED_COARSE` non sono approvazioni: descrivono intervalli candidati da controllare.

> Il comando `refine` del controller non sovrascrive i candidati già presenti. Per rigenerarli dopo una verifica, è possibile usare direttamente `flash_sync_refine.py --work "<cartella_workspace>" --force`.

### 3. Proposta dei punti di inserimento

```bash
python flash_sync.py propose \
  --avi "episodio_pal.avi" \
  --mkv "episodio_bluray.mkv"
```

Legge `flash_edits_candidates.csv`, analizza l'energia RMS di `ITA_PAL.flac` vicino a ogni discontinuità e salva `flash_edits_proposed.csv` con tempo, durata candidata, RMS e stato di revisione. **Non modifica** `flash_edits.csv`.

Per rigenerare solo le proposte già presenti, aggiungi `--force-propose`.

### 4. Revisione obbligatoria della mappa

Ascolta e controlla ciascun evento. Dopo la revisione, compila nel workspace `flash_edits.csv` con **esattamente due colonne**:

```csv
avi_seconds,insert_seconds
211.0810,2.2310
```

La riga numerica è **solo un esempio tratto da un caso di test**: non copiarla in altri episodi. `avi_seconds` si riferisce alla timeline AVI **prima** del rallentamento; `insert_seconds` è la durata positiva da aggiungere. I salti negativi e il montaggio di scene diverse non sono gestiti automaticamente dal builder.

Determina inoltre il `--delay` iniziale per **ogni episodio**, a partire dai primi offset affidabili di correlazione; non riusare valori di altri episodi.

### 5. Generazione del FLAC di prova

```bash
python flash_sync.py preview \
  --avi "episodio_pal.avi" \
  --mkv "episodio_bluray.mkv" \
  --delay <OFFSET_INIZIALE> \
  --approve-edits
```

Sostituisci `<OFFSET_INIZIALE>` con un numero misurato, per esempio `0.86` **solo se verificato per quell'episodio**. L'opzione `--approve-edits` è una conferma esplicita del contenuto del CSV manuale: non indica una convalida automatica.

Output: `Flash_ITA_MAPPED_PREVIEW.flac`.

### 6. Mux del Blu-ray di prova

```bash
python flash_sync.py mux \
  --avi "episodio_pal.avi" \
  --mkv "episodio_bluray.mkv"
```

Output: `SYNC_QA.mkv`, nella cartella di lavoro. I flussi del Blu-ray vengono copiati (`-c copy`), con l'aggiunta della traccia italiana diagnostica.

### 7. Controllo qualità (QA)

Verifica il sincronismo delle voci all'inizio, a metà e alla fine, oltre a **prima e dopo ogni giunzione**. Ascolta eventuali interruzioni innaturali, silenzi in presenza di musica o dialoghi e differenze di montaggio fra le edizioni.

## File prodotti

| File | Utilizzo |
| --- | --- |
| `manifest.json` | Identità delle sorgenti e degli indici audio del workspace |
| `operations.log` | Log delle operazioni gestite dal controller |
| `alignment.csv` | Offset e correlazioni grossolane |
| `flash_jumps_refined.csv` | Regioni di salto rilevate |
| `flash_refinement_samples.csv` | Campioni NCC per le regioni |
| `flash_edits_candidates.csv` | Intervalli candidati dopo il raffinamento locale |
| `flash_refine_local.csv` | Dettaglio dei confronti NCC locali |
| `flash_edits_proposed.csv` | Punti proposti in base all'RMS, da verificare |
| `flash_edits.csv` | **Mappa revisionata manualmente**, letta dalla preview |
| `Flash_ITA_MAPPED_PREVIEW.flac` | Traccia italiana diagnostica |
| `SYNC_QA.mkv` | MKV con la traccia diagnostica aggiunta |

## Limiti e precauzioni

- **La NCC identifica somiglianze e discontinuità, non certifica punti di montaggio perfetti.** Scene con musica ripetitiva, silenzi o materiale differente possono produrre falsi match.
- **Il minimo RMS non coincide necessariamente con un taglio corretto.** Le proposte richiedono verifica visiva e d'ascolto.
- **I gap vengono riempiti con silenzio.** Per ricostruire musica, effetti o dialoghi serve un successivo editing manuale o una diversa strategia di compositing audio.
- **Non tutti gli episodi condividono la stessa mappa temporale.** Offset e interventi vanno calcolati di nuovo per ogni coppia AVI/MKV.
- **Niente sovrascritture automatiche delle preview:** in caso di nuova prova, rinomina o archivia l'output precedente prima di rigenerarlo.
- Gli script non eliminano né modificano i sorgenti, ma il workspace può contenere file di grandi dimensioni: tieni fuori dal repository audio, video, CSV di lavorazione e altri output generati.

## Stato del progetto

**Versione corrente: 1.3 (sperimentale).** La pipeline è stata utilizzata con successo per realizzare anteprime sincronizzate di materiale PAL rispetto a un Blu-ray; non costituisce una validazione universale su ogni edizione.

I contributi sono benvenuti, soprattutto su gestione dei tagli negativi, ricostruzione di audio mancante, rilevamento automatico dei falsi plateau, QA delle giunzioni e test riproducibili.

## Documentazione

La guida tecnica approfondita è disponibile in [`flashsync.md`](flashsync.md).

## Licenza

**Da definire.** Prima di pubblicare il codice come open source, aggiungi un file `LICENSE` con la licenza scelta. Il progetto non include né distribuisce materiale audiovisivo protetto da copyright; usa soltanto sorgenti che hai diritto di elaborare.

