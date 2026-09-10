"""Long jobs, run in this process, with their output kept for the page.

Everything JobDesk does that takes more than an instant -- a radar sweep, a
packet build, an ATS check -- used to be a command you typed. The app runs the
same code in a background thread and keeps every line it printed, so the page
can show a console instead of telling you to go open one.

Three decisions worth stating.

**In-process, not a subprocess.** `radar.main.run()` is a function; calling it
is cheaper and more honest than shelling out to `py -m jobdesk.radar.main` and
scraping the pipe. The output is captured by pointing `sys.stdout` at the run's
buffer for the duration, which is exactly what those modules print to.

**One at a time.** `redirect_stdout` is process-global, so two runs at once
would braid their output together. That restriction is the right behaviour
anyway: two radar sweeps or two packet builds racing each other write the same
JSON files. Runs queue and execute in order.

**Nothing here submits anything.** A run calls a function this project already
had, and every one of those ends at a folder on disk.
"""

from __future__ import annotations

import io
import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

# How many finished runs to keep. The console is a place to read what just
# happened, not an archive; the packets and the application log are the record.
KEEP = 40

Emit = Callable[[str], None]


@dataclass
class Run:
    id: str
    label: str
    kind: str
    started: str
    state: str = "queued"          # queued | running | done | failed
    finished: str = ""
    lines: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str = ""

    def head(self) -> dict:
        """The run without its output, for the list of runs."""
        return {"id": self.id, "label": self.label, "kind": self.kind,
                "started": self.started, "finished": self.finished,
                "state": self.state, "error": self.error,
                "lines": len(self.lines), "result": self.result}


class _Buffer(io.TextIOBase):
    """A file object that appends whole lines to a run.

    The modules being captured print with `print()`, which writes the text and
    the newline as two separate calls, so partial writes have to be held until
    a newline arrives or the last word of every line would be its own entry.
    """

    def __init__(self, run: Run) -> None:
        self.run = run
        self._partial = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._partial += text
        while "\n" in self._partial:
            line, _, self._partial = self._partial.partition("\n")
            self.run.lines.append(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._partial.strip():
            self.run.lines.append(self._partial.rstrip())
        self._partial = ""


_runs: dict[str, Run] = {}
_order: list[str] = []
_lock = threading.Lock()
_work: "queue.Queue[tuple[Run, Callable[[Emit], dict]]]" = queue.Queue()
_worker: threading.Thread | None = None


def _drain() -> None:
    """The one worker thread. Takes runs off the queue and runs them."""
    import contextlib
    import sys

    while True:
        run, work = _work.get()
        run.state = "running"
        buffer = _Buffer(run)
        try:
            # Only stdout is redirected. A traceback on stderr belongs in the
            # console the server was started from, where a person debugging
            # this can see it whether or not the page is open.
            with contextlib.redirect_stdout(buffer):
                result = work(buffer.write) or {}
            buffer.flush()
            run.result = result if isinstance(result, dict) else {"value": result}
            run.state = "done"
        except Exception as exc:
            buffer.flush()
            run.error = f"{type(exc).__name__}: {exc}"
            run.state = "failed"
            run.lines.append("")
            run.lines.extend(traceback.format_exc().rstrip().splitlines())
            print(f"[jobdesk] run {run.label!r} failed: {run.error}",
                  file=sys.stderr)
        finally:
            run.finished = datetime.now().isoformat(timespec="seconds")
            _work.task_done()


def _ensure_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_drain, name="jobdesk-runner",
                                   daemon=True)
        _worker.start()


def start(label: str, kind: str, work: Callable[[Emit], dict]) -> Run:
    """Queue a job. `work` is called with an `emit(text)` and returns a dict.

    Returns immediately with a run whose id the page polls. `emit` writes into
    the same buffer `print` does, so a function that wants to say something to
    the console can either take the callback or just print.
    """
    run = Run(id=uuid.uuid4().hex[:12], label=label, kind=kind,
              started=datetime.now().isoformat(timespec="seconds"))
    with _lock:
        _runs[run.id] = run
        _order.append(run.id)
        while len(_order) > KEEP:
            stale = _order.pop(0)
            # A run still going is never evicted, however old: dropping the one
            # the page is watching is the one thing this cache must not do.
            if _runs[stale].state in ("queued", "running"):
                _order.append(stale)
                break
            _runs.pop(stale, None)
    _ensure_worker()
    _work.put((run, work))
    return run


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


def recent(limit: int = 20) -> list[Run]:
    with _lock:
        ids = list(reversed(_order))[:limit]
    return [_runs[i] for i in ids if i in _runs]


def active() -> Run | None:
    """The run currently executing, if any. What the header badge shows."""
    with _lock:
        for run_id in reversed(_order):
            run = _runs.get(run_id)
            if run and run.state in ("queued", "running"):
                return run
    return None


def tail(run_id: str, after: int = 0) -> dict:
    """Output since line `after`, plus enough state to know when to stop.

    The page polls this. It sends back the line count it already has and gets
    only what is new, so a run that prints a thousand lines is not re-sent a
    thousand times.
    """
    run = _runs.get(run_id)
    if run is None:
        return {"missing": True, "id": run_id}
    lines = run.lines[after:]
    return {
        "id": run.id, "label": run.label, "kind": run.kind,
        "state": run.state, "started": run.started, "finished": run.finished,
        "lines": lines, "next": after + len(lines),
        "result": run.result, "error": run.error,
        "done": run.state in ("done", "failed"),
    }
