# M1 — Privacy Normalizer: Usage Example

This document shows a realistic end-to-end run of the M1 Privacy Normalizer
with a sample audit report JSON output.

---

## 1. Generate the test fixture (once)

```bash
bash tests/fixtures/make_fixture.sh
# → tests/fixtures/sample_5s.mp4
```

---

## 2. Run `liveguard harden`

```bash
liveguard harden tests/fixtures/sample_5s.mp4 \
  --output output/clean_5s.mp4 \
  --report output/m1_report.json \
  --crf 22 \
  --preset fast \
  --noise 4 \
  --comment "Stream prep session"
```

Expected terminal output:

```
✅ Normalisation complete → output/clean_5s.mp4
Report written to: output/m1_report.json
```

---

## 3. Sample audit report (all timestamps redacted for documentation)

```json
{
  "liveguard_version": "0.1.0",
  "module": "M1",
  "input_path": "/home/creator/videos/sample_5s.mp4",
  "output_path": "/home/creator/output/clean_5s.mp4",
  "input_hash": "REDACTED-FOR-EXAMPLE-a3f8d2c1b7e94f0512ab3d89c4e7f06a",
  "output_hash": "REDACTED-FOR-EXAMPLE-d1c9e2f3a4b5067891abcdef234567ef",
  "duration_seconds": 5.013,
  "c2pa_before": false,
  "c2pa_after": false,
  "c2pa_preserved": true,
  "tags_removed": [
    "GPS:GPSLatitude",
    "GPS:GPSLongitude",
    "GPS:GPSAltitude",
    "EXIF:Make",
    "EXIF:Model",
    "EXIF:SerialNumber",
    "EXIF:CameraSerialNumber"
  ],
  "tags_injected": {
    "Software": "LiveGuard/1.0",
    "DateTimeOriginal": "REDACTED-FOR-EXAMPLE",
    "Comment": "Stream prep session"
  },
  "noise_strength": 4,
  "audio_normalized": true,
  "ffmpeg_log_path": "/tmp/liveguard_XXXXXXX_ffmpeg.log",
  "completed_at": "REDACTED-FOR-EXAMPLE"
}
```

---

## 4. C2PA preservation path

If the input contains C2PA provenance and the codec preserves it:

```json
{
  "c2pa_before": true,
  "c2pa_after": true,
  "c2pa_preserved": true
}
```

If the codec drops C2PA (and `--strip-provenance` was NOT set), a warning
is written to stderr:

```json
{
  "liveguard_audit": true,
  "event": "c2pa_dropped_by_codec",
  "input": "/path/to/raw.mp4",
  "output": "/path/to/clean.mp4",
  "note": "C2PA provenance was present in input but lost during re-encoding. Consider using a C2PA-aware codec pipeline.",
  "timestamp": "REDACTED-FOR-EXAMPLE"
}
```

---

## 5. Strip-provenance path (requires explicit acknowledgement)

```bash
liveguard harden raw.mp4 --output clean.mp4 \
  --strip-provenance \
  --i-understand-legal-risk
```

The CLI prints a 3-second warning before executing. An audit event is emitted:

```json
{
  "liveguard_audit": true,
  "event": "provenance_stripped",
  "input": "/path/to/raw.mp4",
  "output": "/path/to/clean.mp4",
  "acknowledged": true,
  "timestamp": "REDACTED-FOR-EXAMPLE"
}
```

Without `--i-understand-legal-risk`, the command exits with code **3**
(compliance violation):

```
[liveguard] ERROR (exit 3): --strip-provenance requires --i-understand-legal-risk...
```

---

## 6. Missing dependency path

If `ffmpeg` is not on PATH:

```
[liveguard] ERROR (exit 5): Required tool 'ffmpeg' not found on PATH.
```

Install FFmpeg 6.1+ and ensure it is on `PATH` before running LiveGuard.
