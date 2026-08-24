"""
Hermetic regression over the planning package, the matrix and the ranking (ROADMAP M10-M11).

    python -m fdr.test_planning

What these pin down is not that the matrix renders — it is that the matrix cannot become
misleading in the four ways that matter:

  1. a raised cluster reaching the matrix with an empty field, which reads as "nothing to do";
  2. review-mode output losing its label and being read as an assessment;
  3. an unassessable priority dimension being scored zero, which silently penalises a
     cluster for the system's own missing data;
  4. a rank appearing without the reasoning §11.2 and §16 require.
"""
from __future__ import annotations
import json

from . import clusters as CL
from . import planning as PL
from . import priority as PR
from . import safe_language
from .assemble import build_report
from .model import RAISED, FIRED, BusinessProfile
from .render import render

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


def _review(assume: str = "S02,S05,S21", **kw):
    return build_report(
        "Review Entity", ("FY2022-23", "FY2023-24", "FY2024-25"),
        comparable_years=3,
        assume_fired=frozenset(assume.split(",")),
        **kw,
    )


# ---- planning content -------------------------------------------------------------

def test_every_cluster_has_planning_content() -> None:
    """A cluster that can be raised but not planned is useless."""
    check(set(PL.BY_ID) == set(CL.BY_ID),
          f"planning content missing for {sorted(set(CL.BY_ID) - set(PL.BY_ID))}")


def test_raised_cluster_carries_the_full_10_3_package() -> None:
    rep = _review()
    raised = rep.raised()
    check(bool(raised), "review mode raised no cluster")
    for c in raised:
        for field, val in (
            ("inherent_risk", c.inherent_risk),
            ("planning_significance", c.planning_significance),
            ("control_implications", c.control_implications),
            ("materiality_basis", c.materiality_basis),
            ("evidence_request", c.evidence_request),
            ("affected_assertions", c.affected_assertions),
            ("priority_reasoning", c.priority_reasoning),
        ):
            check(bool(val), f"{c.cluster_id}: §10.3 field {field} is empty on a raised cluster")
        for k in ("nature", "timing", "extent"):
            check(bool(c.recommended_response.get(k)),
                  f"{c.cluster_id}: response.{k} is empty")
        check(c.significant_risk is not None,
              f"{c.cluster_id}: significant-risk determination not made")


def test_alternative_explanations_reach_the_matrix() -> None:
    """§2.2 — the non-error explanations must travel with the lead, not sit in a data file."""
    rep = _review()
    for c in rep.raised():
        check(bool(c.alt_explanations),
              f"{c.cluster_id}: raised with no alternative explanations (§2.2)")
    text = render(rep)
    check("alternative explanations to eliminate first" in text,
          "alternative explanations do not render on the matrix row")


def test_evidence_carries_the_management_explanation_caveat() -> None:
    """§2.3 — management explanation is a lead to test, never audit evidence in itself."""
    text = render(_review())
    check("never audit evidence in itself" in text,
          "the matrix does not carry the management-explanation caveat")


def test_every_authored_string_passes_the_lint() -> None:
    """Lint the content directly, not whichever clusters a test happens to raise.

    Found the hard way: the safe-language tests exercised three assume-sets, none of which
    raised the capitalisation cluster, so its authored response prose reached the CLI
    unlinted and the release gate fired on a real run. Cluster coverage by sampling is not
    coverage; this walks every authored field of every cluster.
    """
    for pc in PL.CONTENT:
        fields = {
            "inherent_risk_basis": pc.inherent_risk_basis,
            "significant_risk_basis": pc.significant_risk_basis,
            "planning_significance": pc.planning_significance,
            "response.nature": pc.response.nature,
            "response.timing": pc.response.timing,
            "response.extent": pc.response.extent,
            "by_nature_basis": pc.by_nature_basis,
            "control_implications": pc.control_implications,
        }
        for i, e in enumerate(pc.extra_evidence):
            fields[f"extra_evidence[{i}]"] = e
        for name, text in fields.items():
            bad = safe_language.lint(text)
            check(not bad, f"{pc.cluster_id}.{name}: {bad[0] if bad else ''}")

    for c in CL.CLUSTERS:
        for i, e in enumerate(c.evidence):
            check(not safe_language.lint(e), f"{c.id}.evidence[{i}] fails the lint")
        check(not safe_language.lint(c.control_implications),
              f"{c.id}.control_implications fails the lint")

    from . import signals as SG
    for s in SG.SIGNALS:
        for i, e in enumerate(s.alt_explanations):
            check(not safe_language.lint(e), f"{s.id}.alt_explanations[{i}] fails the lint")


def test_every_cluster_renders_through_the_release_gate() -> None:
    """Raise each cluster in turn and push it through assert_safe, as the CLI does."""
    from . import signals as SG
    for c in CL.CLUSTERS:
        face = [s for s in c.signals if SG.BY_ID[s].availability == SG.FACE]
        rep = build_report(
            "Gate", ("FY2024-25",), comparable_years=1,
            assume_fired=frozenset(face),
            business_profile=BusinessProfile(model="manufacturing", model_confidence="LOW"),
        )
        try:
            safe_language.assert_safe(render(rep))
        except ValueError as e:
            _fails.append(f"{c.id} does not survive the release gate: {e}")


def test_proposed_content_is_declared() -> None:
    items = PL.proposed_items()
    check(bool(items), "no PROPOSED items declared — provenance tracking has been lost")
    check(any("RC-WC" in i for i in items),
          "the working-capital Appendix F gap is no longer declared")


# ---- review mode ------------------------------------------------------------------

def test_review_mode_is_unmistakable() -> None:
    text = render(_review())
    check("REVIEW MODE — NOT AN ASSESSMENT OF THIS ENTITY" in text,
          "review-mode banner missing from the render")
    check("Nothing was measured" in text, "review-mode banner does not say nothing was measured")
    rep = _review()
    check(rep.coverage.review_mode is True, "review_mode flag not set")
    check(rep.coverage.assumed_signals == ("S02", "S05", "S21"),
          "assumed signals not recorded on the coverage block")
    check("REVIEW-MODE" in rep.versions["pipeline"],
          "the payload's pipeline version does not disclose review mode")


def test_assumed_signals_carry_no_confidence() -> None:
    """An asserted signal was not measured, so nothing is known about it."""
    rep = _review()
    for c in rep.clusters:
        for s in c.contributing_signals:
            if s.status == FIRED:
                check(s.confidence is None,
                      f"{s.signal_id}: an assumed signal was given a confidence")
                check("ASSUMED" in s.observation,
                      f"{s.signal_id}: assumed result does not label itself")
        if c.status == RAISED:
            check(c.diagnostic_confidence is None,
                  f"{c.cluster_id}: a cluster built on assumed signals carries a confidence")


def test_normal_mode_is_unaffected() -> None:
    rep = build_report("Plain", ("FY2024-25",), comparable_years=1)
    check(rep.coverage.review_mode is False, "review mode leaked into a normal run")
    check(not rep.raised(), "a cluster was raised without assumptions or data")
    check("REVIEW MODE" not in render(rep), "review banner appears on a normal run")


# ---- prioritisation ---------------------------------------------------------------

def test_unassessable_dimensions_are_not_scored_zero() -> None:
    """P4 in the ranking: absent data must not silently penalise a cluster."""
    rep = _review()
    for p in rep.priorities:
        for d in p.dimensions:
            check(d.score is None or d.score > 0 or "0" in d.basis,
                  f"{p.cluster_id}/{d.name}: scored 0 with no stated basis")
            check(bool(d.basis.strip()), f"{p.cluster_id}/{d.name}: no basis given")
        check(p.assessed_count < len(PR.DIMENSIONS),
              f"{p.cluster_id}: all nine dimensions assessed with no panel — implausible")


def test_by_nature_override_orders_above_score() -> None:
    """§11.2 — elevated even when quantitatively small; an override, not a bonus."""
    rep = _review("S02,S05,S21")
    ranks = {p.cluster_id: p.rank for p in rep.priorities}
    by_nature = [p for p in rep.priorities if p.by_nature]
    plain = [p for p in rep.priorities if not p.by_nature]
    check(bool(by_nature) and bool(plain), "test needs both kinds of cluster raised")
    check(max(p.rank for p in by_nature) < min(p.rank for p in plain),
          f"a by-nature cluster ranked below a plain one: {ranks}")


def test_every_rank_explains_itself_and_the_one_below() -> None:
    """§11.2 / §16 — a ranked item without a reasoning trail is not acceptable output."""
    rep = _review()
    for i, p in enumerate(rep.priorities):
        check(len(p.reasoning) > 120, f"{p.cluster_id}: reasoning is too thin to review")
        if i + 1 < len(rep.priorities):
            below = rep.priorities[i + 1].cluster_id
            check(below in p.reasoning,
                  f"{p.cluster_id}: does not say why {below} ranks lower")


def test_combination_method_is_disclosed() -> None:
    text = render(_review())
    check("Combination method" in text, "the ranking method is not disclosed in the output")
    check("excluded rather than scored zero" in text,
          "the output does not disclose how unassessed dimensions are handled")


def test_shortlist_size_is_respected() -> None:
    rep = _review("S02,S05,S13,S21", shortlist_size=2)
    check(len(rep.priorities) == 2, f"shortlist of 2 produced {len(rep.priorities)}")
    check(len(rep.shortlist) == 2, "shortlist ids do not match the truncated ranking")


def test_only_raised_clusters_are_ranked() -> None:
    rep = _review("S02")
    ranked = {p.cluster_id for p in rep.priorities}
    raised = {c.cluster_id for c in rep.raised()}
    check(ranked == raised, f"ranked {ranked} but raised {raised}")


# ---- output contracts --------------------------------------------------------------

def test_reproducible() -> None:
    """§16 / §18.3 — the same inputs and thresholds produce the same output."""
    a = _review().to_dict()
    b = _review().to_dict()
    check(a == b, "two identical runs produced different payloads")


def test_payload_carries_the_ranking() -> None:
    d = _review().to_dict()
    check("priorities" in d, "the §14.3 payload omits the ranking")
    check(bool(d["priorities"]), "the ranking is empty in the payload")
    p = d["priorities"][0]
    for k in ("rank", "score", "dimensions", "reasoning", "combination_method"):
        check(k in p, f"priority payload missing {k!r}")
    check(json.loads(json.dumps(d, ensure_ascii=False)) == d, "payload is not round-trippable")


def test_safe_language_holds_in_review_mode() -> None:
    for rep in (_review(),
                _review("S16,S17"),
                _review("S02,S05,S21",
                        business_profile=BusinessProfile(model="power_utilities",
                                                         model_confidence="LOW"))):
        bad = safe_language.lint(render(rep))
        check(not bad, f"safe-language lint failed on the planning output: {bad[:3]}")


def test_suppression_still_wins_over_an_assumption() -> None:
    """A signal that is normal for the business model must not fire because we asserted it."""
    rep = _review("S05", business_profile=BusinessProfile(model="power_utilities",
                                                          model_confidence="LOW"))
    s05 = [s for c in rep.clusters for s in c.contributing_signals if s.signal_id == "S05"][0]
    check(s05.status != FIRED,
          "an assumed signal overrode business-model suppression — §15.2 must win")


# ---- §11.2 capacity adjustment ----------------------------------------------------

def _big(capacity: int | None = None):
    return build_report(
        "Capacity Entity", ("FY2024-25",), comparable_years=1,
        assume_fired=frozenset({"S02", "S05", "S09", "S10", "S13", "S21"}),
        business_profile=BusinessProfile(model="manufacturing", model_confidence="LOW"),
        capacity=capacity,
    )


def test_no_capacity_stated_excludes_nothing() -> None:
    """The FDR does not invent a capacity it was not given."""
    rep = _big(None)
    check(rep.below_the_line == (), "items were excluded with no capacity stated")
    check(rep.capacity["capacity"] is None, "a capacity was invented")
    check(len(rep.priorities) == len(rep.raised()), "the shortlist lost a raised cluster")


def test_capacity_fills_in_rank_order() -> None:
    rep = _big(9)
    check(bool(rep.below_the_line), "capacity of 9 excluded nothing from 17 units of work")
    ranks_in = [p.rank for p in rep.priorities]
    ranks_out = [b.rank for b in rep.below_the_line]
    check(ranks_in == sorted(ranks_in), "the shortlist is not in rank order")
    check(max(ranks_in) < min(ranks_out),
          f"a lower-ranked theme was promoted past a higher one: in={ranks_in} out={ranks_out}")


def test_capacity_arithmetic_is_disclosed_and_correct() -> None:
    """§11.2 — the method is disclosed and the score reproducible from stated inputs."""
    rep = _big(9)
    cap = rep.capacity
    used = sum(PL.BY_ID[p.cluster_id].effort_weight for p in rep.priorities)
    check(cap["used"] == used, f"reported used {cap['used']} but shortlist costs {used}")
    check(cap["used"] <= cap["capacity"], "the shortlist exceeds the stated capacity")
    check(cap["unused"] == cap["capacity"] - cap["used"], "unused capacity does not reconcile")
    total = used + sum(b.effort for b in rep.below_the_line)
    check(cap["required"] == total, "required effort does not reconcile to the ranked themes")
    check(bool(cap["method"]), "the capacity method is not disclosed")


def test_excluded_items_are_never_silent() -> None:
    rep = _big(9)
    for b in rep.below_the_line:
        check(bool(b.reason), f"{b.cluster_id}: excluded with no reason")
        check("Not a judgement that the theme is unimportant" in b.reason,
              f"{b.cluster_id}: exclusion reads as a judgement of unimportance")
    text = render(rep)
    check("Below the line" in text, "excluded themes do not appear in the report")
    for b in rep.below_the_line:
        check(b.cluster_id in text, f"{b.cluster_id} excluded and not shown")


def test_blocked_behind_is_explained_accurately() -> None:
    """A theme whose own effort fits must say WHY it was still excluded."""
    rep = _big(9)
    for b in rep.below_the_line:
        remaining = rep.capacity["unused"]
        if "would have fitted" in b.reason:
            check(b.effort <= remaining,
                  f"{b.cluster_id}: claims it would have fitted, but effort {b.effort} > "
                  f"{remaining} remaining")
            check("higher-ranked" in b.reason,
                  f"{b.cluster_id}: no reason given for not promoting it")


def test_by_nature_exclusion_is_escalated() -> None:
    """§11.2 elevates by-nature matters; capacity excluding one needs a human decision."""
    from . import priority as PRI
    from .model import RAISED
    rep = _big(1)      # a capacity below any single theme's effort
    for b in rep.below_the_line:
        if b.by_nature:
            check("MATERIAL BY NATURE AND EXCLUDED BY CAPACITY" in b.reason,
                  f"{b.cluster_id}: by-nature exclusion not escalated")
            break
    else:
        check(False, "no by-nature theme was excluded — the escalation path is untested")


def test_effort_weights_have_a_stated_basis() -> None:
    """§16 — a number nobody can challenge is not explainable."""
    for pc in PL.CONTENT:
        check(1 <= pc.effort_weight <= 5, f"{pc.cluster_id}: effort outside 1-5")
        check(len(pc.effort_basis) > 40, f"{pc.cluster_id}: effort basis is too thin to review")
        check("hour" not in pc.effort_basis.lower(),
              f"{pc.cluster_id}: effort basis implies hours — the scale is relative only")


# ---- matrix export -----------------------------------------------------------------

def test_csv_export_has_every_appendix_e_column() -> None:
    import csv as _csv
    import io as _io
    from . import export
    rep = _big(None)
    rows = list(_csv.reader(_io.StringIO(export.to_csv(rep))))
    check(rows[0] == [label for _, label in export.COLUMNS], "CSV header is not Appendix E")
    body = [r for r in rows[1:] if r and r[0].isdigit()]
    check(len(body) == len(rep.raised()), f"{len(body)} data rows for {len(rep.raised())} clusters")
    for r in body:
        check(len(r) == len(export.COLUMNS), "a CSV row does not match the header width")
        check(all(r[export.COLUMNS.index(c)] for c in export.COLUMNS
                  if c[0] in ("theme", "response_nature", "evidence_request")),
              "a required matrix cell is empty")


def test_exports_carry_the_caveats() -> None:
    from . import export
    rep = _big(9)
    for text in (export.to_csv(rep), export.to_markdown(rep)):
        check("candidate priorities" in text, "the export drops the §10.5 caveat")
        check("REVIEW MODE" in text.upper(), "the export drops the review-mode label")
        check("Below the line" in text or "BELOW THE LINE" in text,
              "the export drops the excluded themes")


def test_exports_pass_the_release_gate() -> None:
    from . import export
    rep = _big(None)
    for text in (export.to_csv(rep), export.to_markdown(rep)):
        check(not safe_language.lint(text), "an export failed the §17.2 lint")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if _fails:
        print(f"FAIL - {len(_fails)} problem(s) in {len(tests)} checks:")
        for f in _fails:
            print(f"  - {f}")
        return 1
    print(f"OK - {len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
