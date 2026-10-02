"""Read-only access to the company workspace (documents, policies, the invoice inbox)."""

from pathlib import Path

import pdfplumber

from worker.config import settings

MAX_CHARS = 6000


def _resolve(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        # Accept both "inbox/x.pdf" and "company_data/inbox/x.pdf".
        parts = p.parts[1:] if p.parts and p.parts[0] == settings.workspace.name else p.parts
        p = settings.workspace.joinpath(*parts)
    p = p.resolve()
    if settings.workspace.resolve() not in [p, *p.parents]:
        raise PermissionError(f"{path} is outside the company workspace; access denied.")
    return p


def list_files(folder: str = ".") -> str:
    p = _resolve(folder)
    if not p.exists():
        raise FileNotFoundError(f"No such folder: {folder}")
    rows = []
    for f in sorted(p.rglob("*")):
        if f.is_file() and not f.name.startswith(".") and f.suffix != ".py":
            rel = f.relative_to(settings.workspace)
            rows.append(f"{rel}  ({f.stat().st_size} bytes)")
    return "\n".join(rows) or "(empty)"


def read_file(path: str) -> str:
    p = _resolve(path)
    if not p.exists():
        raise FileNotFoundError(f"No such file: {path}. Use list_files to see what exists.")
    if p.suffix.lower() == ".pdf":
        with pdfplumber.open(p) as pdf:
            text = "\n\n".join(page.extract_text() or "" for page in pdf.pages)
        if not text.strip():
            return "(PDF has no extractable text — it may be a scanned image.)"
    else:
        text = p.read_text(errors="replace")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n...[truncated, {len(text) - MAX_CHARS} more chars]"
    return text
