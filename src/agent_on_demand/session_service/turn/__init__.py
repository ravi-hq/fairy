"""Per-turn execution: enqueue, argv builder, and outcome resolution.

The Procrastinate task body itself lives in `session_service.tasks`; this
package holds the small pure helpers around it. `run_turn` defers a turn
onto the worker, `build_turn_argv` composes the bash-shim argv handed to
the backend, and `compute_final_status` resolves the worker thread's
result tuple into a final session status.
"""

from .argv import build_turn_argv
from .enqueue import run_turn
from .outcome import compute_final_status

__all__ = [
    "build_turn_argv",
    "compute_final_status",
    "run_turn",
]
