"""Command line: `uv run worker "task..."`."""

import argparse
import json
import urllib.request

from worker.agent import Worker
from worker.config import settings
from worker.run import ConsoleHuman, RunContext

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
     "c": "\033[36m", "x": "\033[0m"}


def show(e: dict) -> None:
    t = e["type"]
    if t == "plan_created":
        print(f"\n{C['b']}Outcome:{C['x']} {e['outcome']}\n{C['b']}Success criteria:{C['x']}")
        for c in e["criteria"]:
            print(f"  • {c}")
        print(f"{C['b']}Plan:{C['x']}")
        for i, s in enumerate(e["plan"]):
            print(f"  {i}. {s['description']}")
    elif t == "phase":
        print(f"\n{C['c']}== {e['phase'].upper()} =={C['x']}")
    elif t == "thought":
        print(f"{C['dim']}💭 {e['text'][:300]}{C['x']}")
    elif t in {"action", "verify_action"}:
        args = json.dumps(e["args"])[:140]
        print(f"{'🔎' if t == 'verify_action' else '▶'} {e['tool']} {args}")
    elif t == "observation" and not e["ok"]:
        print(f"  {C['r']}✗ {e['result'][:250]}{C['x']}")
    elif t == "memory":
        print(f"  {C['g']}📝 {e['fact']}{C['x']}")
    elif t == "policy_check":
        print(f"  {C['y']}🛡 policy: {e['decision']} — {e['reason']}{C['x']}")
    elif t == "replanned":
        print(f"{C['y']}↻ replanned: {e['diagnosis']}{C['x']}")
    elif t == "verdict":
        print(f"\n{C['b']}Verification: {'PASSED' if e['passed'] else 'FAILED'}{C['x']}")
        for c in e["checks"]:
            print(f"  {'✅' if c['passed'] else '❌'} {c['criterion']}\n     {C['dim']}"
                  f"{c['evidence'][:200]}{C['x']}")
    elif t == "crashed":
        print(f"{C['r']}CRASHED: {e['error']}{C['x']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Autonomous AI task worker")
    ap.add_argument("task", help="what you want done, in plain English")
    ap.add_argument("--reset", action="store_true", help="reset the demo company app first")
    args = ap.parse_args()
    if args.reset:
        urllib.request.urlopen(urllib.request.Request(
            settings.company_app_url + "/_admin/reset", method="POST"))
    run = RunContext(goal=args.task, human=ConsoleHuman())
    run.listeners.append(show)
    result = Worker(run).run_task()
    print(f"\n{C['b']}Result: {result['status']}{C['x']}\n{result.get('summary', '')}")
    print(f"Evidence: {run.dir}/report.md")


if __name__ == "__main__":
    main()
