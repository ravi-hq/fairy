"""Producer-consumer plumbing for log chunks emitted during a turn.

The runtime's blocking `cmd.run()` (in the daemon worker thread) writes
stdout/stderr bytes into `TaggingQueueWriter` instances. The main task
thread drains the queue, batches into `AgentSessionLog` rows, and
bulk-creates them with retry-and-backoff.

Extracted from `tasks.py` so the threading/queue/flush orchestration can
be unit-tested without the procrastinate decorator dance (mutmut's hammett
runner can't load the decorators).
"""

from __future__ import annotations

import io
import logging
import queue
import time
from typing import TYPE_CHECKING

from django.db import close_old_connections

from agent_on_demand.analytics import capture as posthog_capture
from agent_on_demand.models import AgentSession, AgentSessionLog, SessionTurn

if TYPE_CHECKING:
    from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

_SENTINEL = object()
FLUSH_SIZE = 20
BULK_CREATE_DELAYS = (0.1, 0.3, 1.0)
QUEUE_MAXSIZE = 4096
QUEUE_GET_TIMEOUT = 1.0
WRITE_PUT_TIMEOUT = 5.0


class TaggedChunk:
    """A chunk of output tagged with its stream name."""

    __slots__ = ("stream", "data")

    def __init__(self, stream: str, data: bytes):
        self.stream = stream
        self.data = data


class TaggingQueueWriter(io.RawIOBase):
    """A writable BinaryIO that puts tagged chunks into a queue.

    Each chunk is tagged with the stream name (stdout/stderr) so downstream
    consumers can distinguish them.
    """

    def __init__(self, q: queue.Queue, stream: str):
        self._queue = q
        self._stream = stream
        self.drop_count = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        try:
            self._queue.put(TaggedChunk(self._stream, data), timeout=WRITE_PUT_TIMEOUT)
        except queue.Full:
            self.drop_count += 1
            if self.drop_count == 1:
                logger.warning("TaggingQueueWriter: output queue full, dropping chunks")
        return len(data)


class LogChunkSink:
    """Owns the queue, the per-stream writers, and the drain/flush loop.

    Lifecycle:
      1. Construct with the session + turn that owns the chunks.
      2. The runtime's worker thread writes into `stdout_writer` /
         `stderr_writer` and calls `put_sentinel()` in `finally`.
      3. The main task thread calls `drain()` to consume the queue,
         batch into `AgentSessionLog` rows, and persist with retry.
      4. After the worker thread is joined, call `report_drops()` to
         emit the posthog event for any dropped chunks.
    """

    def __init__(
        self,
        session: AgentSession,
        turn: SessionTurn,
        span: Span | None = None,
    ):
        self._session = session
        self._turn = turn
        self._span = span
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self._buffer: list[AgentSessionLog] = []
        self.stdout_writer = TaggingQueueWriter(self._queue, "stdout")
        self.stderr_writer = TaggingQueueWriter(self._queue, "stderr")

    def put_sentinel(self) -> None:
        """Signal the drain loop that the producer is done."""
        self._queue.put(_SENTINEL)

    def drain(self) -> None:
        """Block until the sentinel arrives, batching chunks into the DB.

        Empty-queue timeouts trigger a partial flush so chunks don't sit
        in the buffer indefinitely when the runtime is quiet.
        """
        while True:
            try:
                chunk = self._queue.get(timeout=QUEUE_GET_TIMEOUT)
            except queue.Empty:
                self._flush_buffer()
                continue
            if chunk is _SENTINEL:
                break
            self._buffer.append(
                AgentSessionLog(
                    session=self._session,
                    turn=self._turn,
                    stream=chunk.stream,
                    data=chunk.data.decode("utf-8", errors="replace"),
                )
            )
            self._record_chunk_event(chunk)
            if len(self._buffer) >= FLUSH_SIZE:
                self._flush_buffer()
        self._flush_buffer()

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        for attempt, delay in enumerate(BULK_CREATE_DELAYS, 1):
            try:
                AgentSessionLog.objects.bulk_create(self._buffer)
                self._buffer.clear()
                return
            except Exception:
                if attempt == len(BULK_CREATE_DELAYS):
                    logger.exception("bulk_create exhausted retries")
                    posthog_capture(
                        self._session.user,
                        "session.log_write_retry_exhausted",
                        properties={
                            "session_id": str(self._session.id),
                            "turn_number": self._turn.turn_number,
                            "runtime": self._session.runtime,
                            "dropped_chunks": len(self._buffer),
                        },
                    )
                    raise
                close_old_connections()
                time.sleep(delay)

    def _record_chunk_event(self, chunk: TaggedChunk) -> None:
        """Mark each chunk as a span event so the trace timeline shows when
        the runtime produced output. Without this, only the periodic INSERT
        statements appear inside the turn span and the gaps between them
        (model thinking + tool use) are invisible."""
        if self._span is None:
            return
        self._span.add_event(
            "runtime.output",
            attributes={
                "aod.stream": chunk.stream,
                "aod.bytes": len(chunk.data),
            },
        )

    @property
    def total_drop_count(self) -> int:
        return self.stdout_writer.drop_count + self.stderr_writer.drop_count

    def report_drops(self) -> None:
        """Emit the posthog event if any chunks were dropped during the turn."""
        total = self.total_drop_count
        if total <= 0:
            return
        posthog_capture(
            self._session.user,
            "session.output_chunks_dropped",
            properties={
                "session_id": str(self._session.id),
                "turn_number": self._turn.turn_number,
                "runtime": self._session.runtime,
                "dropped_count": total,
            },
        )
