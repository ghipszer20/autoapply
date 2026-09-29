import pytest

from autoapply.forms import FormField, pick, pick_bool
from autoapply.profile import Profile, ProfileError

YESNO = ["Yes", "No"]


def test_pick_bool_plain():
    assert pick_bool(YESNO, True) == "Yes"
    assert pick_bool(YESNO, False) == "No"


def test_pick_bool_sentences():
    opts = ["Yes, I will require sponsorship", "No, I will not require sponsorship"]
    assert pick_bool(opts, False) == opts[1]
    opts = ["I am authorized to work in the US", "I am not authorized to work in the US"]
    assert pick_bool(opts, True) == opts[0]
    assert pick_bool(opts, False) == opts[1]


def test_pick_bool_no_match():
    assert pick_bool(["Maybe", "Later"], True) is None


def test_pick_bool_free_text_field():
    assert pick_bool([], True) == "Yes"


def test_pick_word_boundary():
    assert pick(["Female", "Male", "Decline to self-identify"], ["male"]) == "Male"


def test_pick_candidates_in_order():
    opts = ["I am a protected veteran", "I am not a protected veteran", "I don't wish to answer"]
    assert pick(opts, ["i am not a protected veteran", "not a protected veteran"]) == opts[1]
    opts2 = ["Yes, I identify as a veteran", "No, I am not a veteran", "Prefer not to say"]
    assert pick(opts2, ["not a protected veteran", "not a veteran"]) == opts2[1]


def test_pick_contains_phrase():
    opts = ["White (Not Hispanic or Latino)", "Black or African American (Not Hispanic or Latino)", "Asian"]
    assert pick(opts, ["white"]) == opts[0]


def test_pick_none():
    assert pick(["a", "b"], ["zzz"]) is None


def test_form_field_defaults():
    f = FormField(id="q1", label="Why us?", type="textarea")
    assert f.required is False and f.options == ()


def test_profile_get_and_validate(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text(
        "﻿identity: {first_name: A, last_name: B, email: a@b.c, phone: '1'}\n"
        "education: {school: UMD, expected_graduation: May 2028}\n"
        "work_authorization: {authorized_to_work_in_us: true, requires_sponsorship_now: false,"
        " requires_sponsorship_future: false}\n", encoding="utf-8")
    prof = Profile.load(p)
    assert prof.get("identity.first_name") == "A"
    assert prof.get("identity.nope", "d") == "d"
    assert prof.get("work_authorization.requires_sponsorship_now") is False


def test_profile_missing_required(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("identity: {first_name: A}\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="identity.last_name"):
        Profile.load(p)
