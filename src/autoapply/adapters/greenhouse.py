"""Greenhouse (job-boards.greenhouse.io) application forms, driven through the rendered DOM."""

from __future__ import annotations

import re
from pathlib import Path

from ..answers import Resolved
from ..forms import FormField, FormSpec
from .base import AdapterError, SubmitResult, best_option, query_for, real_options, settle

# Education block and other system fields have terse labels; give the resolver unambiguous ones.
LABEL_OVERRIDES = [
    (re.compile(r"^school--\d+$"), "School"),
    (re.compile(r"^degree--\d+$"), "Degree"),
    (re.compile(r"^discipline--\d+$"), "Discipline (major)"),
    (re.compile(r"^start-month--\d+$"), "Education start month"),
    (re.compile(r"^start-year--\d+$"), "Education start year"),
    (re.compile(r"^end-month--\d+$"), "Expected graduation month"),
    (re.compile(r"^end-year--\d+$"), "Expected graduation year"),
    (re.compile(r"^candidate-location$"), "Current location (city)"),
    (re.compile(r"^country$"), "Country"),
    (re.compile(r"^resume$"), "Resume/CV"),
    (re.compile(r"^cover_letter$"), "Cover Letter"),
]
TYPEAHEAD = re.compile(r"^(school--\d+|candidate-location)$")

EXTRACT_JS = r"""
(form) => {
  const txt = e => (e ? e.innerText : '').replace(/\s+/g, ' ').trim();
  const star = s => /\*\s*$/.test(s);
  const labelOf = el => {
    const byId = document.getElementById(el.id + '-label');
    if (byId) return txt(byId);
    if (el.labels && el.labels[0] && txt(el.labels[0]) && txt(el.labels[0]) !== 'Attach') return txt(el.labels[0]);
    const lb = el.getAttribute('aria-labelledby');
    if (lb && document.getElementById(lb.split(' ')[0])) return txt(document.getElementById(lb.split(' ')[0]));
    let c = el.parentElement;
    for (let i = 0; i < 5 && c; i++, c = c.parentElement) {
      const l = c.querySelector('.label, [class*="label"], label');
      if (l && txt(l) && !/^attach$/i.test(txt(l))) return txt(l);
    }
    return el.getAttribute('aria-label') || '';
  };
  const out = [], done = new Set();
  for (const fs of form.querySelectorAll('fieldset')) {
    const boxes = [...fs.querySelectorAll('input[type=checkbox], input[type=radio]')];
    if (!boxes.length) continue;
    boxes.forEach(b => done.add(b));
    const legend = txt(fs.querySelector('legend'));
    out.push({id: boxes[0].name || fs.id, kind: boxes[0].type === 'radio' ? 'radio' : 'checkboxes', label: legend,
              required: star(legend) || fs.getAttribute('aria-required') === 'true' || boxes.some(b => b.required),
              options: boxes.map(b => txt(b.labels && b.labels[0]) || b.value), optionIds: boxes.map(b => b.id)});
  }
  for (const el of form.querySelectorAll('input, textarea, select')) {
    if (done.has(el) || !el.id || ['hidden', 'search', 'submit', 'button'].includes(el.type)) continue;
    if (/^(iti|g-recaptcha)/.test(el.id)) continue;
    const label = labelOf(el);
    let kind = el.getAttribute('role') === 'combobox' ? 'combobox'
      : el.tagName === 'TEXTAREA' ? 'textarea' : el.type === 'file' ? 'file' : el.type === 'number' ? 'number'
      : el.tagName === 'SELECT' ? 'nativeselect' : el.type === 'checkbox' ? 'checkbox' : 'text';
    out.push({id: el.id, kind, label,
              required: el.getAttribute('aria-required') === 'true' || el.required || star(label),
              maxLength: el.maxLength > 0 ? el.maxLength : null,
              options: kind === 'nativeselect' ? [...el.options].map(o => o.text.trim()).filter(Boolean) : []});
  }
  return out;
}
"""


def form_url(url: str, key: str) -> str:
    if m := re.search(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", url):
        if m.group(1) != "embed":
            return f"https://job-boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}"
    job_id = key.split(":", 1)[1]
    return f"https://boards.greenhouse.io/embed/job_app?token={job_id}"


def _clean_label(label: str) -> str:
    return re.sub(r"\s*\*\s*$", "", label).strip()


def build_spec(raw: list[dict], *, company: str, title: str, url: str, description: str,
               combobox_options: dict[str, list[str]]) -> FormSpec:
    """Pure: raw DOM field records -> FormSpec. meta['kinds'] keeps the widget kind per field id."""
    fields: list[FormField] = []
    kinds: dict[str, str] = {}
    option_ids: dict[str, list[str]] = {}
    for r in raw:
        fid, kind = r["id"], r["kind"]
        label = next((lab for rx, lab in LABEL_OVERRIDES if rx.match(fid)), None) or _clean_label(r["label"])
        if not label:
            continue
        options: tuple[str, ...] = ()
        if kind == "combobox" and TYPEAHEAD.match(fid):
            kind, ftype = "typeahead", "text"
        elif kind == "combobox":
            options = real_options(combobox_options.get(fid, ()))
            ftype = "select"
        elif kind == "nativeselect":
            options, ftype = real_options(r["options"]), "select"
        elif kind == "checkboxes":
            options, ftype = tuple(r["options"]), "multiselect"
            option_ids[fid] = r["optionIds"]
        elif kind == "radio":
            options, ftype = tuple(r["options"]), "radio"
            option_ids[fid] = r["optionIds"]
        elif kind in ("file", "textarea", "number", "checkbox"):
            ftype = kind
        else:
            ftype = "text"
        kinds[fid] = kind
        fields.append(FormField(id=fid, label=label, type=ftype, required=bool(r["required"]), options=options,
                                max_length=r.get("maxLength")))
    return FormSpec(company=company, title=title, url=url, fields=fields, description=description,
                    meta={"kinds": kinds, "option_ids": option_ids})


def _loc(page, fid: str):
    return page.locator(f'[id="{fid}"]')


def _combobox_options(page, fid: str) -> list[str]:
    box = _loc(page, fid)
    box.click()
    page.wait_for_timeout(250)
    opts = page.eval_on_selector_all(f'[id^="react-select-{fid}-option-"]', "os => os.map(o => o.innerText.trim())")
    page.keyboard.press("Escape")
    return opts


class GreenhouseAdapter:
    ats = "greenhouse"

    def form_url(self, url: str, key: str) -> str:
        return form_url(url, key)

    def load(self, page, url: str, key: str, company: str, title: str) -> FormSpec:
        target = form_url(url, key)
        resp = page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        if resp is not None and resp.status >= 400:
            raise AdapterError(f"HTTP {resp.status} for {target}")
        try:
            page.wait_for_selector("#application-form, form#application_form", timeout=20_000)
        except Exception as e:
            raise AdapterError(f"no application form at {page.url} (closed posting?)") from e
        settle(page)
        form = page.query_selector("#application-form") or page.query_selector("form#application_form")
        raw = form.evaluate(EXTRACT_JS)
        combos = {r["id"]: _combobox_options(page, r["id"]) for r in raw
                  if r["kind"] == "combobox" and not TYPEAHEAD.match(r["id"])}
        desc_el = page.query_selector(".job__description, #content, .job-post-content")
        description = desc_el.inner_text() if desc_el else ""
        return build_spec(raw, company=company, title=title, url=page.url, description=description,
                          combobox_options=combos)

    def fill(self, page, spec: FormSpec, answers: dict[str, Resolved]) -> list[str]:
        problems: list[str] = []
        kinds, option_ids = spec.meta["kinds"], spec.meta["option_ids"]
        by_id = {f.id: f for f in spec.fields}
        for fid, ans in answers.items():
            f, kind, value = by_id[fid], kinds[fid], ans.value
            try:
                if kind == "file":
                    _loc(page, fid).set_input_files(str(Path(value)))
                elif kind in ("text", "textarea", "number"):
                    _loc(page, fid).fill(str(value))
                elif kind == "checkbox":
                    if value is True or value == ["Yes"]:
                        _loc(page, fid).check()
                elif kind in ("checkboxes", "radio"):
                    chosen = value if isinstance(value, list) else [value]
                    for opt in chosen:
                        _loc(page, option_ids[fid][f.options.index(opt)]).check()
                elif kind == "nativeselect":
                    _loc(page, fid).select_option(label=str(value))
                elif kind == "combobox":
                    self._choose(page, fid, str(value), exact=True)
                elif kind == "typeahead":
                    self._choose(page, fid, str(value), exact=False)
            except Exception as e:  # noqa: BLE001 - any widget failure is reported, not fatal to the run
                problems.append(f"{f.label[:60]}: {type(e).__name__}: {str(e)[:120]}")
        return problems

    def _choose(self, page, fid: str, value: str, *, exact: bool) -> None:
        box = _loc(page, fid)
        box.click()
        box.fill(value if exact else query_for(value))
        sel = f'[id^="react-select-{fid}-option-"]'
        page.wait_for_selector(sel, timeout=10_000)
        page.wait_for_timeout(400 if exact else 1200)
        opts = page.eval_on_selector_all(sel, "os => os.map(o => o.innerText.trim())")
        pick = value if exact and value in opts else best_option(opts, value)
        if pick is None:
            page.keyboard.press("Escape")
            raise AdapterError(f"no option for {value!r} among {opts[:6]}")
        page.locator(sel).nth(opts.index(pick)).click()

    def submit(self, page) -> SubmitResult:
        start = page.url
        page.locator("button[type=submit]").last.click()
        for _ in range(40):
            page.wait_for_timeout(500)
            body = page.inner_text("body").lower()
            if "confirmation" in page.url or "thank you for applying" in body or "application has been submitted" in body \
                    or "application was submitted" in body:
                return SubmitResult("submitted", page.url)
            if re.search(r"security code|verification code|enter the code", body):
                return SubmitResult("manual", "email security code required")
            if page.query_selector('iframe[src*="recaptcha"][src*="bframe"]:visible, iframe[title*="challenge"]'):
                return SubmitResult("manual", "captcha challenge")
        errors = page.eval_on_selector_all('[id$="-error"], .helper-text--error, .error-message',
                                           "es => es.map(e => e.innerText.trim()).filter(Boolean)")
        return SubmitResult("failed", f"no confirmation after submit ({page.url == start and 'same page'}); "
                                      f"errors: {errors[:5]}")
