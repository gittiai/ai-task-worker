"""The worker's control loop, built as a LangGraph state machine.

    plan ─▶ act ─▶ tools ─┬─▶ act            (observe → decide the next action)
             ▲            ├─▶ replan ─▶ act  (too many failures in a row: rethink)
             │            └─▶ verify ─┬─▶ report   (verified, or out of attempts)
             └────────────────────────┘           (gaps found: go back and fix them)

The LLM decides *what* to do. Code enforces the parts that must not depend on the LLM
behaving: the step budget, failure counting, the policy guard on data-changing clicks,
and an independent verification pass before anything is reported as done.
"""

from __future__ import annotations

import json
import operator
from datetime import date
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from worker import prompts
from worker.config import settings
from worker.llm import get_llm, structured
from worker.run import PlanStep, RunContext
from worker.tools import files
from worker.tools.browser import Browser

KEEP_FULL_TOOL_OUTPUTS = 2  # older tool outputs are trimmed; facts live in working memory
MAX_HISTORY = 24


# --- structured outputs ------------------------------------------------------------


class Plan(BaseModel):
    outcome: str
    success_criteria: list[str]
    steps: list[str]
    assumptions: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    decision: Literal["allow", "needs_approval", "block"]
    reason: str


class Replan(BaseModel):
    diagnosis: str
    remaining_steps: list[str]


class Check(BaseModel):
    criterion: str
    passed: bool
    evidence: str


class Verdict(BaseModel):
    passed: bool
    checks: list[Check]
    problems: str = ""


# --- graph state --------------------------------------------------------------------


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    steps: int
    consecutive_failures: int
    recent_failures: Annotated[list[str], operator.add]
    verify_attempts: int
    phase: str  # acting | verifying | done | failed
    claim: str


class Worker:
    def __init__(self, run: RunContext):
        self.run = run
        self.llm = get_llm()
        self.browser = Browser(on_screenshot=run.screenshot_path)
        self.today = date.today().isoformat()
        self.outcome = ""
        self._last_calls: list[str] = []
        self.actor_tools = self._make_tools()
        self.verifier_tools = [t for t in self.actor_tools if t.name in {
            "list_files", "read_file", "browser_open", "browser_read", "browser_click"}]
        self.graph = self._build_graph()

    # ---- tools ---------------------------------------------------------------------

    def _make_tools(self) -> list[StructuredTool]:
        run, b = self.run, self.browser

        def list_files(folder: str = ".") -> str:
            """List files in the company workspace (or a sub-folder such as 'inbox')."""
            return files.list_files(folder)

        def read_file(path: str) -> str:
            """Read a workspace file: markdown, text, or PDF (text is extracted)."""
            text = files.read_file(path)
            run.documents[path.removeprefix("company_data/")] = text
            return text

        def browser_open(url: str) -> str:
            """Open a URL (absolute, or a path like /invoices on the company app)."""
            return b.open(url)

        def browser_read() -> str:
            """Re-read the current page: URL, interactive elements with refs, visible text."""
            return b.observe("read")

        def browser_click(ref: str) -> str:
            """Click an element by its ref (e.g. 'e7') from the latest observation."""
            return b.click(ref)

        def browser_type(ref: str, text: str) -> str:
            """Replace the contents of an input/textarea (by ref) with text."""
            return b.type(ref, text)

        def browser_select(ref: str, option: str) -> str:
            """Choose an option (by its visible text) in a <select> element."""
            return b.select(ref, option)

        def browser_fill(fields: dict[str, str]) -> str:
            """Fill several form fields in one action: {"e4": "value", "e5": "value"}.
            Works for inputs, textareas and selects (by option text). Does not submit."""
            return b.fill(fields)

        def note(fact: str) -> str:
            """Save a fact to working memory (extracted values, record IDs, decisions)."""
            run.facts.append(fact)
            run.emit("memory", fact=fact)
            return f"Noted. Working memory has {len(run.facts)} facts."

        def update_plan(step: int, status: str, note: str = "", new_description: str = "") -> str:
            """Update plan step `step` (0-based). status: pending|in_progress|done|skipped|failed.
            Use step = current plan length to append a new step with new_description."""
            if step == len(run.plan):
                run.plan.append(PlanStep(new_description or note or "New step"))
            if not 0 <= step < len(run.plan):
                raise ValueError(f"No step {step}; plan has {len(run.plan)} steps.")
            s = run.plan[step]
            s.status, s.note = status, note or s.note
            if new_description:
                s.description = new_description
            run.emit("plan", plan=[vars(p) for p in run.plan])
            return run.plan_text()

        def ask_human(question: str) -> str:
            """Ask the requester a question when you cannot safely proceed without them."""
            run.emit("human_question", question=question)
            answer = run.human.ask(question, kind="question")
            run.emit("human_answer", answer=answer)
            return f"Requester answered: {answer or '(no answer)'}"

        def track_items(items: list[str]) -> str:
            """For tasks with several items (files, records, people...), register every item
            up front, e.g. one per inbox file. Each must later be closed with resolve_item."""
            for it in items:
                run.items.setdefault(it, {"status": "open", "note": ""})
            run.emit("items", items=run.items)
            return run.items_text()

        def resolve_item(item: str, status: str, note: str) -> str:
            """Close a tracked item. status: done | already_exists | skipped | needs_human.
            note must give evidence: the record ID created, the ID of the existing record that
            matches (same vendor AND number), or why it was skipped."""
            if item not in run.items:
                raise ValueError(f"{item!r} is not tracked. Tracked: {list(run.items)}")
            if status not in {"done", "already_exists", "skipped", "needs_human"}:
                raise ValueError("status must be done, already_exists, skipped or needs_human.")
            run.items[item] = {"status": status, "note": note}
            run.emit("items", items=run.items)
            return run.items_text()

        def finish(summary: str) -> str:
            """Call when all work is handled. Summarise what was done, with record IDs,
            and anything not done and why. This triggers independent verification."""
            still_open = [k for k, v in run.items.items() if v["status"] == "open"]
            if still_open:
                raise RuntimeError(f"Cannot finish: {len(still_open)} tracked items are still "
                                   f"open: {still_open}. Handle or resolve each one first.")
            return "Submitted for verification."

        fns = [list_files, read_file, browser_open, browser_read, browser_click, browser_type,
               browser_select, browser_fill, note, update_plan, track_items, resolve_item,
               ask_human, finish]
        return [StructuredTool.from_function(f) for f in fns]

    # ---- policy guard --------------------------------------------------------------

    def _guard(self, ref: str) -> str | None:
        """Runs before any data-changing click. Returns a refusal message, or None to allow."""
        if not self.browser.is_commit(ref):
            return None
        button = self.browser.describe(ref).get("label", ref)
        form = self.browser.form_state()
        policies = files.read_file("policies.md")
        decision: PolicyDecision = structured(self.llm, PolicyDecision).invoke(
            prompts.GUARD.format(
                policies=policies, goal=self.run.goal, facts=self.run.facts_text(),
                changes="\n".join(f"- {c}" for c in self.run.changes) or "(none yet)",
                documents=self._documents_for_guard(),
                button=button, url=self.browser.page.url, form=form,
                page=self.browser.page.inner_text("body")[:1500],
            )
        )
        self.run.emit("policy_check", button=button, form=form, decision=decision.decision,
                      reason=decision.reason)
        if decision.decision == "allow":
            return None
        if decision.decision == "block":
            return f"BLOCKED by policy control: {decision.reason} The click was not performed."

        key = f"{self.browser.page.url}|{form}"
        if any(a["key"] == key and a["approved"] for a in self.run.approvals):
            return None
        question = (f"The worker wants to click \"{button}\" on {self.browser.page.url} with:\n"
                    f"{form}\n\nWhy approval is needed: {decision.reason}")
        self.run.emit("approval_request", question=question)
        answer = self.run.human.ask(question, kind="approval")
        approved = answer.strip().lower().startswith(("approve", "yes", "y", "ok"))
        self.run.approvals.append({"key": key, "button": button, "form": form,
                                   "reason": decision.reason, "approved": approved,
                                   "comment": answer})
        self.run.emit("approval_decision", approved=approved, comment=answer)
        if approved:
            return None
        return (f"REJECTED by the requester ({answer!r}). The click was not performed. "
                "Do not retry this action; continue with other work and report it.")

    # ---- nodes ---------------------------------------------------------------------

    def plan_node(self, state: State) -> dict:
        self.run.emit("phase", phase="understand_and_plan")
        plan: Plan = structured(self.llm, Plan).invoke(prompts.PLANNER.format(
            today=self.today, workspace=files.list_files("."), goal=self.run.goal))
        self.outcome = plan.outcome
        self.run.success_criteria = plan.success_criteria
        self.run.plan = [PlanStep(s) for s in plan.steps]
        self.run.emit("plan_created", outcome=plan.outcome, criteria=plan.success_criteria,
                      plan=[vars(p) for p in self.run.plan], assumptions=plan.assumptions)
        return {"messages": [HumanMessage(f"Task: {self.run.goal}\nStart working.")],
                "phase": "acting"}

    def _system_prompt(self) -> SystemMessage:
        return SystemMessage(prompts.ACTOR.format(
            today=self.today, goal=self.run.goal, outcome=self.outcome,
            criteria="\n".join(f"- {c}" for c in self.run.success_criteria),
            plan=self.run.plan_text(), facts=self.run.facts_text(),
            items=self.run.items_text()))

    @staticmethod
    def _compact(messages: list[BaseMessage]) -> list[BaseMessage]:
        """Trim old tool output so context stays small; key facts are in working memory."""
        msgs = messages[-MAX_HISTORY:]
        while msgs and isinstance(msgs[0], ToolMessage):  # never start on an orphan result
            msgs = msgs[1:]
        if messages and messages[0] not in msgs:
            msgs = [messages[0], *msgs]
        tool_idx = [i for i, m in enumerate(msgs) if isinstance(m, ToolMessage)]
        old = set(tool_idx[:-KEEP_FULL_TOOL_OUTPUTS])
        out = []
        for i, m in enumerate(msgs):
            if i in old and len(m.content) > 200 and m.name != "read_file":
                m = ToolMessage(content=m.content[:200] + " ...[trimmed]",
                                tool_call_id=m.tool_call_id, name=m.name)
            out.append(m)
        return out

    def _invoke_tools_llm(self, llm, messages: list[BaseMessage], tool_names: list[str]):
        """Invoke a tool-calling model. If the provider rejects a malformed or invented tool
        call, show the model its mistake and let it try again instead of crashing the run."""
        for _ in range(3):
            try:
                return llm.invoke(messages)
            except Exception as e:
                if "tool_use_failed" not in str(e) and "Tool call validation" not in str(e):
                    raise
                msg = str(e).split("'failed_generation'")[0][-400:]
                self.run.emit("observation", tool="(model)", ok=False,
                              result=f"Invalid tool call rejected: {msg}")
                messages = [*messages, HumanMessage(
                    f"Your last tool call was rejected: {msg}\nOnly these tools exist: "
                    f"{', '.join(tool_names)}. Call one of them with valid arguments.")]
        return llm.invoke(messages)

    def act_node(self, state: State) -> dict:
        llm = self.llm.bind_tools(self.actor_tools)
        reply: AIMessage = self._invoke_tools_llm(
            llm, [self._system_prompt(), *self._compact(state["messages"])],
            [t.name for t in self.actor_tools])
        if reply.content:
            self.run.emit("thought", text=str(reply.content)[:2000])
        updates: dict = {"messages": [reply], "steps": state["steps"] + 1}
        if not reply.tool_calls:
            updates["messages"].append(HumanMessage(
                "Continue by calling a tool. If all work is handled, call `finish`."))
        return updates

    def tools_node(self, state: State) -> dict:
        last: AIMessage = state["messages"][-1]
        tools = {t.name: t for t in self.actor_tools}
        out, failures, consecutive, phase, claim = [], [], state["consecutive_failures"], \
            state["phase"], state["claim"]
        for call in last.tool_calls:
            name, args = call["name"], call["args"]
            self.run.emit("action", tool=name, args=args)
            signature = json.dumps([name, args], sort_keys=True)
            self._last_calls = (self._last_calls + [signature])[-3:]
            ok = True
            try:
                if name not in tools:
                    raise ValueError(f"Unknown tool {name}.")
                if self._last_calls.count(signature) == 3 and name not in {"browser_read",
                                                                             "note"}:
                    raise RuntimeError("You have made this exact call 3 times in a row. It is "
                                       "not working; try a different approach.")
                commit = None
                if name == "browser_click" and self.browser.is_commit(args.get("ref", "")):
                    commit = (self.browser.describe(args["ref"]).get("label"),
                              self.browser.form_state().replace("\n", "; "))
                refusal = self._guard(args.get("ref", "")) if commit else None
                result = refusal or tools[name].invoke(args)
                if commit and not refusal:
                    self._record_change(*commit)
                if name == "finish":
                    phase, claim = "verifying", args.get("summary", "")
            except Exception as e:  # every failure becomes an observation the agent can use
                ok = False
                result = f"ERROR: {type(e).__name__}: {self._explain(e)}"
                failures.append(f"{name}({args}) -> {result[:300]}")
            consecutive = 0 if ok else consecutive + 1
            self.run.emit("observation", tool=name, ok=ok, result=str(result)[:3000])
            out.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=name))
        return {"messages": out, "consecutive_failures": consecutive,
                "recent_failures": failures, "phase": phase, "claim": claim}

    def _documents_for_guard(self) -> str:
        """Source documents read in this run (policies excluded; the guard gets those anyway)."""
        docs = [(p, t) for p, t in self.run.documents.items() if "polic" not in p]
        return "\n\n".join(f"--- {p} ---\n{t[:1500]}" for p, t in docs[-8:]) or "(none)"

    def _record_change(self, button: str, form: str) -> None:
        """Every data-changing action is remembered automatically, so neither the worker nor
        the policy guard depends on the model having taken notes."""
        page = self.browser.page
        alert = page.query_selector("[role=alert]")
        outcome = (f"page showed errors: {alert.inner_text()[:200]!r}" if alert
                   else f"now at {page.url} ({page.title()})")
        change = f"Clicked \"{button}\" with [{form}] -> {outcome}"
        self.run.changes.append(change)
        self.run.facts.append(f"DATA CHANGE: {change}")
        self.run.emit("memory", fact=f"DATA CHANGE: {change}")

    def _explain(self, e: Exception) -> str:
        msg = str(e).split("\nCall log")[0].strip()
        if "Timeout" in msg and self.browser.page:
            if self.browser.page.query_selector("[role=dialog]"):
                msg += (" — a dialog/overlay is open on the page and is probably intercepting "
                        "the click. Close it first.")
            else:
                msg += " — the element may be hidden, disabled or covered."
        return msg

    def replan_node(self, state: State) -> dict:
        self.run.emit("phase", phase="replan")
        r: Replan = structured(self.llm, Replan).invoke(prompts.REPLAN.format(
            goal=self.run.goal, plan=self.run.plan_text(), facts=self.run.facts_text(),
            failures="\n".join(state["recent_failures"][-5:])))
        done = [s for s in self.run.plan if s.status == "done"]
        self.run.plan = done + [PlanStep(s) for s in r.remaining_steps]
        self.run.emit("replanned", diagnosis=r.diagnosis, plan=[vars(p) for p in self.run.plan])
        return {"consecutive_failures": 0, "messages": [HumanMessage(
            f"Replanned after repeated failures. Diagnosis: {r.diagnosis}\n"
            f"New plan:\n{self.run.plan_text()}")]}

    def verify_node(self, state: State) -> dict:
        attempt = state["verify_attempts"] + 1
        self.run.emit("phase", phase="verify", attempt=attempt)
        llm = self.llm.bind_tools(self.verifier_tools)
        tools = {t.name: t for t in self.verifier_tools}
        msgs: list[BaseMessage] = [HumanMessage(prompts.VERIFIER.format(
            today=self.today, goal=self.run.goal,
            criteria="\n".join(f"- {c}" for c in self.run.success_criteria),
            facts=self.run.facts_text(),
            claim=f"{state['claim']}\n\nPer-item report:\n{self.run.items_text()}"))]
        for _ in range(15):
            reply = self._invoke_tools_llm(llm, msgs, list(tools))
            msgs.append(reply)
            if not reply.tool_calls:
                break
            for call in reply.tool_calls:
                self.run.emit("verify_action", tool=call["name"], args=call["args"])
                try:
                    if call["name"] == "browser_click" and self.browser.is_commit(
                            call["args"].get("ref", "")):
                        raise PermissionError("The verifier is read-only and cannot click "
                                              "buttons that change data.")
                    result = tools[call["name"]].invoke(call["args"])
                except Exception as e:
                    result = f"ERROR: {self._explain(e)}"
                msgs.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
        verdict: Verdict = structured(self.llm, Verdict).invoke(
            [*self._compact(msgs), HumanMessage(prompts.VERIFIER_JUDGE)])
        self.run.emit("verdict", attempt=attempt, **verdict.model_dump())
        if verdict.passed:
            return {"verify_attempts": attempt, "phase": "done"}
        if attempt >= settings.max_verify_attempts:
            return {"verify_attempts": attempt, "phase": "failed"}
        gaps = "\n".join(f"- {c.criterion}: {c.evidence}" for c in verdict.checks if not c.passed)
        return {"verify_attempts": attempt, "phase": "acting", "messages": [HumanMessage(
            f"Independent verification FAILED.\n{gaps}\n{verdict.problems}\n"
            "Fix these problems, then call finish again.")]}

    def report_node(self, state: State) -> dict:
        phase = state["phase"]
        if phase not in {"done", "failed"}:
            phase = "failed"  # stopped by the step budget
        verdict = next((e for e in reversed(self.run.events) if e["type"] == "verdict"), None)
        self.run.result = {
            "status": "completed" if phase == "done" else "incomplete",
            "goal": self.run.goal, "summary": state["claim"] or "(worker did not finish)",
            "steps_used": state["steps"], "verification": verdict,
            "approvals": self.run.approvals, "facts": self.run.facts,
            "plan": [vars(p) for p in self.run.plan],
        }
        from worker.report import write_report
        write_report(self.run)
        self.run.emit("finished", status=self.run.result["status"])
        return {"phase": phase}

    # ---- routing -------------------------------------------------------------------

    def after_act(self, state: State) -> str:
        if state["steps"] >= settings.max_steps:
            return "report"
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "act"

    def after_tools(self, state: State) -> str:
        if state["phase"] == "verifying":
            return "verify"
        if state["steps"] >= settings.max_steps:
            return "report"
        if state["consecutive_failures"] >= settings.max_consecutive_failures:
            return "replan"
        return "act"

    def after_verify(self, state: State) -> str:
        return "act" if state["phase"] == "acting" else "report"

    def _build_graph(self):
        g = StateGraph(State)
        g.add_node("plan", self.plan_node)
        g.add_node("act", self.act_node)
        g.add_node("tools", self.tools_node)
        g.add_node("replan", self.replan_node)
        g.add_node("verify", self.verify_node)
        g.add_node("report", self.report_node)
        g.add_edge(START, "plan")
        g.add_edge("plan", "act")
        g.add_conditional_edges("act", self.after_act, ["tools", "act", "report"])
        g.add_conditional_edges("tools", self.after_tools, ["act", "replan", "verify", "report"])
        g.add_edge("replan", "act")
        g.add_conditional_edges("verify", self.after_verify, ["act", "report"])
        g.add_edge("report", END)
        return g.compile()

    def run_task(self) -> dict:
        self.run.emit("start", goal=self.run.goal, model=settings.model, run_id=self.run.run_id)
        try:
            self.graph.invoke(
                {"messages": [], "steps": 0, "consecutive_failures": 0, "recent_failures": [],
                 "verify_attempts": 0, "phase": "planning", "claim": ""},
                {"recursion_limit": settings.max_steps * 3 + 20},
            )
        except Exception as e:
            self.run.emit("crashed", error=f"{type(e).__name__}: {e}")
            self.run.result = {"status": "crashed", "goal": self.run.goal, "error": str(e)}
            from worker.report import write_report
            write_report(self.run)
        finally:
            self.browser.close()
        return self.run.result
