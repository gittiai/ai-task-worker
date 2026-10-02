# AI Task Worker

An autonomous AI worker that takes a short, plain-English company request and **completes
it on a computer**: it reads company documents, operates a real web application through a
browser, asks a human only when policy or missing information requires it, and
**independently verifies** the result before it reports done, with evidence.

> "Record the latest invoice from Stark Logistics in Acme Ops and tell me once it's done."

From that one sentence, the worker reads the company handbook, finds the right PDF among
several from the same vendor, extracts the values, logs into the internal system, checks for
duplicates, fills the form in the required formats, gets past a popup and a server error,
re-opens the saved record to confirm it, and writes an evidence pack (report, trace and
screenshots).

**Demo video:** _TODO — link_

---

## Quick start

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/) and an [xAI API key](https://console.x.ai).

```bash
uv sync
uv run playwright install chromium
cp .env.example .env            # then put your XAI_API_KEY in .env

# Terminal 1: the demo company's internal system (add CHAOS_MODE=1 to test recovery)
uv run uvicorn company_app.main:app --port 8000

# Terminal 2: the control-room UI
uv run streamlit run ui/app.py

# ...or the command line
uv run worker --reset "Record the latest invoice from Stark Logistics in Acme Ops"
```

Set `HEADLESS=0` in `.env` to watch the browser. Run the tests (no API key needed) with
`uv run pytest -q`.

### Tasks to try

| Task | What it shows |
|---|---|
| Record the latest invoice from Stark Logistics in Acme Ops and tell me once it's done. | Picks the newest of two PDFs; date and amount formatting; verification |
| Process every invoice in the inbox that isn't in Acme Ops yet. | Multi-item work; the duplicate is refused; approval for the ₹1,24,000 invoice; computes "Net 30"; asks about the invoice with no due date |
| Flag all overdue open invoices in Acme Ops. | A different task with **no code changes** |
| Any of the above with `CHAOS_MODE=1` | Recovers from a blocking popup, randomised element IDs and a transient 503 |

---

## Architecture

```
            ┌──────────────────────── RunContext ────────────────────────┐
            │ plan · success criteria · working memory · approvals ·      │
            │ append-only event trace · screenshots · human channel       │
            └─────────────────────────────────────────────────────────────┘
 request                                                                    evidence
   │     ┌──────┐   ┌─────┐   ┌───────────────────────┐                  ┌────────┐
   └────▶│ plan │──▶│ act │──▶│ tools                 │──── finish ─────▶│ verify │
         └──────┘   └─────┘   │  • files (sandboxed)  │                  └───┬────┘
                      ▲  ▲    │  • browser (refs)     │  gaps found          │ passed / out
                      │  │    │  • note / update_plan │◀─────────────────────┘ of attempts
                      │  │    │  • ask_human          │                      ▼
                      │  │    │  ── policy guard ──   │                  ┌────────┐
                      │  │    └──────────┬────────────┘                  │ report │
                      │  └── observe ────┤                               └────────┘
                      │                  │ 3 failures in a row
                      └──── replan ◀─────┘
```

It's a LangGraph state machine (`worker/agent.py`), and each node maps onto the
problem statement's loop:

| Loop stage | Where |
|---|---|
| **Goal → Understand → Plan** | `plan` node: turns the request into an outcome, checkable *success criteria*, a high-level plan and stated assumptions. It sees a map of the company workspace, not its contents, so it has to work out which sources matter. |
| **Execute → Observe → Adapt** | `act` ⇄ `tools`: the LLM picks one or more tool calls; every browser action returns the new page state, so the next decision is based on what actually happened. Errors become observations with hints (e.g. "a dialog is intercepting the click"). |
| **Recover** | Code counts consecutive failures; after 3 the `replan` node diagnoses the root cause and rewrites the remaining plan. Identical calls repeated 3 times are refused. |
| **Human in the loop** | `ask_human` when information is missing; the **policy guard** asks for approval on data-changing actions when policy requires it. |
| **Verify** | `verify` node: a separate pass with **read-only** tools that re-opens the system of record and checks each success criterion, citing evidence. If it fails, the gaps go back to the worker (up to 2 attempts). |
| **Complete** | `report` node writes `runs/<id>/report.md`, `result.json`, `trace.jsonl` and screenshots. |

### Components

| Path | What it is |
|---|---|
| `worker/agent.py` | The control loop, policy guard and verifier |
| `worker/prompts.py` | All prompts. None mention invoices; the task-specific knowledge lives in the company's own documents. |
| `worker/tools/browser.py` | Playwright wrapper: element refs, observations, commit-action detection |
| `worker/tools/files.py` | Read-only, path-sandboxed workspace access, with PDF text extraction |
| `worker/run.py` | Run context, event trace, human channels (console and UI queue) |
| `company_app/` | **Acme Ops**: a real FastAPI + SQLite web app with sessions, validation and a chaos mode |
| `company_data/` | The company's handbook (`policies.md`), sandbox logins and an inbox of 7 invoice PDFs |
| `ui/app.py` | Streamlit control room: live plan, activity, latest screenshot, approve/reject |

---

## Key design decisions

**1. The LLM decides; code enforces.** The model chooses what to do next, but the parts
that must not depend on the model behaving live in code: the step budget, failure counting
and replanning, the approval gate, and verification before "done". If a prompt asks the model
to "remember to get approval", that only works when the model complies. A guard that sits in
front of the click works every time.

**2. The policy guard sits on the action, not the plan.** Any click on a button whose label
means *change data* (save, submit, delete, pay…) is intercepted. A policy check, which is
an LLM reading the company handbook plus the live form values, returns
`allow` / `needs_approval` / `block`. Approval is tied to the exact form contents, so changing
a value after approval needs a fresh approval. Rejections are final for that action.

**3. Verification is independent and looks at the system of record.** "Invoice saved" on
screen is not proof. The verifier gets the success criteria the planner wrote *before*
any work began, plus read-only tools, and has to cite evidence (record IDs, values) for each
criterion. It can't save data even if it tries.

**4. The browser is seen as structure, not pixels.** Each observation tags visible interactive
elements with short refs (`e1`, `e2`…) and includes labels, current values, select options
and the visible text. This is cheaper and more reliable than screenshot-coordinate clicking,
and survives the randomised element IDs in chaos mode, because refs are re-assigned on every
observation. Screenshots are still captured on every step as human-readable evidence.

**5. Memory is explicit.** Old tool outputs are trimmed to keep the context small. Anything the
worker needs later (extracted values, record IDs, decisions) it saves with `note`, and the
system prompt re-injects those facts with the current plan every turn. The memory is
small, inspectable, and appears in the report.

**6. Generalisation comes from the company's documents, not the code.** Rules like "amounts
without separators", "dates as YYYY-MM-DD", "approval above ₹50,000" and "compute Net 30"
are in `company_data/policies.md`, not in the prompts. A different task (flagging overdue
invoices), or a different policy, needs no code changes.

**7. A realistic but safe environment.** The brief says not to use real systems, so Acme Ops is a
real web app the agent must drive like a person: login, validation errors, duplicates,
and in chaos mode a blocking popup, element IDs that change on every load and a transient 503.
None of the agent's behaviour is mocked.

---

## Models, frameworks and services

| | |
|---|---|
| LLM | **Grok** (xAI API) via `langchain-xai`; model set by `XAI_MODEL` (default `grok-4`) |
| Agent loop | **LangGraph** (state machine) + `langchain-core` (tool schemas, structured output) |
| Computer use | **Playwright** (Chromium) |
| Documents | **pdfplumber** (extraction), **reportlab** (generating the sample invoices) |
| Company app | **FastAPI**, Jinja2, SQLite |
| UI | **Streamlit** |
| Tests | pytest; the agent loop is tested with a scripted model, so no API key is needed |

No other external services. Everything except the LLM call runs locally.

## Assumptions

- Company systems are reachable as web apps in a browser. Desktop apps are out of scope for
  this prototype (see *Next*).
- Invoices are text PDFs. Scanned images would need OCR or a vision model.
- The company's written policies are the source of truth for how work is done and what needs
  approval.
- The requester is the approver. In a real company those would often be different people.
- Credentials for the sandbox system are in the workspace (`accounts.md`). A real deployment
  would use a secrets vault and scoped service accounts.

## Known limitations

- **Single browser tab, web only.** No desktop-app control, file downloads or multi-tab flows yet.
- **Commit detection is label-based.** It catches buttons named save/submit/delete etc. A
  data-changing control with an unusual label (or a GET link that changes data) would get past
  the guard. A production system would classify actions per connector or use network-level
  interception.
- **The verifier shares the worker's browser session.** That's fast, but less independent than
  a fresh session or a direct API/DB check.
- **One LLM does everything.** The policy check and the verifier use the same model as the
  worker, so they can share its blind spots. A different model for verification would be stronger.
- **No persistence across runs.** If the process dies mid-task, the run can't be resumed
  (LangGraph checkpointing would add this). Nothing is learned from past runs yet.
- **Approval matching is exact.** Any change to the form after approval triggers a new
  approval request.

## What I'd build next

1. **Learning from outcomes:** store successful runs as reusable *procedures* ("how we record
   an invoice in Acme Ops"), and store human corrections as company memory. Retrieve them
   at planning time so the second run of a task is faster and needs fewer questions.
2. **Durable, resumable runs:** LangGraph checkpointer on Postgres, task queue and scheduler,
   so runs survive restarts and can wait hours for an approval (via Slack or email).
3. **Connectors before pixels:** use an API when a system has one, the browser when it
   doesn't, and desktop control (accessibility APIs, then vision) as a last resort, all
   behind the same tool interface.
4. **Stronger safety:** action classification per connector, a permission model per worker,
   a dry-run mode, and verification with a separate model and session.
5. **Evals:** a suite of tasks × chaos settings scored automatically by the verifier and DB
   checks, so reliability is a number that's tracked over time.

## Repository layout

```
company_app/   fake internal system (FastAPI + SQLite + templates)
company_data/  policies.md, accounts.md, inbox/*.pdf, generate_invoices.py
worker/        agent.py, prompts.py, run.py, report.py, cli.py, tools/
ui/            Streamlit control room
tests/         company app, file sandbox, and control-loop tests
runs/          evidence per run (git-ignored)
```
