"""Unit tests for utils.search_utils.extract_candidate_info on synthetic recruiter payloads."""
from utils.search_utils import extract_candidate_info

# extract_linkedin_urn (recruiter channel) accepts ONLY 39-char profile ids — LinkedIn's
# real member-URN length. Shorter ids are rejected and the whole candidate returns None.
VALID_ID = "ACwAAEEnVB4BkB8zGhLQHdAkb8C144X4aTAkebg"   # 39 chars
assert len(VALID_ID) == 39


def _payload(**over):
    base = {
        "memberProfile": f"urn:li:ts_profile:{VALID_ID}",
        "memberProfileResolutionResult": {
            "firstName": "Taro",
            "lastName": "Yamada",
            "location": {"displayName": "Tokyo, Japan"},   # real schema: dict, not str
            "memberPreferences": {"openToNewOpportunities": True},
            "educations": [{"degreeName": "Bachelor of Engineering"}],
            "summary": "Sourcing specialist with 8 years of experience.",
        },
    }
    base["memberProfileResolutionResult"].update(over)
    return base


def test_extracts_core_fields():
    info = extract_candidate_info(_payload(), channel="recruiter", include_education_summary=True)
    assert info is not None
    linkedin_id, first, last, degree, location = info[0], info[1], info[2], info[3], info[4]
    assert linkedin_id == VALID_ID
    assert (first, last) == ("Taro", "Yamada")
    assert "Bachelor of Engineering" in degree
    assert location == "Tokyo, Japan"
    # include_education_summary=True -> 11-tuple with open_to_opportunities last
    assert len(info) == 11
    assert info[10] is True


def test_missing_member_profile_returns_none():
    assert extract_candidate_info({"memberProfileResolutionResult": {}},
                                  channel="recruiter") is None


def test_short_profile_id_falls_back_to_unknown():
    # ids that are not 39 chars fail recruiter URN validation; the candidate is still
    # returned but with linkedin_id "Unknown" (dropped later by the dedup/consumers).
    info = extract_candidate_info(_payload() | {"memberProfile": "urn:li:ts_profile:short"},
                                  channel="recruiter", include_education_summary=True)
    assert info is not None and info[0] == "Unknown"


def test_malformed_profile_does_not_raise():
    bad = {"memberProfile": f"urn:li:ts_profile:{VALID_ID}",
           "memberProfileResolutionResult": {"firstName": None, "educations": "not-a-list"}}
    info = extract_candidate_info(bad, channel="recruiter", include_education_summary=True)
    # must not raise; either a parsed tuple or the error-profile fallback
    assert info is None or isinstance(info, tuple)
