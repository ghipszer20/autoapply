import pytest

from autoapply.adapters.ashby import build_spec, parse_url
from autoapply.adapters.base import AdapterError

JOB = {
    "title": "Data Intern", "descriptionHtml": "<p>Work with <b>data</b>&nbsp;daily.</p>",
    "applicationForm": {"sections": [{"fieldEntries": [
        {"isRequired": True, "field": {"path": "_systemfield_name", "type": "String", "title": "Name"}},
        {"isRequired": True, "field": {"path": "_systemfield_resume", "type": "File", "title": "Resume"}},
        {"isRequired": True, "field": {"path": "b1", "type": "Boolean", "title": "Are you 18?"}},
        {"isRequired": False, "field": {"path": "v1", "type": "ValueSelect", "title": "Source&nbsp;",
                                        "selectableValues": [{"label": "Agency ", "value": "a"}, {"label": "Job Board", "value": "j"}]}},
        {"isRequired": False, "isHidden": True, "field": {"path": "h", "type": "String", "title": "Hidden"}},
        {"isRequired": False, "field": {"path": "w", "type": "Weird", "title": "Unknown type"}},
    ]}]},
    "surveyForms": [{"sections": [{"fieldEntries": [
        {"isRequired": False, "field": {"path": "g", "type": "ValueSelect", "title": "Gender",
                                        "selectableValues": [{"label": "Male", "value": "m"}]}}]}]}],
}


def test_parse_url():
    assert parse_url("https://jobs.ashbyhq.com/ramp/b66be397-240b-41a6-9b05-493299b270a9/application") == \
        ("ramp", "b66be397-240b-41a6-9b05-493299b270a9")
    with pytest.raises(AdapterError):
        parse_url("https://example.com/x")


def test_build_spec():
    spec = build_spec(JOB, company="A", title="T", url="u")
    by = {f.id: f for f in spec.fields}
    assert set(by) == {"_systemfield_name", "_systemfield_resume", "b1", "v1", "g"}
    assert by["b1"].type == "radio" and by["b1"].options == ("Yes", "No") and by["b1"].required
    assert by["v1"].label == "Source" and by["v1"].options == ("Agency", "Job Board")
    assert by["_systemfield_resume"].type == "file"
    assert spec.meta["kinds"]["g"] == "ValueSelect"
    assert spec.description == "Work with data daily."
