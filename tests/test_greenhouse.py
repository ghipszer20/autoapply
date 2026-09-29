from autoapply.adapters.base import best_option, query_for
from autoapply.adapters.greenhouse import build_spec, form_url


def test_form_url():
    assert form_url("https://job-boards.greenhouse.io/schonfeld/jobs/8171692?gh_src=x", "greenhouse:8171692") == \
        "https://job-boards.greenhouse.io/schonfeld/jobs/8171692"
    assert form_url("https://boards.greenhouse.io/figma/jobs/61?gh_jid=61", "greenhouse:61") == \
        "https://job-boards.greenhouse.io/figma/jobs/61"
    assert form_url("https://stripe.com/jobs/search?gh_jid=8128745", "greenhouse:8128745") == \
        "https://boards.greenhouse.io/embed/job_app?token=8128745"


def test_best_option_expands_states_and_prefers_shortest():
    opts = ["College Park, Georgia, United States", "College Park, Maryland, United States"]
    assert best_option(opts, "College Park, MD") == opts[1]
    schools = ["University of Maryland - Baltimore County", "University of Maryland - College Park",
               "University of Maryland - University College"]
    assert best_option(schools, "University of Maryland, College Park") == schools[1]
    assert best_option(schools, "Harvard") is None
    assert query_for("University of Maryland, College Park") == "University of Maryland"


RAW = [
    {"id": "first_name", "kind": "text", "label": "First Name*", "required": True, "maxLength": None, "options": []},
    {"id": "resume", "kind": "file", "label": "Resume/CV*", "required": False, "maxLength": None, "options": []},
    {"id": "school--0", "kind": "combobox", "label": "School*", "required": True, "maxLength": None, "options": []},
    {"id": "end-month--0", "kind": "combobox", "label": "End date month*", "required": True, "maxLength": None,
     "options": []},
    {"id": "question_1", "kind": "combobox", "label": "Authorized?*", "required": True, "maxLength": None,
     "options": []},
    {"id": "question_2[]", "kind": "checkboxes", "label": "Which tools?", "required": False,
     "options": ["Git", "SQL"], "optionIds": ["question_2[]_1", "question_2[]_2"]},
    {"id": "question_3", "kind": "textarea", "label": "Why us?", "required": False, "maxLength": 500, "options": []},
    {"id": "x", "kind": "text", "label": "", "required": False, "maxLength": None, "options": []},
]


def test_build_spec():
    spec = build_spec(RAW, company="A", title="T", url="u", description="d",
                      combobox_options={"question_1": ["Yes", "No"], "end-month--0": ["January", "May"]})
    by = {f.id: f for f in spec.fields}
    assert "x" not in by  # unlabeled helper inputs dropped
    assert by["first_name"].label == "First Name" and by["first_name"].required
    assert by["school--0"].type == "text" and spec.meta["kinds"]["school--0"] == "typeahead"
    assert by["end-month--0"].label == "Expected graduation month" and by["end-month--0"].options == ("January", "May")
    assert by["question_1"].type == "select" and by["question_1"].options == ("Yes", "No")
    assert by["question_2[]"].type == "multiselect" and spec.meta["option_ids"]["question_2[]"][1] == "question_2[]_2"
    assert by["question_3"].max_length == 500 and by["resume"].type == "file"
