"""A browser the agent drives through Playwright.

The agent never sees CSS selectors or pixel coordinates. `observe()` tags every visible
interactive element with a short ref (e1, e2, ...) and returns a compact text view of
the page; actions take a ref. Refs are re-assigned on every observation, so they stay
valid even when the page randomises its element IDs.

Every action returns a fresh observation, so the agent always sees the effect of what
it just did.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from worker.config import settings

ACTION_TIMEOUT_MS = 4000
PAGE_TEXT_CHARS = 1500

# Buttons whose label suggests they change data. Clicking one goes through the policy guard.
COMMIT_WORDS = re.compile(r"\b(save|submit|create|update|delete|remove|pay|approve|send|confirm)\b",
                          re.I)

_TAG_JS = """
() => {
  document.querySelectorAll('[data-ref]').forEach(e => e.removeAttribute('data-ref'));
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button]';
  const visible = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = e => {
    if (e.labels && e.labels.length) return e.labels[0].innerText.trim();
    return (e.getAttribute('aria-label') || e.placeholder || e.innerText || e.value || e.name || '')
      .trim().slice(0, 60);
  };
  const out = []; let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    if (!visible(e)) continue;
    const ref = 'e' + (++n); e.setAttribute('data-ref', ref);
    const item = { ref, tag: e.tagName.toLowerCase(), type: e.type || '', label: labelOf(e),
                   name: e.name || '' };
    if (e.tagName === 'SELECT') {
      item.value = e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : '';
      item.options = [...e.options].map(o => o.text).slice(0, 15);
    } else if (['INPUT', 'TEXTAREA'].includes(e.tagName)) {
      item.value = e.type === 'password' ? (e.value ? '••••' : '') : e.value;
    } else if (e.tagName === 'A') { item.href = e.getAttribute('href'); }
    item.in_dialog = !!e.closest('[role=dialog]');
    out.push(item);
  }
  const dialog = document.querySelector('[role=dialog]');
  return { elements: out, text: document.body.innerText,
           dialog: dialog ? dialog.innerText.slice(0, 300) : null };
}
"""


class Browser:
    def __init__(self, on_screenshot=None):
        self._pw = None
        self._browser = None
        self.page = None
        self.elements: dict[str, dict] = {}
        self.on_screenshot = on_screenshot  # callback(label) -> Path

    def start(self) -> None:
        if self.page:
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=settings.headless)
        self.page = self._browser.new_page(viewport={"width": 1200, "height": 900})
        self.page.set_default_timeout(ACTION_TIMEOUT_MS)

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self.page = self._browser = self._pw = None

    # --- observation -------------------------------------------------------

    def observe(self, label: str = "observe") -> str:
        self.start()
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        except PlaywrightError:
            pass
        snap = self.page.evaluate(_TAG_JS)
        self.elements = {e["ref"]: e for e in snap["elements"]}
        shot = self._screenshot(label)
        lines = [f"URL: {self.page.url}", f"Title: {self.page.title()}"]
        if snap["dialog"]:
            lines.append(f"!! A dialog is open and may block the page: {snap['dialog']!r}")
        lines.append("Interactive elements:")
        for e in snap["elements"]:
            desc = f"  [{e['ref']}] {e['tag']}"
            if e["type"] and e["tag"] == "input":
                desc += f"({e['type']})"
            desc += f" \"{e['label']}\""
            if e.get("value"):
                desc += f" value=\"{e['value']}\""
            if e.get("options"):
                desc += f" options={e['options']}"
            if e.get("href"):
                desc += f" -> {e['href']}"
            if e["in_dialog"]:
                desc += " (in dialog)"
            lines.append(desc)
        # Drop text lines that just repeat element labels; they are listed above.
        labels = {e["label"] for e in snap["elements"]}
        text = "\n".join(ln for ln in snap["text"].splitlines()
                         if ln.strip() and ln.strip() not in labels)
        if len(text) > PAGE_TEXT_CHARS:
            text = text[:PAGE_TEXT_CHARS] + " ...[page text truncated]"
        lines += ["Visible text:", text]
        if shot:
            lines.append(f"(screenshot: {shot.name})")
        return "\n".join(lines)

    def _screenshot(self, label: str):
        if not self.on_screenshot:
            return None
        path = self.on_screenshot(label)
        try:
            self.page.screenshot(path=str(path))
            return path
        except PlaywrightError:
            return None

    # --- actions -----------------------------------------------------------

    def _locate(self, ref: str):
        if ref not in self.elements:
            raise ValueError(f"Unknown element ref {ref!r}. Refs change after every page "
                             "change; call browser_read to get current refs.")
        return self.page.locator(f'[data-ref="{ref}"]')

    def describe(self, ref: str) -> dict:
        return self.elements.get(ref, {})

    def is_commit(self, ref: str) -> bool:
        e = self.elements.get(ref, {})
        clickable = e.get("tag") == "button" or e.get("type") in {"submit", "button"}
        return bool(clickable and COMMIT_WORDS.search(e.get("label", "")))

    def open(self, url: str) -> str:
        self.start()
        if url.startswith("/"):
            url = settings.company_app_url.rstrip("/") + url
        resp = self.page.goto(url, wait_until="domcontentloaded")
        status = f"HTTP {resp.status}\n" if resp else ""
        return status + self.observe(f"open {url}")

    def click(self, ref: str) -> str:
        label = self.describe(ref).get("label", ref)
        self._locate(ref).click()
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=ACTION_TIMEOUT_MS)
        except PlaywrightError:
            pass
        return self.observe(f"click {label}")

    def type(self, ref: str, text: str) -> str:
        self._locate(ref).fill(text)
        return self.observe(f"type {self.describe(ref).get('label', ref)}")

    def select(self, ref: str, option: str) -> str:
        loc = self._locate(ref)
        try:
            loc.select_option(label=option)
        except PlaywrightError:
            loc.select_option(value=option)
        return self.observe(f"select {option}")

    def fill(self, fields: dict[str, str]) -> str:
        filled, problems = [], []
        for ref, value in fields.items():
            try:
                loc = self._locate(ref)
                if self.elements[ref]["tag"] == "select":
                    try:
                        loc.select_option(label=str(value))
                    except PlaywrightError:
                        loc.select_option(value=str(value))
                else:
                    loc.fill(str(value))
                filled.append(f"{self.elements[ref]['label'] or ref}={value}")
            except (PlaywrightError, ValueError) as e:
                problems.append(f"{ref}: {str(e).splitlines()[0]}")
        if problems and not filled:
            raise ValueError("Nothing was filled. " + "; ".join(problems))
        head = f"Filled: {', '.join(filled)}"
        if problems:
            head += f"\nCould not fill: {'; '.join(problems)}"
        return head + "\n" + self.observe("fill form")

    def form_state(self) -> str:
        """Current values of every form field, used by the policy guard."""
        rows = [f"{e['label'] or e['name']}: {e.get('value', '')}"
                for e in self.elements.values() if e["tag"] in {"input", "select", "textarea"}]
        return "\n".join(rows)
