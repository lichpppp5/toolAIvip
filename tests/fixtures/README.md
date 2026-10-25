# LiveGuard Test Fixtures

This directory holds static test assets used across unit and integration tests.

## Files

| File | Description |
|---|---|
| `sample_5s.mp4` | 5-second 1280×720 synthetic test video (H.264 + AAC) |
| `make_fixture.sh` | Script to generate `sample_5s.mp4` via FFmpeg |

## Generating the fixture

Run `make_fixture.sh` from the repository root (requires FFmpeg):

```bash
bash tests/fixtures/make_fixture.sh
```

The script uses `lavfi` sources (no internet required, no copyrighted material):
- **Video**: `testsrc` — colour-bar test pattern, 1280×720, 30 fps, 5 seconds.
- **Audio**: `sine` — 440 Hz sine wave, 5 seconds.
- **Codec**: H.264 (yuv420p) + AAC, shortest-stream trimming.

## Golden-file contract

Tests that reference `sample_5s.mp4` must call `pytest.importorskip` or
skip gracefully when the file is absent (CI generates it via `make_fixture.sh`):

```python
import pytest
SAMPLE = Path("tests/fixtures/sample_5s.mp4")

@pytest.mark.skipif(not SAMPLE.exists(), reason="Fixture not generated; run make_fixture.sh")
def test_m1_on_real_video():
    ...
```

## Adding new fixtures

1. Add generation logic to `make_fixture.sh`.
2. Document the new file in this README.
3. Never commit real personal videos — synthetic only.
