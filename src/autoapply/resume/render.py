"""Render a Resume (or any HTML) to PDF with Playwright's headless Chromium. Resumes must be one page."""

from __future__ import annotations

from html import escape
from pathlib import Path

from pypdf import PdfReader

from .model import Entry, Resume


class RenderError(Exception):
    pass


CSS = """
@page { size: Letter; margin: 0.45in 0.55in; }
body { font-family: 'Times New Roman', Times, serif; font-size: 10.5pt; line-height: 1.22; color: #000; margin: 0; }
h1 { font-size: 20pt; text-align: center; margin: 0 0 2pt; font-weight: bold; }
.contact { text-align: center; font-size: 10pt; margin-bottom: 6pt; }
h2 { font-size: 11pt; text-transform: uppercase; border-bottom: 0.8pt solid #000; margin: 8pt 0 3pt; padding-bottom: 1pt; }
.row { display: flex; justify-content: space-between; }
.org { font-weight: bold; }
.sub { font-style: italic; }
ul { margin: 1pt 0 4pt; padding-left: 16pt; }
li { margin: 0 0 1pt; }
.entry { margin-bottom: 3pt; }
"""


def _entry(e: Entry) -> str:
    out = [f'<div class="entry"><div class="row"><span class="org">{escape(e.org)}</span>'
           f'<span>{escape(e.location if e.title else e.dates)}</span></div>']
    if e.title:
        out.append(f'<div class="row"><span class="sub">{escape(e.title)}</span><span class="sub">{escape(e.dates)}</span></div>')
    if e.bullets:
        out.append("<ul>" + "".join(f"<li>{escape(b.text)}</li>" for b in e.bullets) + "</ul>")
    out.append("</div>")
    return "".join(out)


def render_html(r: Resume) -> str:
    ed = r.education
    edu_bullets = []
    if ed.gpa:
        edu_bullets.append(f"GPA: {escape(ed.gpa)}")
    if ed.coursework:
        edu_bullets.append("<b>Relevant Coursework:</b> " + escape(", ".join(ed.coursework)))
    if ed.skills:
        edu_bullets.append("<b>Technical Skills:</b> " + escape(", ".join(ed.skills)))
    parts = [
        f"<h1>{escape(r.name)}</h1>",
        '<div class="contact">' + " &#9830; ".join(escape(c) for c in r.contact) + "</div>",
        "<h2>Education</h2>",
        f'<div class="entry"><div class="row"><span class="org">{escape(ed.org)}</span><span>{escape(ed.location)}</span></div>'
        f'<div class="row"><span class="sub">{escape(ed.title)}</span><span class="sub">{escape(ed.dates)}</span></div>'
        "<ul>" + "".join(f"<li>{b}</li>" for b in edu_bullets) + "</ul></div>",
    ]
    for s in r.sections:
        if s.entries:
            parts.append(f"<h2>{escape(s.heading)}</h2>" + "".join(_entry(e) for e in s.entries))
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"


def html_to_pdf(html: str, out: Path) -> int:
    """Write the PDF and return its page count."""
    from playwright.sync_api import sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(out), format="Letter", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()
    return len(PdfReader(out).pages)


def render_pdf(r: Resume, out: Path) -> Path:
    pages = html_to_pdf(render_html(r), out)
    if pages != 1:
        out.unlink(missing_ok=True)
        raise RenderError(f"resume renders to {pages} pages (must be 1)")
    return out
