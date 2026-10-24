# Local models

Model files are ignored by git. The default detector is the pretrained AASIST ONNX model.
Download the pinned, SHA-256-verified model with:

```powershell
python backend\scripts\download_aasist.py
```

The weights are from [SpeechAntiSpoofingBenchmarks/AASIST](https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST), based on the official AASIST model pretrained on ASVspoof 2019 LA, and are distributed under the MIT license. The download script pins a repository revision and checks the model's SHA-256 digest.

The optional LFCC/scikit-learn bundle is loaded from `backend\models\lfcc_baseline.joblib` only when the AASIST model is absent. Set `VOICESHIELD_AASIST_MODEL_PATH` or `VOICESHIELD_MODEL_PATH` to select alternate local files. Relative model paths are resolved from `backend`. The baseline can be trained with `python backend\scripts\train_baseline.py <manifest.csv>` after preparing authorized labeled training data.
