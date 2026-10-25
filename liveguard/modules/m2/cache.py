"""CLIP ViT-B/32 model weights cache helper for M2.

This standalone script downloads CLIP ViT-B/32 model weights once to a
local cache directory. By design, M2 refuses to auto-download models at
runtime to preserve isolation and predictability.

Usage::

    $ python -m liveguard.modules.m2.cache
    $ python -m liveguard.modules.m2.cache --cache-dir ~/.cache/liveguard/models
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def download_clip_cache(
    cache_dir: Path | str,
    interactive: bool = True,
    force: bool = False,
) -> Path:
    """Download CLIP ViT-B/32 weights to the specified cache directory.

    Guarded by an interactive confirmation prompt when *interactive* is True.

    Args:
        cache_dir: Target directory path for storing downloaded weights.
        interactive: If True, prompt user on stdin before initiating download.
        force: If True, bypass interactive confirmation prompt.

    Returns:
        The resolved Path to the cache directory.

    Raises:
        RuntimeError: If user declines the download prompt or download fails.
        ImportError: If ``open_clip`` is not installed.

    Example::

        cache_path = download_clip_cache(Path("/tmp/models"), interactive=False)
    """
    target_dir = Path(cache_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    model_name = "ViT-B-32"
    pretrained_tag = "laion2b_s34b_b79k"

    print("=" * 64)
    print("LiveGuard M2 — CLIP Model Weight Pre-Cache Helper")
    print("=" * 64)
    print(f"Model: {model_name} ({pretrained_tag})")
    print(f"Target Cache Directory: {target_dir}")
    print("Note: Weights are ~600MB. Auto-download during streaming is forbidden.")
    print("=" * 64)

    if interactive and not force:
        try:
            answer = input("Proceed with download? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nDownload aborted by user.")
            raise RuntimeError("Download aborted by user.") from None

        if answer not in ("y", "yes"):
            print("Download cancelled.")
            raise RuntimeError("Download declined by user.")

    print(f"Fetching weights into {target_dir}...")
    try:
        import open_clip

        open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained_tag,
            cache_dir=str(target_dir),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to download CLIP weights: {exc}", file=sys.stderr)
        raise RuntimeError(f"Failed to cache weights: {exc}") from exc

    print(f"SUCCESS: CLIP ViT-B/32 cached successfully at {target_dir}")
    return target_dir


def main() -> None:
    """CLI entrypoint for ``python -m liveguard.modules.m2.cache``."""
    parser = argparse.ArgumentParser(
        description="Download CLIP ViT-B/32 model weights for LiveGuard M2."
    )
    parser.add_argument(
        "--cache-dir",
        default="~/.cache/liveguard/models",
        help="Target directory for model cache (default: ~/.cache/liveguard/models).",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip interactive confirmation prompt.",
    )
    args = parser.parse_args()

    try:
        download_clip_cache(
            cache_dir=args.cache_dir,
            interactive=not args.yes,
            force=args.yes,
        )
    except RuntimeError:
        sys.exit(1)


if __name__ == "__main__":
    main()
