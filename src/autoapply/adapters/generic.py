"""Generic single-page application forms (Workable, JazzHR/applytojob, BambooHR, Breezy, Rippling, Jobvite, ...).

Reads every visible labelled control, answers through the same resolver, fills native widgets only. Anything
exotic (custom comboboxes, multi-step wizards, logins) makes the application fail -> manual list, never a guess.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..answers import Resolved
from ..forms import FormField, FormSpec
from .base import AdapterError, SubmitResult, real_options, settle

HOSTS = {"workable.com": "workable", "applytojob.com": "jazzhr", "bamboohr.com": "bamboohr", "breezy.hr": "breezy",
         "rippling.com": "rippling", "jobvite.com": "jobvite"}

EXTRACT_JS = r"""
() => {
  const txt = e => (e ? e.innerText || e.textContent || '' : '').replace(/\s+/g, ' ').trim();
  const visible = e => !!(e.offsetParent || e.type === 'file');
  const labelOf = e => {
    if (e.labels && e.labels[0] && txt(e.labels[0])) return txt(e.labels[0]);
    const lb = e.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(' ').map(i => txt(document.getElementById(i))).join(' ').trim(); if (t) return t; }
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label');
    const ph = (e.placeholder || '').trim();
    if (ph.length > 1 && !/^(type|enter|select|choose|search|e\.g|ex\b|your answer|start typing|\.\.\.)/i.test(ph)) return ph;
    let c = e.parentElement;
    for (let i = 0; i < 4 && c; i++, c = c.parentElement) {
      // the nearest label that isn't bound to a different control, and whose group holds only this control
      const l = [...c.querySelectorAll('label, legend, [class*=label], [class*=Label]')].find(x =>
          txt(x) && !x.contains(e) && (!x.htmlFor || x.htmlFor === e.id));
      const controls = c.querySelectorAll('input:not([type=hidden]), textarea, select').length;
      if (l && controls === 1) return txt(l);
      if (l && controls > 1 && i === 0) return txt(l);
    }
    return '';  // unlabelled: never guess what a field means
  };
  const out = [], seen = new Set();
  const all = [...document.querySelectorAll('input, textarea, select')].filter(e =>
      !['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(e.type) && visible(e) && !/captcha/i.test(e.name || e.id));
  all.forEach((e, i) => {
    if (e.dataset.aaIdx === undefined) e.dataset.aaIdx = String(i);
    if (e.type === 'radio' || e.type === 'checkbox') {
      const group = e.name ? all.filter(x => x.name === e.name && x.type === e.type) : [e];
      const gid = 'g:' + (e.name || e.dataset.aaIdx);
      if (seen.has(gid)) return;
      seen.add(gid);
      group.forEach((g, j) => { if (g.dataset.aaIdx === undefined) g.dataset.aaIdx = String(i) + '_' + j; });
      const fs = e.closest('fieldset');
      const q = (fs && txt(fs.querySelector('legend'))) || (group.length > 1 ? labelOf(e.parentElement.parentElement) : labelOf(e));
      out.push({id: gid, kind: group.length === 1 && e.type === 'checkbox' ? 'checkbox' : e.type,
                label: q || labelOf(e), required: group.some(g => g.required || g.getAttribute('aria-required') === 'true'),
                options: group.map(g => txt(g.labels && g.labels[0]) || g.value), optionIdx: group.map(g => g.dataset.aaIdx)});
      return;
    }
    let label = labelOf(e);
    if (e.type === 'file' && !/resume|cv|cover|transcript/i.test(label)) {  // upload widgets often have junk labels
      let c = e.parentElement, found = '';
      for (let i = 0; i < 6 && c && !found; i++, c = c.parentElement) {
        const t = txt(c);
        if (/cover letter/i.test(t) && !/resume|\bcv\b/i.test(t)) found = 'Cover letter';
        else if (/resume|\bcv\b/i.test(t)) found = 'Resume';
      }
      label = found || label;
    }
    out.push({id: 'i:' + e.dataset.aaIdx, kind: e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'textarea' : e.type,
              label: label, required: e.required || e.getAttribute('aria-required') === 'true' || /\*\s*$/.test(labelOf(e)),
              maxLength: e.maxLength > 0 ? e.maxLength : null,
              options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()) : []});
  });
  return out;
}
"""


def _tick(box) -> None:
    """Check a radio/checkbox, including visually-hidden inputs behind custom widgets (click their label)."""
    try:
        box.check(timeout=4000)
    except Exception:  # noqa: BLE001 - custom widget swallowed the click
        box.locator("xpath=ancestor::label[1]").first.click(timeout=4000)
    if not box.is_checked():
        box.evaluate("e => { e.click(); }")
    if not box.is_checked():
        raise AdapterError("option did not stay selected")


def dismiss_cookie_banner(page) -> None:
    """Cookie banners cover Apply buttons; decline where possible, else accept (only necessary cookies matter)."""
    for name in (r"^(decline|reject)( all)?$", r"^(accept|allow)( all)?( cookies)?$", r"^(ok|got it|i agree)$"):
        btn = page.get_by_role("button", name=re.compile(name, re.I))
        if btn.count():
            try:
                btn.first.click(timeout=3000)
                page.wait_for_timeout(500)
                return
            except Exception:  # noqa: BLE001
                continue


def ats_for_host(host: str) -> str | None:
    return next((name for h, name in HOSTS.items() if host == h or host.endswith("." + h)), None)


def build_spec(raw: list[dict], *, company: str, title: str, url: str, description: str) -> FormSpec:
    fields, kinds, option_idx = [], {}, {}
    for r in raw:
        label = re.sub(r"^\s*\*\s*|\s*\*\s*$", "", r["label"] or "").strip()
        if not label:
            continue
        kind = r["kind"]
        ftype = {"select": "select", "textarea": "textarea", "file": "file", "radio": "radio", "checkbox": "checkbox",
                 "number": "number", "date": "date"}.get(kind, "text")
        options = real_options(r.get("options") or ())
        if kind == "checkbox" and len(r.get("options") or ()) > 1:
            ftype = "multiselect"
        if kind in ("radio", "checkbox"):
            option_idx[r["id"]] = dict(zip(r["options"], r["optionIdx"]))
            if ftype == "checkbox":
                options = ()
        kinds[r["id"]] = kind
        fields.append(FormField(id=r["id"], label=label, type=ftype, required=bool(r["required"]), options=options,
                                max_length=r.get("maxLength")))
    return FormSpec(company=company, title=title, url=url, fields=fields, description=description,
                    meta={"kinds": kinds, "option_idx": option_idx})


class GenericAdapter:
    ats = "generic"

    def form_url(self, url: str, key: str) -> str:
        return url

    def load(self, page, url: str, key: str, company: str, title: str) -> FormSpec:
        try:
            return self._load(page, url, company, title)
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001 - timeouts, detached nodes: an unsupported page, not a crash
            raise AdapterError(f"generic form not usable: {type(e).__name__}: {str(e)[:160]}") from e

    def _load(self, page, url: str, company: str, title: str) -> FormSpec:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        settle(page)
        dismiss_cookie_banner(page)
        description = page.inner_text("body")[:8000]
        if not page.query_selector("input[type=file], form input[type=email]"):
            apply = page.get_by_role("link", name=re.compile(r"^\s*apply", re.I)).or_(
                page.get_by_role("button", name=re.compile(r"^\s*apply", re.I))).first
            if apply.count() == 0:
                raise AdapterError("no application form or Apply button")
            apply.click()
            page.wait_for_timeout(3000)
            settle(page)
        if re.search(r"sign in|log in|create (an )?account", page.inner_text("body")[:3000], re.I) and \
                page.query_selector("input[type=password]"):
            raise AdapterError("login required")
        raw = page.evaluate(EXTRACT_JS)
        if not any(r["kind"] == "file" for r in raw):
            raise AdapterError("no resume upload on the form (multi-step or unsupported)")
        return build_spec(raw, company=company, title=title, url=page.url, description=description)

    def fill(self, page, spec: FormSpec, answers: dict[str, Resolved]) -> list[str]:
        problems = []
        kinds, option_idx = spec.meta["kinds"], spec.meta["option_idx"]
        by_id = {f.id: f for f in spec.fields}
        for fid in sorted(answers, key=lambda k: kinds[k] != "file"):
            f, kind, value = by_id[fid], kinds[fid], answers[fid].value
            try:
                if fid.startswith("i:"):
                    el = page.locator(f'[data-aa-idx="{fid[2:]}"]')
                    if kind == "file":
                        el.set_input_files(str(Path(value)))
                        page.wait_for_timeout(2500)
                    elif kind == "select":
                        el.select_option(label=str(value))
                    else:
                        el.fill(str(value))
                else:
                    idx = option_idx[fid]
                    if kind == "checkbox" and not isinstance(value, list):
                        if value is True:
                            _tick(page.locator(f'[data-aa-idx="{next(iter(idx.values()))}"]'))
                        continue
                    for opt in value if isinstance(value, list) else [value]:
                        _tick(page.locator(f'[data-aa-idx="{idx[opt]}"]'))
            except Exception as e:  # noqa: BLE001
                problems.append(f"{f.label[:60]}: {type(e).__name__}: {str(e)[:100]}")
        return problems

    def submit(self, page) -> SubmitResult:
        btn = page.get_by_role("button", name=re.compile(r"submit|send application|apply", re.I)).last
        btn.click()
        for _ in range(40):
            page.wait_for_timeout(500)
            body = page.inner_text("body").lower()
            if re.search(r"thank you|application (was |has been )?(received|submitted)|successfully (submitted|applied)",
                         body):
                return SubmitResult("submitted", page.url)
            if page.query_selector('iframe[src*="captcha"][src*="challenge"], iframe[title*="challenge"]'):
                return SubmitResult("manual", "captcha challenge")
        return SubmitResult("failed", "no confirmation after submit")
