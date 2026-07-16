"""Unit tests for optimize_and_fetch: pipelined fetch, union dedup, stats — optimizer and
LinkedIn fetcher fully mocked."""
import time

import optimize_and_fetch as oaf


def _archetype(label, count=10, relaxed=False, widened=False):
    return {"label": label, "final_count": count, "relaxed": relaxed, "widen_geo": widened,
            "final_skills": ["SkillA"],
            "format_filter_conditions": {"filters": {"locations": [{"name": "Tokyo, Japan"}]}}}


def _raw_result(linkedin_id):
    """Minimal recruiter-format search hit that extract_candidate_info can parse."""
    return {"memberProfile": f"urn:li:ts_profile:{linkedin_id}",
            "memberProfileResolutionResult": {"firstName": f"F{linkedin_id}", "lastName": "L",
                                              "educations": [], "location": "Tokyo, Japan"}}


def _install_fakes(monkeypatch, archetypes, results_by_label, call_log):
    """Fake single_process (fires on_archetype_ready per archetype) + fake fetch/extract."""

    def fake_single_process(initial_conditions, mandatory_skills, relaxation_options,
                            min_target, max_target, job_desc=None, on_archetype_ready=None, **kw):
        for a in archetypes:
            if on_archetype_ready:
                on_archetype_ready(a)          # PIPELINING HOOK
                call_log.append(("ready", a["label"], time.time()))
        return {"archetypes": archetypes, "final_skills": ["SkillA"], "final_count": 42,
                "final_conditions": {}, "format_filter_conditions": {}}

    def fake_batch_search(est_count, conditions, skills, max_search_num=200, channel="recruiter"):
        # figure out which archetype this is via captured conditions identity
        call_log.append(("fetch", est_count, time.time()))
        return results_by_label.pop(0)

    def fake_extract(job_info, channel="recruiter", include_education_summary=False):
        lid = job_info["memberProfile"].split(":")[-1]
        mp = job_info["memberProfileResolutionResult"]
        return (lid, mp["firstName"], mp["lastName"], "", mp["location"], "", "", "", 5.0, "", None)

    monkeypatch.setattr(oaf, "single_process", fake_single_process)
    monkeypatch.setattr(oaf, "batch_basic_linkedin_search", fake_batch_search)
    monkeypatch.setattr(oaf, "extract_candidate_info", fake_extract)
    monkeypatch.setattr(oaf, "clear_optimization_caches", lambda: None)


def test_union_dedups_across_archetypes(monkeypatch):
    archetypes = [_archetype("a1"), _archetype("a2")]
    # candidate c2 appears in BOTH archetypes' results -> must appear once in the union
    results = [[_raw_result("c1"), _raw_result("c2")], [_raw_result("c2"), _raw_result("c3")]]
    log = []
    _install_fakes(monkeypatch, archetypes, results, log)

    union_df, stats = oaf.optimize_and_fetch_union(job_desc="jd text", clear_caches=False)
    assert sorted(union_df["linkedin_id"]) == ["c1", "c2", "c3"]
    assert list(union_df.columns) == oaf.CANDIDATE_COLUMNS
    assert stats["total_fetched"] == 4
    assert stats["union_unique"] == 3
    assert stats["n_archetypes"] == 2
    assert [p["archetype"] for p in stats["per_archetype"]] == ["a1", "a2"]


def test_pipelining_fetches_submitted_via_callback(monkeypatch):
    archetypes = [_archetype("a1"), _archetype("a2"), _archetype("a3")]
    results = [[_raw_result(f"c{i}")] for i in range(3)]
    log = []
    _install_fakes(monkeypatch, archetypes, results, log)

    oaf.optimize_and_fetch_union(job_desc="jd", clear_caches=False)
    # every archetype was delivered through on_archetype_ready (not the post-hoc safety net):
    assert [e[1] for e in log if e[0] == "ready"] == ["a1", "a2", "a3"]
    # and every fetch was actually executed
    assert sum(1 for e in log if e[0] == "fetch") == 3


def test_safety_net_when_callback_not_fired(monkeypatch):
    """A single_process WITHOUT the hook (older version) must still get fetched post-hoc."""
    archetypes = [_archetype("a1")]
    results = [[_raw_result("c1")]]
    log = []
    _install_fakes(monkeypatch, archetypes, results, log)

    def old_single_process(initial_conditions, mandatory_skills, relaxation_options,
                           min_target, max_target, job_desc=None, **kw):
        return {"archetypes": archetypes, "final_skills": [], "final_count": 1,
                "final_conditions": {}}         # never calls on_archetype_ready

    import optimize_and_fetch as oaf2
    oaf2.__dict__["single_process"] = old_single_process
    union_df, stats = oaf.optimize_and_fetch_union(job_desc="jd", clear_caches=False)
    assert stats["union_unique"] == 1
    assert list(union_df["linkedin_id"]) == ["c1"]


def test_archetypes_without_filters_are_skipped(monkeypatch):
    good = _archetype("good")
    empty = {"label": "empty", "final_count": 5, "format_filter_conditions": {"filters": {}}}
    results = [[_raw_result("c1")]]
    log = []
    _install_fakes(monkeypatch, [good, empty], results, log)

    union_df, stats = oaf.optimize_and_fetch_union(job_desc="jd", clear_caches=False)
    assert stats["n_archetypes"] == 1            # only the condition WITH filters counted
    assert sum(1 for e in log if e[0] == "fetch") == 1


def test_return_optimization_flag(monkeypatch):
    archetypes = [_archetype("a1")]
    results = [[_raw_result("c1")]]
    _install_fakes(monkeypatch, archetypes, results, [])

    out = oaf.optimize_and_fetch_union(job_desc="jd", clear_caches=False, return_optimization=True)
    assert len(out) == 3
    union_df, stats, opt = out
    assert opt["final_count"] == 42
    assert opt["archetypes"][0]["label"] == "a1"


def test_empty_run_returns_empty_df_with_columns(monkeypatch):
    _install_fakes(monkeypatch, [], [], [])
    union_df, stats = oaf.optimize_and_fetch_union(job_desc="jd", clear_caches=False)
    assert len(union_df) == 0
    assert list(union_df.columns) == oaf.CANDIDATE_COLUMNS
    assert stats["union_unique"] == 0
