"""Unit tests for M2 model cache helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from liveguard.modules.m2.cache import download_clip_cache


def test_download_clip_cache_aborts_on_user_rejection(tmp_path: Path):
    """User typing 'no' declines download and raises RuntimeError."""
    with patch("builtins.input", return_value="n"):
        with pytest.raises(RuntimeError) as exc_info:
            download_clip_cache(tmp_path / "cache", interactive=True)
        assert (
            "declined" in str(exc_info.value).lower()
            or "aborted" in str(exc_info.value).lower()
        )


def test_download_clip_cache_succeeds_with_force(tmp_path: Path):
    """With force=True, interactive prompt is bypassed and open_clip is called."""
    with patch("open_clip.create_model_and_transforms") as mock_open_clip:
        mock_open_clip.return_value = (MagicMock(), None, MagicMock())
        result_dir = download_clip_cache(
            tmp_path / "cache", interactive=False, force=True
        )
        assert result_dir.exists()
        mock_open_clip.assert_called_once()
