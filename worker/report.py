"""Writes the evidence pack for a run: report.md + result.json next to trace.jsonl."""

import json

from worker.run import RunContext


def write_report(run: RunContext) -> None:
    r = run.result or {}
    (run.dir / "result.json").write_text(json.dumps(r, indent=2, default=str))
    icon = {"completed": "✅", "incomplete": "⚠️", "crashed": "❌"}.get(r.get("status"), "?")
    lines = [f"# {icon} {r.get('status', 'unknown').upper()}", "", f"**Task:** {run.goal}", "",
             "## Summary", r.get("summary") or r.get("error", ""), "", "## Plan"]
    lines += [f"- [{s.status}] {s.description}" + (f" — {s.note}" if s.note else "")
              for s in run.plan]
    if run.items:
        lines += ["", "## Work items"] + [
            f"- **{v['status']}** {k}" + (f" — {v['note']}" if v["note"] else "")
            for k, v in run.items.items()]
    v = r.get("verification")
    if v:
        lines += ["", f"## Independent verification (attempt {v['attempt']})"]
        lines += [f"- {'✅' if c['passed'] else '❌'} {c['criterion']}  \n  _Evidence:_ "
                  f"{c['evidence']}" for c in v["checks"]]
        if v.get("problems"):
            lines += ["", f"Problems: {v['problems']}"]
    if run.approvals:
        lines += ["", "## Human approvals"]
        lines += [f"- {'Approved' if a['approved'] else 'Rejected'}: \"{a['button']}\" — "
                  f"{a['reason']} (reply: {a['comment']!r})" for a in run.approvals]
    if run.facts:
        lines += ["", "## Working memory"] + [f"- {f}" for f in run.facts]
    shots = sorted((run.dir / "screenshots").glob("*.png"))
    if shots:
        lines += ["", "## Screenshots"] + [f"- screenshots/{s.name}" for s in shots]
    lines += ["", f"Full step-by-step trace: `trace.jsonl` ({len(run.events)} events)"]
    (run.dir / "report.md").write_text("\n".join(lines))
