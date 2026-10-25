# -----------------------------------------------------------------------------
# Stage 1: Build wheel and compile dependencies
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libexiftool-perl \
    ffmpeg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY liveguard ./liveguard

RUN pip install --no-cache-dir build \
 && python -m build --wheel --no-isolation

# -----------------------------------------------------------------------------
# Stage 2: Final runtime container
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Security: non-root user
RUN groupadd --gid 1000 liveguard \
 && useradd --uid 1000 --gid liveguard --shell /bin/bash --create-home liveguard

# System runtime dependencies (ffmpeg, exiftool, node 20 for M3 bridge)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libexiftool-perl \
    curl \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy built wheel from builder stage and install with dashboard dependencies
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl[dashboard] \
 && rm -rf /tmp/*.whl

# Prepare persistence directories with non-root ownership
RUN mkdir -p /app/secrets /app/audit /app/logs /app/runtime \
 && chown -R liveguard:liveguard /app

COPY --chown=liveguard:liveguard liveguard.yaml /app/liveguard.yaml
COPY --chown=liveguard:liveguard node /app/node

USER liveguard

EXPOSE 8501 8080 9090

ENTRYPOINT ["liveguard"]
CMD ["--help"]
