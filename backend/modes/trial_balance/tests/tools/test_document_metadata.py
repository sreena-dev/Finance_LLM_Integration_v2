"""Tests for backend/tools/document_metadata.py -- COMPANY_DETAILS parsing
and framework (AS/IND_AS) resolution, the logic that used to live inline
in db_bridge.py as `_parse_company_details`/a bare FY-year-to-date
conversion, now shared by every input Scenario (A/B/C/D)."""

from datetime import datetime

from modes.trial_balance.pipeline.tools.document_metadata import (
    FrameworkResolution,
    parse_company_details,
    resolve_framework,
)


class TestParseCompanyDetails:
    """Labels are upper-cased but NOT space-normalized (no "Company Name"
    -> "COMPANY_NAME" folding) -- the real template's own COMPANY_DETAILS
    sheet already uses underscored labels directly (COMPANY_NAME,
    ENTITY_ID, PERIOD_START, ...), so tests use that same real format
    rather than a space-separated label a real template wouldn't have."""

    def test_reads_key_value_pairs_from_the_company_sheet(self):
        grid = [["Company Details", None], ["COMPANY_NAME", "ACME Ltd"], ["CIN", "U40105DL2006PTC156884"]]
        metadata = parse_company_details(grid)
        assert metadata.company_name == "ACME Ltd"
        assert metadata.cin == "U40105DL2006PTC156884"

    def test_keys_are_upper_cased(self):
        grid = [["entity_id", "E1"]]
        metadata = parse_company_details(grid)
        assert metadata.entity_id == "E1"

    def test_rows_without_a_value_are_skipped_not_recorded_as_none(self):
        """Unlike the old db_bridge.py version (which recorded a present-
        but-blank cell as None), parse_company_details treats "no value in
        column B at all" as simply not a label/value row -- this is what
        lets it skip the sheet's own title row without hardcoding a
        specific title string."""
        grid = [[None, "orphan value"], ["COMPANY_NAME", "ACME Ltd"], ["CIN", None]]
        metadata = parse_company_details(grid)
        assert metadata.company_name == "ACME Ltd"
        assert metadata.cin is None

    def test_period_start_and_end_parse_the_real_template_js_date_format(self):
        """"05:30:00 GMT+0530" is midnight UTC (local minus the +5:30
        offset) on the same calendar date it names."""
        grid = [["PERIOD_START", "Sun Mar 30 2025 05:30:00 GMT+0530 (India Standard Time)"]]
        metadata = parse_company_details(grid)
        assert metadata.fy_period_start == datetime(2025, 3, 30, 0, 0, 0)

    def test_fy_start_end_bare_year_fallback_used_when_period_start_end_absent(self):
        """TB-v2-git's existing TB_GROUPING_TEMPLATE.xlsx convention (a
        bare fiscal year, not a full JS date string) still resolves via
        the April-March fiscal year convention."""
        grid = [["FY Start", 2025], ["FY End", 2026]]
        metadata = parse_company_details(grid)
        assert metadata.fy_period_start == datetime(2025, 4, 1)
        assert metadata.fy_period_end == datetime(2026, 3, 31)

    def test_period_start_wins_over_fy_start_when_both_present(self):
        grid = [["PERIOD_START", "Sun Mar 30 2025 05:30:00 GMT+0530 (India Standard Time)"], ["FY Start", 2020]]
        metadata = parse_company_details(grid)
        assert metadata.fy_period_start == datetime(2025, 3, 30, 0, 0, 0)

    def test_framework_starts_unresolved_pending_resolve_framework(self):
        grid = [["COMPANY_NAME", "ACME Ltd"]]
        metadata = parse_company_details(grid)
        assert metadata.framework is None
        assert metadata.framework_resolution == FrameworkResolution.UNRESOLVED


class TestResolveFramework:
    def test_explicit_override_wins_over_everything(self):
        framework, resolution = resolve_framework(explicit="AS", company_standards_raw="IND AS")
        assert framework == "AS"
        assert resolution == FrameworkResolution.EXPLICIT

    def test_unambiguous_company_standards_value_resolves(self):
        framework, resolution = resolve_framework(company_standards_raw="Ind AS")
        assert framework == "IND_AS"
        assert resolution == FrameworkResolution.COMPANY_DETAILS

    def test_slash_joined_company_standards_value_is_ambiguous_not_guessed(self):
        framework, resolution = resolve_framework(company_standards_raw="IND AS/ AS")
        assert framework is None
        assert resolution == FrameworkResolution.AMBIGUOUS

    def test_heuristic_grid_used_when_company_standards_absent(self):
        grid = [["Financial Assets"], ["Right of Use"], ["Other Comprehensive Income"]]
        framework, resolution = resolve_framework(heuristic_grid=grid)
        assert framework == "IND_AS"
        assert resolution == FrameworkResolution.HEURISTIC

    def test_nothing_resolvable_is_unresolved_never_defaulted(self):
        framework, resolution = resolve_framework()
        assert framework is None
        assert resolution == FrameworkResolution.UNRESOLVED
