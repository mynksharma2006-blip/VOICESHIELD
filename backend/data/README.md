# Local evaluation data

Keep audio corpora and manifests local; they are excluded from version control. Follow the applicable dataset terms and do not put benchmark audio into the public demo.

Use a CSV manifest with headers `path,label,source,partition`. Labels must be `bona_fide` or `spoof`; paths may be absolute or relative to the CSV. For baseline training, rows in partition `train` are used only. For evaluation, run one partition at a time. Keep ASVspoof LA development and In-the-Wild entries in separate `source` groups; the evaluator prints those groups separately. It does not pick or tune a threshold from evaluation scores.

Upload accepts clips from 2 to 10 seconds; the current training and evaluation scripts use the same bounds. Prepare authorized, locally available clips accordingly.

For the Phase 2 robustness and partial-fake scripts, use a held-out, labeled
evaluation partition with both genuine and spoof source clips. The Phase 2
robustness report simulates 8 kHz telephone-band conversion, Opus/AAC
encode-decode, and stated Gaussian SNR levels; it is not a real call-network
measurement. The partial-fake script creates deterministic crossfaded splices
from the supplied source clips. Keep all generated audio and manifests local,
respect dataset terms, and do not use derived splices as independent benchmark
recordings.
