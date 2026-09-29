"""Ashby (jobs.ashbyhq.com): form schema from the public GraphQL API, filled through the DOM by field path."""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

import httpx

from ..answers import Resolved
from ..forms import FormField, FormSpec
from .base import AdapterError, SubmitResult, best_option, query_for, real_options, settle

GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title descriptionHtml
    applicationForm { sections { fieldEntries { ... on FormFieldEntry { field isRequired isHidden } } } }
    surveyForms { sections { fieldEntries { ... on FormFieldEntry { field isRequired isHidden } } } }
  } }"""
_URL = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})")

TYPES = {"String": "text", "Email": "text", "Phone": "text", "LongText": "textarea", "Number": "number",
         "Date": "date", "File": "file", "Boolean": "radio", "ValueSelect": "radio", "MultiValueSelect": "multiselect",
         "Location": "text", "SocialLink": "text", "Url": "text"}


def parse_url(url: str) -> tuple[str, str]:
    m = _URL.search(url)
    if not m:
        raise AdapterError(f"not an Ashby job URL: {url}")
    return m.group(1), m.group(2)


def _strip_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def build_spec(job: dict, *, company: str, title: str, url: str) -> FormSpec:
    """Pure: GraphQL jobPosting -> FormSpec. meta['kinds'] keeps the Ashby field type per path."""
    fields: list[FormField] = []
    kinds: dict[str, str] = {}
    forms = [job.get("applicationForm")] + list(job.get("surveyForms") or [])
    for form in forms:
        for section in (form or {}).get("sections", []):
            for entry in section.get("fieldEntries", []):
                f = entry.get("field") or {}
                if entry.get("isHidden") or not f.get("path") or f.get("type") not in TYPES:
                    continue
                kind = f["type"]
                options: tuple[str, ...] = ()
                if kind == "Boolean":
                    options = ("Yes", "No")
                elif kind in ("ValueSelect", "MultiValueSelect"):
                    options = tuple(v["label"].strip() for v in f.get("selectableValues") or [])
                label = _strip_html(f.get("title")).replace("\xa0", " ").strip()
                if not label:
                    continue
                kinds[f["path"]] = kind
                fields.append(FormField(id=f["path"], label=label, type=TYPES[kind],
                                        required=bool(entry.get("isRequired")), options=options))
    return FormSpec(company=company, title=title, url=url, fields=fields,
                    description=_strip_html(job.get("descriptionHtml")), meta={"kinds": kinds})


def fetch_job(client: httpx.Client, org: str, job_id: str) -> dict:
    resp = client.post(GRAPHQL, json={"operationName": "ApiJobPosting", "query": QUERY, "variables": {
        "organizationHostedJobsPageName": org, "jobPostingId": job_id}})
    resp.raise_for_status()
    data = resp.json()
    job = (data.get("data") or {}).get("jobPosting")
    if not job:
        raise AdapterError(f"ashby posting {org}/{job_id} not found (closed?) {data.get('errors', '')}")
    return job


class AshbyAdapter:
    ats = "ashby"

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30)

    def form_url(self, url: str, key: str) -> str:
        org, job_id = parse_url(url)
        return f"https://jobs.ashbyhq.com/{org}/{job_id}/application"

    def load(self, page, url: str, key: str, company: str, title: str) -> FormSpec:
        org, job_id = parse_url(url)
        try:
            job = fetch_job(self.client, org, job_id)
        except httpx.HTTPError as e:
            raise AdapterError(f"ashby api: {e}") from e
        target = self.form_url(url, key)
        page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_selector("[data-field-path]", timeout=25_000)
        except Exception as e:
            raise AdapterError(f"no application form at {page.url}") from e
        settle(page)
        return build_spec(job, company=company, title=title, url=target)

    def fill(self, page, spec: FormSpec, answers: dict[str, Resolved]) -> list[str]:
        problems: list[str] = []
        kinds = spec.meta["kinds"]
        by_id = {f.id: f for f in spec.fields}
        for fid, ans in answers.items():
            f, kind, value = by_id[fid], kinds[fid], ans.value
            box = page.locator(f'[data-field-path="{fid}"]').first
            try:
                if kind == "File":
                    box.locator("input[type=file]").set_input_files(str(Path(value)))
                elif kind == "Boolean":
                    box.locator(f'button[data-option="{"yes" if str(value).lower().startswith("y") else "no"}"]').click()
                elif kind in ("ValueSelect", "MultiValueSelect"):
                    for opt in value if isinstance(value, list) else [value]:
                        self._choose_option(page, box, opt)
                elif kind == "Location":
                    self._typeahead(page, box, str(value))
                elif kind == "Date":
                    inp = box.locator("input").first
                    inp.fill(self._date_text(str(value)))
                    inp.press("Enter")
                elif kind == "LongText":
                    box.locator("textarea").first.fill(str(value))
                else:
                    box.locator("input").first.fill(str(value))
            except Exception as e:  # noqa: BLE001 - reported per field
                problems.append(f"{f.label[:60]}: {type(e).__name__}: {str(e)[:120]}")
        return problems

    @staticmethod
    def _date_text(value: str) -> str:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", value)
        return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else value

    @staticmethod
    def _choose_option(page, box, opt: str) -> None:
        labels = box.locator("label")
        texts = [t.strip() for t in labels.all_inner_texts()]
        if opt in texts[1:]:  # radio / checkbox group: the first label is the question title
            labels.nth(texts.index(opt, 1)).click()
            return
        combo = box.locator("input[role=combobox], input").first  # long option lists render as a dropdown
        combo.click()
        combo.fill(opt)
        page.wait_for_selector("[role=option]", timeout=8_000)
        opts = [t.strip() for t in page.locator("[role=option]").all_inner_texts()]
        if opt not in opts:
            raise AdapterError(f"option {opt!r} not offered")
        page.locator("[role=option]").nth(opts.index(opt)).click()

    @staticmethod
    def _typeahead(page, box, value: str) -> None:
        inp = box.locator("input").first
        inp.click()
        inp.fill(query_for(value))
        page.wait_for_selector("[role=option]", timeout=10_000)
        page.wait_for_timeout(1000)
        opts = [t.strip() for t in page.locator("[role=option]").all_inner_texts()]
        pick = best_option(opts, value)
        if pick is None:
            raise AdapterError(f"no location option for {value!r} among {opts[:5]}")
        page.locator("[role=option]").nth(opts.index(pick)).click()

    def submit(self, page) -> SubmitResult:
        page.get_by_role("button", name=re.compile(r"submit application", re.I)).click()
        for _ in range(40):
            page.wait_for_timeout(500)
            body = page.inner_text("body").lower()
            if re.search(r"thank you for applying|thanks for applying|application (was|has been) (successfully )?submitted|"
                         r"successfully submitted", body):
                return SubmitResult("submitted", page.url)
            if re.search(r"verification code|security code|confirm your email", body):
                return SubmitResult("manual", "email verification required")
            if "possible spam" in body or "flagged" in body:
                return SubmitResult("manual", "flagged as spam by Ashby")
        errors = page.eval_on_selector_all('[class*="error"], [role=alert]',
                                           "es => es.map(e => e.innerText.trim()).filter(Boolean)")
        return SubmitResult("failed", f"no confirmation after submit; errors: {errors[:5]}")
