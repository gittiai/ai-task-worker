"""Per-run context: working memory, the plan, the evidence trail, and the human channel.

Everything the agent does goes through `RunContext.emit`, which writes an append-only
JSONL trace in runs/<id>/ and notifies any live listener (the CLI or the UI).
"""

import json
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from worker.config import settings


class HumanChannel:
    """How the worker reaches a person. Subclasses decide the medium."""

    def ask(self, question: str, kind: str = "question") -> str:  # kind: question|approval
        raise NotImplementedError


class ConsoleHuman(HumanChannel):
    def ask(self, question: str, kind: str = "question") -> str:
        label = "APPROVAL NEEDED" if kind == "approval" else "QUESTION"
        hint = " [approve/reject + optional comment]" if kind == "approval" else ""
        print(f"\n\033[1;33m{label}:\033[0m {question}{hint}")
        return input("> ").strip()


class QueueHuman(HumanChannel):
    """Blocks the worker thread until the UI posts an answer."""

    def __init__(self):
        self.pending: dict | None = None
        self._answers: queue.Queue[str] = queue.Queue()

    def ask(self, question: str, kind: str = "question") -> str:
        self.pending = {"question": question, "kind": kind}
        answer = self._answers.get()
        self.pending = None
        return answer

    def answer(self, text: str) -> None:
        self._answers.put(text)


@dataclass
class PlanStep:
    description: str
    status: str = "pending"  # pending | in_progress | done | skipped | failed
    note: str = ""


@dataclass
class RunContext:
    goal: str
    human: HumanChannel
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S-")
                        + uuid.uuid4().hex[:4])
    plan: list[PlanStep] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)  # every data-changing action taken
    documents: dict[str, str] = field(default_factory=dict)  # files read, path -> text
    items: dict[str, dict] = field(default_factory=dict)  # work list for multi-item tasks
    events: list[dict] = field(default_factory=list)
    listeners: list[Callable[[dict], None]] = field(default_factory=list)
    result: dict | None = None

    def __post_init__(self):
        self.dir: Path = settings.runs_dir / self.run_id
        (self.dir / "screenshots").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._shot = 0

    def emit(self, type: str, **data) -> dict:
        event = {"t": round(time.time(), 3), "type": type, **data}
        with self._lock:
            self.events.append(event)
            with open(self.dir / "trace.jsonl", "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        for fn in self.listeners:
            try:
                fn(event)
            except Exception:
                pass
        return event

    def screenshot_path(self, label: str) -> Path:
        self._shot += 1
        safe = "".join(c if c.isalnum() else "-" for c in label.lower())[:40]
        return self.dir / "screenshots" / f"{self._shot:03d}-{safe}.png"

    def plan_text(self) -> str:
        if not self.plan:
            return "(no plan yet)"
        return "\n".join(
            f"{i}. [{s.status}] {s.description}" + (f" — {s.note}" if s.note else "")
            for i, s in enumerate(self.plan)
        )

    def items_text(self) -> str:
        if not self.items:
            return "(no tracked items)"
        return "\n".join(f"- [{v['status']}] {k}" + (f" — {v['note']}" if v["note"] else "")
                         for k, v in self.items.items())

    def facts_text(self) -> str:
        return "\n".join(f"- {f}" for f in self.facts) or "(nothing recorded yet)"
