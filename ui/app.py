"""Streamlit control room: give the worker a task, watch it work, answer its questions.

Run: uv run streamlit run ui/app.py
"""

import sys
import threading
import time
import urllib.request
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worker.agent import Worker  # noqa: E402
from worker.config import settings  # noqa: E402
from worker.run import QueueHuman, RunContext  # noqa: E402

EXAMPLES = [
    "Record the latest invoice from Stark Logistics in Acme Ops and tell me once it's done.",
    "Process every invoice in the inbox that isn't in Acme Ops yet.",
    "Flag all overdue open invoices in Acme Ops.",
]

st.set_page_config(page_title="AI Task Worker", page_icon="🤖", layout="wide")
st.title("🤖 AI Task Worker")
st.caption(f"Model: {settings.model} · Company system: {settings.company_app_url}")

with st.sidebar:
    st.subheader("Demo company")
    st.markdown(f"[Open Acme Ops]({settings.company_app_url}) · login `ops@acme.test` / `demo123`")
    if st.button("Reset Acme Ops data"):
        urllib.request.urlopen(urllib.request.Request(
            settings.company_app_url + "/_admin/reset", method="POST"))
        st.success("Reset to seed data.")
    st.subheader("Example tasks")
    for ex in EXAMPLES:
        st.code(ex, language=None, wrap_lines=True)

state = st.session_state
task = st.text_area("What should the worker do?", value=EXAMPLES[0], height=80)
running = "thread" in state and state.thread.is_alive()

if st.button("▶ Run", type="primary", disabled=running):
    human = QueueHuman()
    run = RunContext(goal=task, human=human)
    worker = Worker(run)
    state.run, state.human = run, human
    state.thread = threading.Thread(target=worker.run_task, daemon=True)
    state.thread.start()
    running = True

run: RunContext | None = state.get("run")
if run:
    human: QueueHuman = state.human
    if human.pending:
        p = human.pending
        box = st.warning if p["kind"] == "approval" else st.info
        box(("🛡 **Approval needed**\n\n" if p["kind"] == "approval" else "❓ **Question**\n\n")
            + p["question"])
        if p["kind"] == "approval":
            c1, c2, c3 = st.columns([1, 1, 3])
            comment = c3.text_input("Comment (optional)", key=f"c{len(run.events)}")
            if c1.button("Approve", type="primary"):
                human.answer(f"approve {comment}".strip())
                st.rerun()
            if c2.button("Reject"):
                human.answer(f"reject {comment}".strip())
                st.rerun()
        else:
            with st.form(f"answer{len(run.events)}"):
                ans = st.text_input("Your answer")
                if st.form_submit_button("Send"):
                    human.answer(ans)
                    st.rerun()

    left, right = st.columns([3, 2])
    with left:
        created = next((e for e in run.events if e["type"] == "plan_created"), None)
        if created:
            st.markdown(f"**Outcome:** {created['outcome']}")
            st.markdown("**Success criteria**\n" + "\n".join(f"- {c}" for c in created["criteria"]))
        st.markdown("**Plan**")
        icons = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "skipped": "⏭",
                 "failed": "❌"}
        st.markdown("\n".join(f"{icons.get(s.status, '•')} {s.description}"
                              + (f" — _{s.note}_" if s.note else "") for s in run.plan)
                    or "_planning…_")
        st.markdown("**Activity**")
        log = st.container(height=420)
        for e in run.events[-80:]:
            t = e["type"]
            if t == "phase":
                log.markdown(f"**— {e['phase'].replace('_', ' ')} —**")
            elif t == "thought":
                log.caption(f"💭 {e['text'][:400]}")
            elif t in {"action", "verify_action"}:
                log.markdown(f"{'🔎' if t == 'verify_action' else '▶'} `{e['tool']}` "
                             f"{str(e['args'])[:120]}")
            elif t == "observation" and not e["ok"]:
                log.error(e["result"][:300])
            elif t == "memory":
                log.success(f"📝 {e['fact']}")
            elif t == "policy_check":
                log.warning(f"🛡 {e['decision']}: {e['reason']}")
            elif t == "replanned":
                log.warning(f"↻ Replanned: {e['diagnosis']}")
            elif t == "approval_decision":
                log.info("Approved" if e["approved"] else f"Rejected: {e['comment']}")
            elif t == "crashed":
                log.error(f"Crashed: {e['error']}")
    with right:
        shots = sorted((run.dir / "screenshots").glob("*.png"))
        if shots:
            st.image(str(shots[-1]), caption=shots[-1].name)
        if run.facts:
            st.markdown("**Working memory**\n" + "\n".join(f"- {f}" for f in run.facts))

    if run.result:
        st.divider()
        report = run.dir / "report.md"
        if report.exists():
            st.markdown(report.read_text())
        st.caption(f"Evidence folder: {run.dir}")

if running:
    time.sleep(1)
    st.rerun()
