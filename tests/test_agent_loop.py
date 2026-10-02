"""Exercise the control loop with a scripted model, so the wiring is tested without an API key."""

from langchain_core.messages import AIMessage

from worker import agent as agent_mod
from worker.run import HumanChannel, RunContext


class ScriptedLLM:
    """Returns queued replies: AIMessages for invoke(), pydantic objects for structured output."""

    def __init__(self, replies):
        self.replies = list(replies)

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema):
        return self

    def invoke(self, _):
        return self.replies.pop(0)


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"id-{name}"}])


class ScriptedHuman(HumanChannel):
    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def ask(self, question, kind="question"):
        self.asked.append((kind, question))
        return self.answers.pop(0)


PLAN = agent_mod.Plan(outcome="done", success_criteria=["c1"], steps=["read policy", "do it"])
PASS = agent_mod.Verdict(passed=True, checks=[agent_mod.Check(criterion="c1", passed=True,
                                                               evidence="seen")])
FAIL = agent_mod.Verdict(passed=False, checks=[agent_mod.Check(criterion="c1", passed=False,
                                                                evidence="record missing")])


def make(monkeypatch, tmp_path, replies, human=None):
    monkeypatch.setattr(agent_mod, "get_llm", lambda: ScriptedLLM(replies))
    monkeypatch.setattr("worker.run.settings", type("S", (), {"runs_dir": tmp_path})())
    run = RunContext(goal="test goal", human=human or ScriptedHuman([]))
    return run, agent_mod.Worker(run)


def test_happy_path_plans_acts_verifies(monkeypatch, tmp_path):
    run, w = make(monkeypatch, tmp_path, [
        PLAN,
        call("read_file", path="policies.md"),
        call("note", fact="threshold is 50k"),
        call("finish", summary="did it"),
        AIMessage(content="checked"),  # verifier looks, then stops
        PASS,
    ])
    result = w.run_task()
    assert result["status"] == "completed"
    assert run.facts == ["threshold is 50k"]
    assert (run.dir / "report.md").exists() and (run.dir / "trace.jsonl").exists()


def test_repeated_failures_trigger_replan(monkeypatch, tmp_path):
    run, w = make(monkeypatch, tmp_path, [
        PLAN,
        call("read_file", path="nope1.md"),
        call("read_file", path="nope2.md"),
        call("read_file", path="nope3.md"),
        agent_mod.Replan(diagnosis="wrong file names", remaining_steps=["list files first"]),
        call("finish", summary="gave up honestly"),
        AIMessage(content="checked"),
        PASS,
    ])
    w.run_task()
    types = [e["type"] for e in run.events]
    assert "replanned" in types
    assert run.plan[-1].description == "list files first"


def test_failed_verification_sends_worker_back(monkeypatch, tmp_path):
    run, w = make(monkeypatch, tmp_path, [
        PLAN,
        call("finish", summary="claimed done"),
        AIMessage(content="looked"), FAIL,
        call("note", fact="fixing the gap"),
        call("finish", summary="actually done"),
        AIMessage(content="looked"), PASS,
    ])
    result = w.run_task()
    assert result["status"] == "completed"
    assert [e["passed"] for e in run.events if e["type"] == "verdict"] == [False, True]


def test_guard_asks_human_and_respects_rejection(monkeypatch, tmp_path):
    human = ScriptedHuman(["reject - too expensive"])
    run, w = make(monkeypatch, tmp_path, [
        agent_mod.PolicyDecision(decision="needs_approval", reason="over INR 50,000"),
    ], human=human)

    class FakePage:
        url = "http://app/invoices/new"

        def inner_text(self, _):
            return "form"

    w.browser.page = FakePage()
    w.browser.elements = {"e9": {"tag": "button", "type": "submit", "label": "Save invoice"}}
    refusal = w._guard("e9")
    assert human.asked and human.asked[0][0] == "approval"
    assert refusal.startswith("REJECTED")
    assert run.approvals[0]["approved"] is False


def test_non_commit_clicks_skip_the_guard(monkeypatch, tmp_path):
    run, w = make(monkeypatch, tmp_path, [])
    w.browser.elements = {"e1": {"tag": "a", "type": "", "label": "New invoice"}}
    assert w._guard("e1") is None


def test_finish_refused_while_items_open(monkeypatch, tmp_path):
    run, w = make(monkeypatch, tmp_path, [
        PLAN,
        call("track_items", items=["a.pdf", "b.pdf"]),
        call("resolve_item", item="a.pdf", status="done", note="record 4"),
        call("finish", summary="done"),          # refused: b.pdf still open
        call("resolve_item", item="b.pdf", status="skipped", note="no due date; asked"),
        call("finish", summary="done"),
        AIMessage(content="checked"), PASS,
    ])
    result = w.run_task()
    refused = [e for e in run.events if e["type"] == "observation" and e["tool"] == "finish"
               and not e["ok"]]
    assert refused and "b.pdf" in refused[0]["result"]
    assert result["status"] == "completed"
