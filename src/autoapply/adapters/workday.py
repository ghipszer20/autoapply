"""Workday (*.myworkdayjobs.com): account per tenant, then a multi-page application flow.

Status: PILOT. Built against Workday's stable data-automation-id attributes, but everything after the account
step can only be exercised live (dry runs create no accounts). Keep "workday" out of run.ats_enabled until one
supervised run works: `autoapply apply <workday key> --live --headed`.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from ..answers import Resolution
from ..forms import FormField, FormSpec
from .base import AdapterError, SubmitResult, real_options, settle

KEYRING_SERVICE = "autoapply-workday"
MAX_PAGES = 12

EXTRACT_JS = r"""
() => {
  const txt = e => (e ? e.innerText : '').replace(/\s+/g, ' ').trim();
  const out = [];
  const boxes = [...document.querySelectorAll('[data-automation-id^="formField-"]')];
  boxes.forEach((box, i) => {
    if (!box.offsetParent) return;  // hidden
    const label = txt(box.querySelector('label, legend')) || box.getAttribute('data-automation-id').slice(10);
    const required = /\*/.test(label) || !!box.querySelector('[aria-required=true], [required]');
    const id = box.getAttribute('data-automation-id') + '#' + i;
    const radios = [...box.querySelectorAll('input[type=radio]')];
    const checks = [...box.querySelectorAll('input[type=checkbox]')];
    let kind = 'text', options = [];
    if (box.querySelector('input[type=file]')) kind = 'file';
    else if (box.querySelector('button[aria-haspopup=listbox]')) kind = 'dropdown';
    else if (box.querySelector('[data-automation-id=multiselectInputContainer], [data-uxi-widget-type=selectinput]')) kind = 'prompt';
    else if (radios.length) { kind = 'radio'; options = radios.map(r => txt(r.closest('div').querySelector('label')) || r.value); }
    else if (checks.length === 1) kind = 'checkbox';
    else if (checks.length > 1) { kind = 'checkboxes'; options = checks.map(c => txt(c.closest('div').querySelector('label')) || c.value); }
    else if (box.querySelector('[data-automation-id=dateSectionMonth-input], [data-automation-id=dateSectionYear-input]')) kind = 'date';
    else if (box.querySelector('textarea')) kind = 'textarea';
    out.push({id, kind, label, required, options});
  });
  return out;
}
"""


def tenant_of(url: str) -> str:
    return urlsplit(url).netloc.split(".")[0]


def strong_password() -> str:
    core = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    return f"Aa1!{core}"  # satisfies every Workday rule set seen (upper, lower, digit, special, length)


def _clean(label: str) -> str:
    return re.sub(r"\s*\*\s*$", "", label).strip()


def build_spec(raw: list[dict], *, company: str, title: str, url: str, description: str,
               dropdown_options: dict[str, list[str]]) -> FormSpec:
    fields, kinds = [], {}
    for r in raw:
        kind = r["kind"]
        label = _clean(r["label"])
        if not label:
            continue
        options: tuple[str, ...] = ()
        ftype = {"file": "file", "textarea": "textarea", "checkbox": "checkbox", "date": "date"}.get(kind, "text")
        if kind == "dropdown":
            options, ftype = real_options(dropdown_options.get(r["id"], ())), "select"
        elif kind in ("radio", "checkboxes"):
            options, ftype = tuple(r["options"]), ("radio" if kind == "radio" else "multiselect")
        kinds[r["id"]] = kind
        fields.append(FormField(id=r["id"], label=label, type=ftype, required=bool(r["required"]), options=options))
    return FormSpec(company=company, title=title, url=url, fields=fields, description=description,
                    meta={"kinds": kinds})


class WorkdayAdapter:
    ats = "workday"

    def __init__(self, keyring_mod=None, email_code: Callable[[str], str | None] | None = None):
        if keyring_mod is None:
            import keyring as keyring_mod
        self.keyring = keyring_mod
        self.email_code = email_code  # (tenant) -> verification link/code from Gmail, when set up

    # -- entry used by apply_one -------------------------------------------------------------------------------
    def apply_flow(self, page, *, url: str, key: str, company: str, title: str, email: str,
                   answer: Callable[[FormSpec], Resolution], live: bool, screenshot: Callable[[str], str]) -> dict:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_selector("[data-automation-id=adventureButton]", timeout=25_000)
        except Exception as e:
            raise AdapterError(f"no Apply button at {page.url} (closed posting?)") from e
        desc_el = page.query_selector("[data-automation-id=jobPostingDescription]")
        description = desc_el.inner_text() if desc_el else ""
        page.click("[data-automation-id=adventureButton]")
        page.wait_for_timeout(2500)
        if page.query_selector("[data-automation-id=applyManually]"):
            page.click("[data-automation-id=applyManually]")
        page.wait_for_selector("[data-automation-id=email], [data-automation-id=applyFlowPage]", timeout=25_000)
        if not live:
            return {"status": "dry_run", "reason": "workday: reached the account step (dry runs create no account)",
                    "description": description, "screenshot": screenshot("account")}
        if (problem := self._sign_in(page, tenant_of(url), email)) is not None:
            return {"status": "manual", "reason": problem, "description": description,
                    "screenshot": screenshot("account")}
        answers: dict = {}
        for _ in range(MAX_PAGES):
            settle(page)
            step = (page.inner_text("[data-automation-id=progressBarActiveStep]")
                    if page.query_selector("[data-automation-id=progressBarActiveStep]") else "")
            if re.search(r"review", step, re.I):
                shot = screenshot("review")
                result = self._submit(page)
                return {"status": result.status, "reason": result.detail, "answers": answers,
                        "description": description, "screenshot": shot}
            spec = self._read_page(page, company, title, description)
            res = answer(spec)
            answers.update({k: {"value": str(v.value), "source": v.source} for k, v in res.answers.items()})
            if not res.ok:
                return {"status": "skipped", "reason": f"{step}: {res.skip_reason}", "answers": answers,
                        "description": description, "screenshot": screenshot("skip")}
            problems = self._fill(page, spec, res)
            if problems:
                return {"status": "failed", "reason": f"{step}: fill: " + "; ".join(problems[:3]),
                        "answers": answers, "description": description, "screenshot": screenshot("fill")}
            page.click("[data-automation-id=bottom-navigation-next-button]")
            page.wait_for_timeout(2500)
            errors = page.eval_on_selector_all("[data-automation-id=errorMessage], [data-automation-id=inputAlert]",
                                               "es => es.map(e => e.innerText.trim()).filter(Boolean)")
            if errors:
                return {"status": "failed", "reason": f"{step}: page errors: {errors[:3]}", "answers": answers,
                        "description": description, "screenshot": screenshot("errors")}
        return {"status": "failed", "reason": "workday: too many pages", "answers": answers,
                "description": description, "screenshot": screenshot("pages")}

    # -- account -----------------------------------------------------------------------------------------------
    def _sign_in(self, page, tenant: str, email: str) -> str | None:
        """None when signed in; otherwise why a human is needed."""
        user = f"{tenant}:{email}"
        password = self.keyring.get_password(KEYRING_SERVICE, user)
        if password is None:
            password = strong_password()
            self.keyring.set_password(KEYRING_SERVICE, user, password)  # saved before the account exists
            page.fill("[data-automation-id=email]", email)
            page.fill("[data-automation-id=password]", password)
            if page.query_selector("[data-automation-id=verifyPassword]"):
                page.fill("[data-automation-id=verifyPassword]", password)
            if page.query_selector("[data-automation-id=createAccountCheckbox]"):
                page.check("[data-automation-id=createAccountCheckbox]")
            self._click_button(page, "createAccountSubmitButton")
        else:
            if page.query_selector("[data-automation-id=signInLink]"):
                page.click("[data-automation-id=signInLink]")
                page.wait_for_timeout(1500)
            page.fill("[data-automation-id=email]", email)
            page.fill("[data-automation-id=password]", password)
            self._click_button(page, "signInSubmitButton")
        for _ in range(30):
            page.wait_for_timeout(1000)
            if page.query_selector("[data-automation-id=applyFlowPage] [data-automation-id^=formField-]"):
                return None
            body = page.inner_text("body").lower()
            if re.search(r"verify your (email|account)|verification (email|link)|check your email", body):
                if self.email_code and (link := self.email_code(tenant)):
                    page.goto(link)
                    return self._sign_in(page, tenant, email)
                return "workday: account email verification needed (set up Gmail, or verify and re-run)"
            if "already" in body and "account" in body and "exists" in body:
                return "workday: an account already exists for this email on this tenant (password unknown)"
            if re.search(r"incorrect|invalid (email|password)|locked", body):
                return "workday: sign-in rejected"
        return "workday: did not reach the application form after sign-in"

    @staticmethod
    def _click_button(page, automation_id: str) -> None:
        # Workday overlays a transparent click_filter div on its submit buttons
        target = page.query_selector(f"[data-automation-id={automation_id}] ~ [data-automation-id=click_filter]") \
            or page.query_selector(f"[data-automation-id=click_filter][aria-label]") \
            or page.query_selector(f"[data-automation-id={automation_id}]")
        if target is None:
            raise AdapterError(f"workday: no {automation_id}")
        target.click()

    # -- pages -------------------------------------------------------------------------------------------------
    def _box(self, page, fid: str):
        aid, idx = fid.rsplit("#", 1)
        return page.locator('[data-automation-id^="formField-"]').nth(int(idx))

    def _read_page(self, page, company: str, title: str, description: str) -> FormSpec:
        raw = page.evaluate(EXTRACT_JS)
        options: dict[str, list[str]] = {}
        for r in raw:
            if r["kind"] == "dropdown":
                box = self._box(page, r["id"])
                box.locator("button[aria-haspopup=listbox]").click()
                page.wait_for_timeout(500)
                options[r["id"]] = [t.strip() for t in page.locator("[role=listbox] [role=option]").all_inner_texts()]
                page.keyboard.press("Escape")
        return build_spec(raw, company=company, title=title, url=page.url, description=description,
                          dropdown_options=options)

    def _fill(self, page, spec: FormSpec, res: Resolution) -> list[str]:
        problems = []
        kinds = spec.meta["kinds"]
        by_id = {f.id: f for f in spec.fields}
        for fid, ans in res.answers.items():
            box, kind, value = self._box(page, fid), kinds[fid], ans.value
            try:
                if kind == "file":
                    box.locator("input[type=file]").set_input_files(str(Path(value)))
                    page.wait_for_timeout(4000)
                elif kind == "dropdown":
                    box.locator("button[aria-haspopup=listbox]").click()
                    page.locator("[role=listbox] [role=option]", has_text=str(value)).first.click()
                elif kind == "prompt":
                    inp = box.locator("input").first
                    inp.fill(str(value))
                    inp.press("Enter")
                    page.wait_for_timeout(1200)
                    opt = page.locator("[data-automation-id=promptOption]").first
                    opt.click()
                elif kind == "radio":
                    box.get_by_label(str(value), exact=True).check()
                elif kind == "checkbox":
                    if value is True or value == ["Yes"] or value == "Yes":
                        box.locator("input[type=checkbox]").check()
                elif kind == "checkboxes":
                    for opt in value if isinstance(value, list) else [value]:
                        box.get_by_label(opt, exact=True).check()
                elif kind == "date":
                    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value)) or re.match(r"(\d{2})/(\d{2})/(\d{4})", str(value))
                    if not m:
                        raise AdapterError(f"unparseable date {value!r}")
                    y, mo = (m.group(1), m.group(2)) if len(m.group(1)) == 4 else (m.group(3), m.group(1))
                    if box.locator("[data-automation-id=dateSectionMonth-input]").count():
                        box.locator("[data-automation-id=dateSectionMonth-input]").fill(mo)
                    box.locator("[data-automation-id=dateSectionYear-input]").fill(y)
                elif kind == "textarea":
                    box.locator("textarea").fill(str(value))
                else:
                    box.locator("input").first.fill(str(value))
            except Exception as e:  # noqa: BLE001
                problems.append(f"{by_id[fid].label[:50]}: {type(e).__name__}: {str(e)[:100]}")
        return problems

    def _submit(self, page) -> SubmitResult:
        page.click("[data-automation-id=bottom-navigation-next-button]")
        for _ in range(40):
            page.wait_for_timeout(500)
            body = page.inner_text("body").lower()
            if re.search(r"application (was )?submitted|thank you for applying|thanks for applying|successfully submitted",
                         body):
                return SubmitResult("submitted", page.url)
        return SubmitResult("failed", "workday: no confirmation after submit")
