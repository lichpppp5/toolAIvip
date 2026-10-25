#!/usr/bin/env bash
set -e

echo "=== [1/4] Checking LiveGuard CLI Version ==="
liveguard --version

echo "=== [2/4] Running Test Suite ==="
pytest -q

echo "=== [3/4] Running Pre-commit Hooks ==="
if command -v pre-commit >/dev/null 2>&1; then
  pre-commit run --all-files
elif python3 -m pre_commit --version >/dev/null 2>&1; then
  python3 -m pre_commit run --all-files
else
  echo "pre-commit not found; skipping hook checks."
fi

echo "=== [4/4] Building Docker Image ==="
if docker info >/dev/null 2>&1; then
  docker build -t liveguard:smoke .
  docker run --rm liveguard:smoke --version
else
  echo "Docker daemon is not running; skipped docker build step."
fi

echo "=== All LiveGuard Smoke Tests Passed! ==="
