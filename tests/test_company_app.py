import pytest
from fastapi.testclient import TestClient

from company_app import db
from company_app import main as app_main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setenv("CHAOS_MODE", "0")
    db.init_db()
    c = TestClient(app_main.app)
    c.post("/login", data={"email": "ops@acme.test", "password": "demo123"})
    return c


def new(client, **over):
    data = {"vendor": "Stark Logistics", "invoice_number": "STK-1042", "amount": "24500.00",
            "currency": "INR", "due_date": "2026-10-30", "notes": ""} | over
    return client.post("/invoices/new", data=data, follow_redirects=False)


def test_requires_login(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    r = TestClient(app_main.app).get("/invoices", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_create_and_read_back(client):
    r = new(client)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "STK-1042" in page and "24500.00" in page


def test_validation_rejects_bad_formats(client):
    r = new(client, amount="₹24,500", due_date="30/10/2026")
    assert r.status_code == 422
    assert "positive number" in r.text and "YYYY-MM-DD" in r.text


def test_duplicate_is_refused(client):
    r = new(client, vendor="Globex Corporation", invoice_number="GLX-2026-0815")
    assert r.status_code == 422 and "already exists" in r.text


def test_chaos_first_submit_fails_then_succeeds(client, monkeypatch):
    monkeypatch.setenv("CHAOS_MODE", "1")
    app_main._submit_attempts.clear()
    assert new(client).status_code == 503
    assert new(client).status_code == 303
