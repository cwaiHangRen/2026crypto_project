# v2 media adapters and attack lab

`gm_provenance.media_adapters` provides one boundary for image, PDF and offline WAV. It returns `MediaOperation` containing original/final SHA-256, ContentID, watermark recovery, media details and the independent Manifest/signature/version/revocation slots. The image and PDF paths call the existing v1 watermark; PDF pages use deterministic PDFium rasterization. WAV uses an offline PCM16 LSB carrier for smoke demonstrations. MP3/FLAC/MP4 return `available=false` with an explicit optional-codec reason instead of pretending to verify.

```powershell
$env:PYTHONPATH = "src"
python scripts/run_v2_attack_matrix.py marked.png --content-id 0123456789abcdef0123456789abcdef --output results/v2-attack
```

The attack runner writes `attack-matrix.json`, `attack-matrix.csv`, `summary.json` and attacked bytes under `samples/`. Cases are deterministic and include JPEG/PNG conversion, resize, crop, rotation, mirror, screenshot surrogate, metadata removal, local replacement, PDF rasterization/page deletion/reordering/content replacement, and WAV resample/transcode/volume/crop. Missing system codecs are reported as `UNAVAILABLE` and remain visible in the matrix.
