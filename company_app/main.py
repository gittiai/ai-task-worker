"""Acme Ops: a small fake internal company system for the AI worker to operate.

It is a real web app (sessions, forms, validation, a database) so the agent has to
drive it through a browser like a person would. CHAOS_MODE=1 makes it misbehave on
purpose: a blocking popup, randomised element IDs, and a transient server error.
"""

import os
import random
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from company_app import db

USERS = {"ops@acme.test": "demo123"}
VENDORS = [
    "Globex Corporation",
    "Initech Pvt Ltd",
    "Umbrella Supplies",
    "Stark Logistics",
    "Wayne Office Co",
    "Hooli Cloud Services",
]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Acme Ops", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(16))
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
_submit_attempts: dict[str, int] = {}


def chaos() -> bool:
    return os.getenv("CHAOS_MODE", "0") == "1"


def ctx(request: Request, **extra):
    salt = random.randint(1000, 9999)

    def rid(base: str) -> str:
        # Random per page load, but stable within it so <label for> still matches.
        return f"{base}-{salt}" if chaos() else base

    return {
        "request": request,
        "user": request.session.get("user"),
        "chaos": chaos(),
        "rid": rid,
        "submit_label": random.choice(["Save invoice", "Create record", "Submit"])
        if chaos()
        else "Save invoice",
        **extra,
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/invoices", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", ctx(request))


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    if USERS.get(email.strip().lower()) != password:
        return templates.TemplateResponse(
            request, "login.html", ctx(request, error="Invalid email or password."), status_code=401
        )
    request.session["user"] = email.strip().lower()
    return RedirectResponse("/invoices", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/invoices", response_class=HTMLResponse)
def list_invoices(request: Request, q: str = "", status: str = ""):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    conn = db.connect()
    sql, args = "SELECT * FROM invoices WHERE 1=1", []
    if q:
        sql += " AND (vendor LIKE ? OR invoice_number LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if status:
        sql += " AND status = ?"
        args.append(status)
    rows = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
    conn.close()
    return templates.TemplateResponse(
        request,
        "invoices.html",
        ctx(request, invoices=rows, q=q, status=status, today=date.today().isoformat()),
    )


@app.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_page(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request, "new_invoice.html", ctx(request, vendors=VENDORS, form={}, errors=[])
    )


@app.post("/invoices/new", response_class=HTMLResponse)
def create_invoice(
    request: Request,
    vendor: str = Form(""),
    invoice_number: str = Form(""),
    amount: str = Form(""),
    currency: str = Form("INR"),
    due_date: str = Form(""),
    notes: str = Form(""),
):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = dict(
        vendor=vendor, invoice_number=invoice_number.strip(), amount=amount, currency=currency,
        due_date=due_date, notes=notes,
    )

    # Transient failure: the first submit of a session fails in chaos mode.
    if chaos():
        n = _submit_attempts.get(user, 0)
        _submit_attempts[user] = n + 1
        if n == 0:
            return templates.TemplateResponse(
                request, "new_invoice.html",
                ctx(request, vendors=VENDORS, form=form,
                    errors=["503: The ledger service is busy. Please try again."]),
                status_code=503,
            )

    errors = []
    if vendor not in VENDORS:
        errors.append("Vendor must be selected from the list.")
    if not form["invoice_number"]:
        errors.append("Invoice number is required.")
    try:
        amount_val = round(float(amount.replace(",", "")), 2)
        if amount_val <= 0:
            raise ValueError
    except ValueError:
        errors.append("Amount must be a positive number (no currency symbols).")
        amount_val = 0.0
    try:
        date.fromisoformat(due_date)
    except ValueError:
        errors.append("Due date must be in YYYY-MM-DD format.")

    conn = db.connect()
    if form["invoice_number"] and conn.execute(
        "SELECT 1 FROM invoices WHERE vendor = ? AND invoice_number = ?",
        (vendor, form["invoice_number"]),
    ).fetchone():
        errors.append(f"Invoice {form['invoice_number']} from {vendor} already exists.")

    if errors:
        conn.close()
        return templates.TemplateResponse(
            request, "new_invoice.html", ctx(request, vendors=VENDORS, form=form, errors=errors),
            status_code=422,
        )
    cur = conn.execute(
        "INSERT INTO invoices (vendor, invoice_number, amount, currency, due_date, notes,"
        " created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (vendor, form["invoice_number"], amount_val, currency, due_date, notes, user),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return RedirectResponse(f"/invoices/{new_id}?created=1", status_code=303)


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int, created: int = 0):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    conn = db.connect()
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    conn.close()
    if not row:
        return HTMLResponse("Invoice not found", status_code=404)
    return templates.TemplateResponse(
        request, "invoice_detail.html", ctx(request, inv=row, created=created)
    )


@app.post("/invoices/{invoice_id}/status")
def set_status(request: Request, invoice_id: int, status: str = Form(...)):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    if status not in {"open", "flagged", "paid"}:
        return HTMLResponse("Bad status", status_code=400)
    conn = db.connect()
    conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    conn.commit()
    conn.close()
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


@app.post("/_admin/reset")
def reset():
    """Test-only: restore the seed data between demo runs."""
    db.init_db(reset=True)
    _submit_attempts.clear()
    return JSONResponse({"ok": True})
