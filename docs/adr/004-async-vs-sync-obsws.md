# ADR 004: Async vs Sync Execution for obs-websocket Client

## Status
Accepted (Phase 3) — To be expanded in Phase 7.

## Context
The LiveGuard pipeline is natively asynchronous (`asyncio`), powering continuous stream health monitoring, metrics, and cooperative multitasking. However, the official Python SDK for obs-websocket v5 (`obsws-python`) provides synchronous, blocking network calls through its `ReqClient`.

Directly executing synchronous network calls on the main asyncio event loop would block background tasks (such as heartbeat telemetry, stream state monitoring, and signal handlers).

## Decision
We execute all synchronous `obsws-python` client calls (`create_input`, `start_stream`, `get_stream_status`, etc.) inside thread workers using Python's `asyncio.to_thread()`.

This maintains:
1. **Thread safety**: Every OBS RPC operates in isolation without freezing asyncio cooperative scheduling.
2. **Cancellation and timeout support**: `asyncio.wait_for` can cleanly wrap thread tasks.
3. **API Consistency**: The `M4OBSOrchestrator` implements the pure `async` `OBSOrchestrator` Protocol defined in `liveguard.interfaces`.

## Consequences
- Requires thread pool management handled transparently by Python's default thread pool executor.
- Clean separation between async business logic (circuit breakers, jittered retries, timers) and synchronous WebSocket I/O.
