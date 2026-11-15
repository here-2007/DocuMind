"""Read-only SSE projection of the durable query event ledger."""

import asyncio
import json
from time import monotonic

from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from documind.errors import AppError
from documind.queries import events

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 10


def event_cursor(after, last_event_id):
    values = []
    for value in (after, last_event_id):
        if value is not None:
            if not value.isascii() or not value.isdecimal() or len(value) > 10:
                raise AppError("INVALID_EVENT_CURSOR", 400)
            values.append(int(value))
    if len(set(values)) > 1:
        raise AppError("INVALID_EVENT_CURSOR", 400)
    return values[0] if values else 0


def encode_event(identifier, row, result_url):
    seq, kind, payload = row["seq"], row["event_type"], row["payload"]
    data = {"query_run_id": str(identifier), "seq": seq}
    if kind in {"answer", "error"}:
        data.update(payload)
        data["result_url"] = result_url
    else:
        data.update(type=kind, data=payload, occurred_at=row["occurred_at"].isoformat())
        kind = "trace_event"
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data)}\n\n"


def accepts_stream(header):
    weights = {}
    for item in header.split(","):
        media, *parameters = item.split(";")
        quality = 1.0
        for parameter in parameters:
            key, _, value = parameter.strip().partition("=")
            if key.lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if 0 <= quality <= 1:
            name = media.strip().lower()
            weights[name] = max(quality, weights.get(name, 0.0))
    stream = weights.get("text/event-stream", 0.0)
    return stream > 0 and stream >= weights.get("application/json", 0.0)


def expired_events(request, result_url):
    return JSONResponse(
        {
            "error": {
                "code": "EVENTS_EXPIRED",
                "message": "Events expired",
                "request_id": request.state.request_id,
                "details": {"result_url": result_url},
            }
        },
        status_code=410,
        headers={"Link": f'<{result_url}>; rel="result"'},
    )


async def query_stream(request, repo, workspace, identifier, after, limit):
    result_url = f"/api/v1/workspaces/{workspace}/queries/{identifier}"

    async def load(cursor):
        return await run_in_threadpool(
            events, repo, workspace, identifier, cursor, limit, stream=True
        )

    first = await load(after)
    if first == ([], True):
        return Response(status_code=204)

    async def frames():
        cursor = after
        last_frame = monotonic()
        while True:
            try:
                items, finished = await load(cursor)
            except AppError as exc:
                body = {"code": exc.code, "result_url": result_url}
                yield f"event: stream_error\ndata: {json.dumps(body)}\n\n"
                return
            if items:
                # Read each bounded batch immediately before projection.
                yield "".join(encode_event(identifier, row, result_url) for row in items)
                cursor = items[-1]["seq"]
                last_frame = monotonic()
            if finished or await request.is_disconnected():
                return
            if not items:
                await asyncio.sleep(POLL_SECONDS)
                if monotonic() - last_frame >= HEARTBEAT_SECONDS:
                    yield ": heartbeat\n\n"
                    last_frame = monotonic()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
