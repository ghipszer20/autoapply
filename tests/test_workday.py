from autoapply.adapters.workday import build_spec, strong_password, tenant_of


def test_tenant_and_password():
    assert tenant_of("https://fox.wd1.myworkdayjobs.com/en-US/domestic/job/x_R1") == "fox"
    pw = strong_password()
    assert len(pw) >= 16 and any(c.isupper() for c in pw) and any(c.islower() for c in pw) \
        and any(c.isdigit() for c in pw) and "!" in pw


def test_build_spec():
    raw = [
        {"id": "formField-legalName--firstName#0", "kind": "text", "label": "First Name*", "required": True, "options": []},
        {"id": "formField-source#1", "kind": "dropdown", "label": "How Did You Hear About Us?*", "required": True,
         "options": []},
        {"id": "formField-candidateIsPreviousWorker#2", "kind": "radio", "label": "Have you worked here before?*",
         "required": True, "options": ["Yes", "No"]},
        {"id": "formField-x#3", "kind": "text", "label": "", "required": False, "options": []},
    ]
    spec = build_spec(raw, company="A", title="T", url="u", description="d",
                      dropdown_options={"formField-source#1": ["Select One", "Job Board", "Other"]})
    by = {f.id: f for f in spec.fields}
    assert set(by) == {"formField-legalName--firstName#0", "formField-source#1", "formField-candidateIsPreviousWorker#2"}
    assert by["formField-legalName--firstName#0"].label == "First Name"
    assert by["formField-source#1"].options == ("Select One", "Job Board", "Other") or \
        by["formField-source#1"].options == ("Job Board", "Other")
    assert by["formField-candidateIsPreviousWorker#2"].type == "radio"


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, s, u):
        return self.store.get((s, u))

    def set_password(self, s, u, p):
        self.store[(s, u)] = p


def test_adapter_constructs_with_fake_keyring():
    from autoapply.adapters.workday import WorkdayAdapter
    assert WorkdayAdapter(keyring_mod=FakeKeyring()).ats == "workday"
