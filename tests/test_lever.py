from autoapply.adapters.lever import build_spec, parse_url

RAW = [
    {"name": "name", "kind": "text", "label": "Full name✱", "required": True, "options": [], "values": []},
    {"name": "resume", "kind": "file", "label": "Resume/CV ✱", "required": False, "options": [], "values": []},
    {"name": "urls[Twitter]", "kind": "text", "label": "Twitter URL", "required": False, "options": [], "values": []},
    {"name": "cards[x][field4]", "kind": "select", "label": "How did you hear about us?✱", "required": True,
     "options": ["Select...", "Website", "Other"], "values": []},
    {"name": "cards[x][field5]", "kind": "radio", "label": "Are you 18?✱", "required": True,
     "options": ["Yes", "No"], "values": ["Yes", "No"]},
    {"name": "eeo[gender]", "kind": "select", "label": "", "required": False,
     "options": ["Select ...", "Male", "Female"], "values": []},
]


def test_parse_url():
    assert parse_url("https://jobs.lever.co/belvederetrading/10746b3d-1760-4573-9b63-b93f5a5e4fc0/apply") == \
        ("belvederetrading", "10746b3d-1760-4573-9b63-b93f5a5e4fc0")


def test_build_spec():
    spec = build_spec(RAW, company="A", title="T", url="u", description="d")
    ids = [f.id for f in spec.fields]
    assert ids[0] == "resume" and "urls[Twitter]" not in ids
    by = {f.id: f for f in spec.fields}
    assert by["cards[x][field4]"].options == ("Website", "Other") and by["cards[x][field4]"].label == "How did you hear about us?"
    assert by["eeo[gender]"].label == "Gender" and by["eeo[gender]"].options == ("Male", "Female")
    assert spec.meta["values"]["cards[x][field5]"] == {"Yes": "Yes", "No": "No"}
