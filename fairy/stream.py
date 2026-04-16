import asyncio
import io
import json
import queue
import threading
from collections.abc import AsyncGenerator

from sprites import ExecError, Sprite


_SENTINEL = object()


class QueueWriter(io.RawIOBase):
    """A writable BinaryIO that puts chunks into a queue.

    Assigned to cmd.stdout/cmd.stderr so sprites-py writes here in real-time.
    """

    def __init__(self, q: queue.Queue):
        self._queue = q

    def writable(self) -> bool:
        return True

    def write(self, b: bytes | bytearray) -> int:
        data = bytes(b)
        self._queue.put(data)
        return len(data)


async def stream_agent_output(
    sprite: Sprite,
    timeout: float,
) -> AsyncGenerator[str, None]:
    """Run agent in a background thread, yield SSE event strings as output arrives.

    Each yielded string is a JSON object:
    - {"type": "output", "stream": "stdout", "data": "..."}
    - {"type": "output", "stream": "stderr", "data": "..."}
    - {"type": "exit", "code": 0}
    - {"type": "error", "message": "..."}
    """
    output_q: queue.Queue = queue.Queue(maxsize=4096)
    result_holder: list = []

    def _run_in_thread():
        try:
            cmd = sprite.command("bash", "/run-agent.sh", timeout=timeout)
            cmd.stdout = QueueWriter(output_q)
            cmd.stderr = QueueWriter(output_q)
            cmd.run()
            result_holder.append(("exit", 0))
        except ExecError as e:
            result_holder.append(("exit", e.exit_code))
        except Exception as e:
            result_holder.append(("error", str(e)))
        finally:
            output_q.put(_SENTINEL)

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    loop = asyncio.get_running_loop()

    while True:
        # Read from queue without blocking the event loop
        chunk = await loop.run_in_executor(None, output_q.get)
        if chunk is _SENTINEL:
            break
        yield json.dumps({
            "type": "output",
            "data": chunk.decode("utf-8", errors="replace"),
        })

    thread.join(timeout=5.0)

    if result_holder:
        kind, value = result_holder[0]
        if kind == "exit":
            yield json.dumps({"type": "exit", "code": value})
        else:
            yield json.dumps({"type": "error", "message": value})
    else:
        yield json.dumps({"type": "error", "message": "Agent thread did not complete"})
