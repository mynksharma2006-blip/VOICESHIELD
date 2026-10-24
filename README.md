# VoiceShield AI

A local-first prototype for screening short audio clips. The app accepts WAV, FLAC, and OGG uploads (including WhatsApp OGG/Opus recordings) of 2–10 seconds, normalizes audio to mono/16 kHz, plays it in the browser, and uses a pretrained AASIST anti-spoofing model to return a cautious screening label. The API processes uploads in memory and does not retain them.

## Project structure

- `backend/` contains the FastAPI service, inference code, evaluation/training scripts, model files, local datasets, and Python requirements.
- `frontend/` contains the web interface in `web/` and native Android/iOS clients in `mobile/`.
- `.env`, `start.bat`, and the Python virtual environment stay at the project root.

## Scam News & Safety

Open **Scam News & Safety** from the web app header or visit `/scam-news`. Choose a country to view official source information and any available live alerts. The United Kingdom view reads the GOV.UK Atom search feed. The India view reads PIB's official press-release feed and checks original releases for an official date and summary. Both filter to scam-related items and only show dated items from the past 60 days. The United States, Canada, and Australia views link to their official agencies, but live feeds for those countries are not configured yet; the page clearly says unavailable and shows no sample or cached items. Feed requests are not cached by the API. The page includes practical safety tips and shows the feed update time when the source provides it, plus the last checked time.

The web interface can be switched between English, Hindi, Bengali, Tamil, Telugu, and Marathi from the language selector on either page or in settings. The selection is saved in this browser and shared between the VoiceShield screens. Main navigation, common screen text, safety tips, reporting-form guidance, and news-feed states are localized. Government news headlines/summaries, official-service descriptions, and some model-generated explanations or server error details remain in their source language (often English).

Official sources: [GOV.UK](https://www.gov.uk/), [FTC](https://consumer.ftc.gov/scams), [Canadian Anti-Fraud Centre](https://antifraudcentre-centreantifraude.ca/index-eng.htm), [Scamwatch Australia](https://www.scamwatch.gov.au/), and [Press Information Bureau of India](https://pib.gov.in/).

## Report Cyber Fraud

The **Report Cyber Fraud** screen saves an incident report only to the local backend storage folder `backend/data/private_reports/`. Reports are stored in `reports.jsonl`; an optional evidence file is kept beside it under a generated name. Evidence is limited to one PDF, PNG, JPEG, or UTF-8 text file up to 5 MB. The API enforces a total request-size limit, validates fields and evidence signatures, and has no public listing or evidence-download route. Do not expose the local server or storage directory to an untrusted network. Anyone with access to the machine’s files can access these reports.

The reporter must provide a name, a safe follow-up method (email, phone, or an opted-in callback request), incident details, accuracy confirmation, and consent to local storage. A successful save creates a unique complaint reference and the initial **Submitted** status. The app also displays a high-entropy private access key once; its SHA-256 hash, not the key itself, is stored. Keep the reference and key separately safe. The **Check a private report** form requires both credentials and returns limited report details and status; a reference alone cannot retrieve a report. Lost keys cannot be recovered. Legacy reports created before secure access keys remain stored but are not eligible for self-service lookup; do not attempt reference-only access.

Statuses are not advanced automatically. An authorized local operator can manually change one with the server stopped, from the project root:

```powershell
.\.venv\Scripts\python.exe backend\scripts\update_report_status.py VS-REFERENCE "Under Review"
.\.venv\Scripts\python.exe backend\scripts\update_report_status.py VS-REFERENCE Resolved
```

Allowed values are `Submitted`, `Under Review`, and `Resolved`. Run this maintenance command only on the trusted backend computer and stop the server first to avoid concurrent file writes. Existing records, including legacy records, are preserved during status updates.

The form explains local storage, includes a suggested evidence-preservation checklist, validates uploads and asks for consent before saving. A returned reference confirms that the in-app record was saved; no complaint or evidence is automatically sent to law enforcement, a government agency, or any external reporting service. Users must submit separately through the country-selected official reporting link. The listed services are the [Australian Cyber Security Centre ReportCyber service](https://www.cyber.gov.au/report-and-recover/report), [Canadian Anti-Fraud Centre](https://reportcyberandfraud.canada.ca/), [UK Report Fraud](https://www.reportfraud.police.uk/), [India National Cyber Crime Reporting Portal](https://cybercrime.gov.in/), and [FBI Internet Crime Complaint Center](https://www.ic3.gov/). Links are provided from the project’s configured government/law-enforcement source list; verify current jurisdiction and instructions on the linked official site.

The form also offers an optional **Request a Callback** choice. When selected,
the reporter provides a callback number, preferred local date/time and UTC
offset, and separate consent for phone contact. The callback details are saved privately
with the complaint, and the response confirms whether that request was saved.
There is no call-placement or callback scheduling service connected; the app
does not place a call and does not promise that a person will call.

After submission, VoiceShield suggests safety steps based on the incident
category and whether money was lost. These are general guidance, not a substitute
for contacting a bank, account provider, local emergency service, or official
reporting authority. The app does not provide agency submission, human report
review, callback follow-up, evidence retrieval, account management, or an
in-app report deletion workflow. Existing reports are local JSONL data and must
be protected with operating-system access controls and appropriate backups.

## Run locally (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
python backend\scripts\download_aasist.py
Copy-Item .env.example .env
uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. For Bash/macOS/Linux, create and activate a virtual environment with `python3 -m venv .venv` and `source .venv/bin/activate`, then use the same pip and uvicorn commands.

On Windows, you can instead double-click [start.bat](./start.bat). It creates the Python 3.12 virtual environment when missing, installs dependencies, downloads the pinned pretrained AASIST model on first run, opens the browser, and starts the local server. The model download requires an internet connection. To download it separately, run `.\.venv\Scripts\python.exe backend\scripts\download_aasist.py`.

The default detector is the AASIST model pretrained on ASVspoof 2019 Logical Access, distributed in ONNX format by [SpeechAntiSpoofingBenchmarks](https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST) under the MIT license. Longer clips are scored in deterministic overlapping windows; shorter clips are repeated to the model's fixed input length. The detector follows the upstream score polarity: a higher score favors bona fide speech, while a lower score favors spoofed speech. The result explanation shows this score direction, the raw score, and why a confident category is or is not available. A risk percentage is intentionally shown as unavailable until a calibration method is trained and validated; the raw score must not be converted directly into a percent. Do not flip score direction or change thresholds without labeled validation data. Because this project has no locally measured validation set, the default UI reports **Can’t tell** for model-scored clips while still displaying the raw score; an unvalidated negative score must not be treated as a suspicious-voice finding. Only set `VOICESHIELD_SCREENING_LABELS_VALIDATED=true` in `.env` after validating the deployed model and threshold on representative, labeled, held-out recordings. The result is a model score, not a probability, identity check, or guarantee. Published benchmark results vary substantially by dataset: the model card reports an equal error rate of 0.83% on ASVspoof 2019 LA and 43.01% on InTheWild. These are source-reported results, not measurements of this app or a promise of performance on WhatsApp recordings.

If no AASIST model is present, the app can use an optional locally trained LFCC/scikit-learn model from `backend\models\lfcc_baseline.joblib`; without either model it returns an **inconclusive demo response** and explicitly says no authenticity analysis occurred. The upload, playback, preprocessing, API response, and error handling work without model weights.

## Phase 2 robustness and partial-fake evaluation

The upload screen also has a **Try 500 ms live mic demo**. It streams microphone PCM to the local server and shows a rolling microphone waveform plus the model score trend. The waveform follows live microphone input; the score updates every 500 ms after a 4.04-second model window fills. Scores are raw model outputs, not probabilities. This is a demo of streaming speech inference, **not** a cellular-call audio feed. It is not evidence that calls can be monitored, and does not meet the phone-call portion of the challenge.

The Android app also has an optional **speakerphone microphone experiment**.
This is not direct cellular-call audio: the user must enable speakerphone,
manually grant microphone permission and consent for each session, keep the app
in the foreground, and configure a reachable HTTPS/WSS backend with a
`VOICESHIELD_CALL_STREAM_TOKEN`. Audio is streamed to that backend but is not
saved by the app; the experiment stops when the app leaves the foreground.
The dedicated call-audio validation switch,
`VOICESHIELD_CALL_AUDIO_LABELS_VALIDATED`, defaults to `false`, keeping output
at **Can’t tell**. Do not enable caller labels until call-specific validation
has been performed on representative labeled data. Microphone capture may be
limited by Android and includes room/speakerphone audio; it cannot prove that a
caller is real or fake. See [mobile setup](./frontend/mobile/README.md) for
secure deployment and installation steps. No Android device/backend endpoint
is configured or connected automatically.

To run the robustness evaluation, prepare a local manifest containing untouched, labeled evaluation clips and run:

```powershell
python backend\scripts\evaluate_phase2.py C:\path\to\manifest.csv --partition eval
```

The script evaluates clean clips, 8 kHz telephone-band/resampling, actual in-memory Opus and AAC encode/decode round trips, Gaussian noise at 20 dB and 10 dB SNR, and combined telephone-band/codec/10 dB noise conditions. It writes EER, fixed-zero-threshold errors, score CSVs, and per-condition EER ratios against clean. The 2.5x target is **reported, not claimed or tuned into the result**. Keep evaluation sources and partitions separate, and measure on held-out data; synthetic codec/noise transformations are not real telephone networks.

For a seeded experiment creating 100 partial-fake composites and scoring localization frames:

```powershell
python backend\scripts\evaluate_partial_fakes.py C:\path\to\manifest.csv --partition eval --count 100 --seed 2026
```

The manifest needs separate genuine and spoof clips in the selected partition; source audio must be authorized and held out from training. Generated WAVs are written to ignored `backend/data/phase2_partial_fakes/`, with segment labels, aggregate/per-clip 0.5-second frame F1, and frame-level scores in `backend/results/`. The frame hop is 0.5 seconds, but the pretrained AASIST model's receptive field remains about 4.04 seconds, so this is **not true 0.5-second localization**. Report that limitation and do not present these generated composites as natural calls or an independent benchmark.

No labeled development/evaluation manifest is currently included. Accordingly, no Phase 2 EER, 2.5x robustness result, or partial-fake F1 has been measured yet. Phase 1 stays on the same frozen AASIST weights and decision threshold; any claim that EER is within 2 points must be backed by measured Phase 1 and Phase 2 reports on the agreed split.

## Optional LFCC baseline

Prepare a local CSV manifest with columns `path,label,source,partition`. Use `bona_fide`/`spoof` labels and put training examples in the `train` partition. Keep paths and data local; the CSV may use paths relative to its own directory.

```powershell
python backend\scripts\train_baseline.py C:\path\to\manifest.csv
python backend\scripts\evaluate.py C:\path\to\manifest.csv --partition dev
python backend\scripts\evaluate.py C:\path\to\manifest.csv --partition dev --phone-stress
```

The baseline extracts mean/std LFCC summaries and fits a balanced scikit-learn logistic regression. Training uses only rows in the selected training partition; the evaluator scores at the fixed zero decision threshold and does not tune on the evaluated data. It prints counts, balanced accuracy, EER, and false-positive/false-negative counts separately by source, partition, and audio condition. `--phone-stress` adds a distinct **simulated** 300–3400 Hz band-limit/8 kHz resampling condition; it is not a real call recording or a codec/network simulation. Evaluation writes `backend/results/metrics.json`, shown on the performance page with a JSON download link. Use `--model` to evaluate another local model path and `--output` to write a test report elsewhere. This baseline is a demo, not a validated detector; reliable conclusions require suitable untouched data and domain-specific evaluation.

## Configuration

Copy `.env.example` to `.env` if needed. `VOICESHIELD_AASIST_MODEL_PATH` selects the AASIST ONNX file; `VOICESHIELD_MODEL_PATH` selects the optional LFCC model. Relative model paths are resolved from `backend`. `VOICESHIELD_MAX_UPLOAD_MB` controls the request size limit; `VOICESHIELD_INCONCLUSIVE_MARGIN` controls the inconclusive score margin. Keep model weights and datasets out of source control.

## Smoke checks

1. Start the server and visit `/`; confirm the interface explains local processing and limits input to WAV/FLAC/OGG.
2. With `backend/models/aasist.onnx` installed, upload a decodable 2–10 second clip; confirm the player, duration, AASIST model ID, and screening result appear. Without model files, the expected response is inconclusive with the demo warning.
3. Try a clip shorter than 2 seconds and one longer than 10 seconds; each should show a clear validation message and no result.
4. Try a corrupt file or an unsupported format such as MP3; confirm a clear decode/type error.
5. With the downloaded AASIST model, repeat step 2 and confirm the model ID and raw decision score are shown as **not a probability**.
6. Restart the server and verify no uploaded clip was written to disk.

## Additional demo features

- A call safety checklist recommends hanging up, calling back on a trusted number, and never sharing money or verification codes based only on a voice call.
- Audio quality heuristics warn about clipping, quiet/silent audio, and possible background noise. When severe clipping or too little speech-like activity is detected, the result is forced to inconclusive. These checks are approximate and do not reliably separate speech from noise.
- The default AASIST detector is a pretrained audio anti-spoofing model; the optional LFCC baseline can be trained on authorized local data. Neither identifies a speaker or proves authenticity.
- Model results include a three-category screening signal graph. The graph is categorical, not a calibrated probability.
- The web page displays a “Call detection off” indicator and explains that the native Android/iOS starter code is not an installed or active call-screening feature.
- The Android call-screening APK is available from the web app’s Call detection section and can warn when a caller number is not in device contacts after installation and role approval. Its local history can be cleared in the app. iOS source scaffolding can identify numbers manually added to a local list and still needs to be built in Xcode. Neither feature analyzes live call audio or determines whether a voice is cloned; details are in [frontend/mobile/README.md](./frontend/mobile/README.md).
- “Delete & reset” clears the selected clip and playback from the page. The server processes upload bytes in memory and does not write them to disk.
- To enable one-tap examples, provide permitted local `backend/data/demo/genuine.wav` and `backend/data/demo/synthetic.wav` files. They are ignored by git. Their example labels describe the provided samples, not the model's prediction.
- The performance panel displays the locally generated `backend/results/metrics.json`, with source and partition separated. Run the evaluator on labeled local data to create/update it; without such a report the page states that no results are available.

## Evaluation status and limitations

No benchmark corpus or labeled test manifest is included. The AASIST model is pretrained elsewhere; this app has not been independently evaluated. ASVspoof LA development and In-the-Wild results must be reported separately; neither dataset has been downloaded or scored here. The returned label cannot prove identity or guarantee authenticity.
