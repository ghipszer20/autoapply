"""Lever (jobs.lever.co/{company}/{id}/apply): plain HTML form, fields addressed by input name."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..answers import Resolved
from ..forms import FormField, FormSpec
from .base import AdapterError, SubmitResult

_URL = re.compile(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})")

EXTRACT_JS = r"""
(form) => {
  const txt = e => (e ? e.innerText : '').replace(/\s+/g, ' ').trim();
  const out = [], seen = new Set();
  for (const q of form.querySelectorAll('.application-question')) {
    const inputs = [...q.querySelectorAll('input:not([type=hidden]), textarea, select')].filter(i => i.name);
    if (!inputs.length) continue;
    const label = txt(q.querySelector('.application-label')) || txt(q.querySelector('label'));
    for (const name of [...new Set(inputs.map(i => i.name))]) {
      if (seen.has(name)) continue;
      seen.add(name);
      const els = inputs.filter(i => i.name === name), e = els[0];
      const kind = e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'textarea' : e.type === 'file' ? 'file'
        : e.type === 'radio' ? 'radio' : e.type === 'checkbox' ? 'checkboxes' : 'text';
      const multi = kind === 'radio' || kind === 'checkboxes';
      out.push({name, kind, label, required: els.some(x => x.required) || /[✱*]\s*$/.test(label),
                options: kind === 'select' ? [...e.options].map(o => o.text.trim())
                         : multi ? els.map(x => txt(x.closest('label')) || x.value) : [],
                values: multi ? els.map(x => x.value) : []});
    }
  }
  return out;
}
"""
BASE_LABELS = {"name": "Full name", "email": "Email", "phone": "Phone", "org": "Current company",
               "location": "Current location", "resume": "Resume/CV", "urls[LinkedIn]": "LinkedIn URL",
               "urls[GitHub]": "GitHub URL", "urls[Portfolio]": "Portfolio URL", "urls[Twitter]": "Twitter URL",
               "urls[Other]": "Other website", "eeo[gender]": "Gender", "eeo[race]": "Race",
               "eeo[veteran]": "Veteran status", "eeo[disability]": "Disability status"}
SKIP_OPTIONAL = {"urls[Twitter]", "urls[Other]", "org", "location"}  # nothing true to put there


def parse_url(url: str) -> tuple[str, str]:
    m = _URL.search(url)
    if not m:
        raise AdapterError(f"not a Lever job URL: {url}")
    return m.group(1), m.group(2)


def _clean(label: str) -> str:
    return re.sub(r"\s*[✱*]\s*$", "", label).strip()


def build_spec(raw: list[dict], *, company: str, title: str, url: str, description: str) -> FormSpec:
    fields, kinds, values = [], {}, {}
    for r in raw:
        name, kind = r["name"], r["kind"]
        if name in SKIP_OPTIONAL and not r["required"]:
            continue
        label = BASE_LABELS.get(name) or _clean(r["label"])
        if not label:
            continue
        options = tuple(o for o in r["options"] if o and not o.lower().startswith("select"))
        ftype = {"select": "select", "textarea": "textarea", "file": "file", "radio": "radio",
                 "checkboxes": "multiselect"}.get(kind, "text")
        kinds[name] = kind
        if kind in ("radio", "checkboxes"):
            values[name] = dict(zip(r["options"], r["values"]))
        fields.append(FormField(id=name, label=label, type=ftype, required=bool(r["required"]), options=options))
    # resume first: Lever parses it and autofills fields, which we then overwrite
    fields.sort(key=lambda f: f.type != "file")
    return FormSpec(company=company, title=title, url=url, fields=fields, description=description,
                    meta={"kinds": kinds, "values": values})


class LeverAdapter:
    ats = "lever"

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30)

    def form_url(self, url: str, key: str) -> str:
        co, jid = parse_url(url)
        return f"https://jobs.lever.co/{co}/{jid}/apply"

    def _description(self, co: str, jid: str) -> str:
        try:
            j = self.client.get(f"https://api.lever.co/v0/postings/{co}/{jid}").json()
        except (httpx.HTTPError, ValueError):
            return ""
        parts = [j.get("descriptionPlain", "")] + [f"{x.get('text', '')}: {re.sub(r'<[^>]+>', ' ', x.get('content', ''))}"
                                                   for x in j.get("lists", [])] + [j.get("additionalPlain", "")]
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def load(self, page, url: str, key: str, company: str, title: str) -> FormSpec:
        co, jid = parse_url(url)
        target = self.form_url(url, key)
        resp = page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        if resp is not None and resp.status >= 400:
            raise AdapterError(f"HTTP {resp.status} for {target} (closed posting?)")
        try:
            page.wait_for_selector("form .application-question", timeout=20_000)
        except Exception as e:
            raise AdapterError(f"no application form at {page.url}") from e
        raw = page.eval_on_selector("form#application-form, form", EXTRACT_JS)
        return build_spec(raw, company=company, title=title, url=target, description=self._description(co, jid))

    def fill(self, page, spec: FormSpec, answers: dict[str, Resolved]) -> list[str]:
        problems: list[str] = []
        kinds, values = spec.meta["kinds"], spec.meta["values"]
        by_id = {f.id: f for f in spec.fields}
        order = sorted(answers, key=lambda k: kinds[k] != "file")
        for name in order:
            f, kind, value = by_id[name], kinds[name], answers[name].value
            sel = f'[name="{name}"]'
            try:
                if kind == "file":
                    page.locator(f"input{sel}").set_input_files(str(Path(value)))
                    page.wait_for_timeout(3000)  # let Lever's resume parser finish its autofill
                elif kind == "select":
                    page.locator(f"select{sel}").select_option(label=str(value))
                elif kind in ("radio", "checkboxes"):
                    for opt in value if isinstance(value, list) else [value]:
                        page.locator(f'input{sel}[value="{values[name][opt]}"]').check()
                else:
                    page.locator(sel).first.fill(str(value))
            except Exception as e:  # noqa: BLE001
                problems.append(f"{f.label[:60]}: {type(e).__name__}: {str(e)[:120]}")
        return problems

    def submit(self, page) -> SubmitResult:
        page.locator("#btn-submit, button[type=submit]").last.click()
        for _ in range(40):
            page.wait_for_timeout(500)
            body = page.inner_text("body").lower()
            if "/thanks" in page.url or re.search(r"application (was )?submitted|thank you for (applying|your application)", body):
                return SubmitResult("submitted", page.url)
            if page.query_selector('iframe[src*="hcaptcha"][src*="challenge"], iframe[title*="challenge"]'):
                return SubmitResult("manual", "hCaptcha challenge")
        errors = page.eval_on_selector_all(".error-message, .application-error, [class*=error]",
                                           "es => es.map(e => e.innerText.trim()).filter(Boolean)")
        return SubmitResult("failed", f"no confirmation after submit; errors: {errors[:5]}")
