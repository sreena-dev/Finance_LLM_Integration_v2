import datetime
import json
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

# Wave 3 Fix 1: build_contract_exposure_lens rebuilt against the JSON shape recovered
# from decompiling the deleted tool's surviving test bytecode (no source or git history
# for the original exists -- see the Wave 3 plan's Context section). Categories map
# directly onto EPIL's "Other Risk Areas" #1-3: claims revenue with no traceable
# receivable, mobilisation advances secured by bank guarantee, and amounts withheld/
# recoverable from customers and vendors -- one coherent EPC/construction contract-
# accounting domain, not three separate screens.
_CONTRACT_EXPOSURE_CATEGORIES = {
    "claims_receivable": ("income-claims", "claims receivable", "claim recognised", "claim recognized"),
    "mobilisation_advance": ("mob adv", "mobilisation advance", "mobilization advance"),
    "bank_guarantee": ("bank guarantee", "bg -", "against bg", "margin money"),
    # Caught live: EPIL's own gl_name text is shorter/abbreviated than the client
    # remarks' prose ("WITHHELD CUSTOMER" not "withheld by customer", "AMOUNT
    # RECOVERABLE V" not "amount recoverable from vendor", plus a distinct "EMD
    # RECOVERABLE" shape) -- bare "withheld"/"recoverable" keywords match both the
    # client's own phrasing and the entity's real, truncated ledger names, consistent
    # with how sensitive_tags.json's "deposit"/"advance" tags already use bare
    # single-word keywords in this same domain.
    "amounts_withheld_recoverable": (
        "withheld", "recoverable",
    ),
    "retention_money": ("retention money", "retention - "),
    "liquidated_damages": ("liquidated damages",),
}

_TOP_N_ACCOUNTS = 5


@pipeline_tool("build_contract_exposure_lens", domain="valueadd")
def build_contract_exposure_lens(canonical_tb_file: str, output_dir: str = None) -> dict:
    """Screens for EPC/construction contract-accounting exposures a generic keyword
    screen won't cluster together: claims revenue recognised with no traceable
    receivable, mobilisation advances secured by bank guarantee (including one carrying
    an abnormal credit balance -- an asset-named account behaving like a liability),
    amounts withheld/recoverable from customers and vendors, retention money, and
    liquidated damages. Writes contract_exposure.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no contract-exposure screening performed.",
            "artifacts": [], "errors": [],
        }

    categories = {}
    findings = []
    for tag, keywords in _CONTRACT_EXPOSURE_CATEGORIES.items():
        gl = gl_total_by_keywords(tb_df, keywords)
        categories[tag] = {
            "balance": round(gl["balance"], 2),
            "member_count": gl["account_count"],
            "accounts": gl["accounts"][:_TOP_N_ACCOUNTS],
        }
        if gl["account_count"] == 0:
            continue

        if tag == "claims_receivable":
            findings.append(make_record(
                source_screen="build_contract_exposure_lens",
                fsli="Claims receivable",
                amount=gl["balance"],
                observation=(
                    f"{gl['account_count']} account(s) totalling {gl['balance']:,.2f} record income "
                    "from claims (cost escalation, contract variation, or arbitration-linked "
                    "recoveries) against clients or third parties."
                ),
                expectation="Recognised claims income is ordinarily traceable to a receivable, an offset, or a specific balance-sheet account.",
                gap="No balance-sheet account on this trial balance is tagged to record who owes this money.",
                assertions=["Existence", "Valuation", "Rights"],
                risk_basis=["nature", "context"],
                risk_rating="high",
                data_sufficiency="low",
                proposed_response=(
                    "Obtain the claim-by-claim schedule (client, contract, amount claimed vs "
                    "recognised, and the recognition basis) and trace each recognised claim to a "
                    "receivable, an offset, or ask management directly where the corresponding "
                    "debit was posted."
                ),
                evidence_requested=[
                    "Claim-by-claim schedule with client, contract and recognition basis",
                    "Correspondence or arbitration filings evidencing each claim above a set threshold",
                    "Subsequent-events evidence (cash receipts, settlement correspondence, awards)",
                ],
                valid_reasons=[
                    "The receivable is genuinely embedded in a broader trade-receivable account not separately tagged",
                    "The claim is a disclosure-only contingent recovery, not yet recognised as an asset",
                ],
                extra={"screen_id": "CONTRACT-CLAIMS"},
            ))

        if tag == "mobilisation_advance":
            abnormal = [a for a in gl["accounts"] if a["closing_balance"] < 0]
            if abnormal:
                findings.append(make_record(
                    source_screen="build_contract_exposure_lens",
                    fsli="Mobilisation advance",
                    amount=abs(abnormal[0]["closing_balance"]),
                    account=f"{abnormal[0]['gl_code']} - {abnormal[0]['gl_name']}",
                    normal_balance_expectation="Debit",
                    observation=(
                        f"'{abnormal[0]['gl_name']}' is named as a mobilisation advance (an asset) "
                        f"but carries a credit balance of {abs(abnormal[0]['closing_balance']):,.2f}."
                    ),
                    expectation="An advance paid out to a contractor ordinarily carries a debit (asset) balance.",
                    gap="Credit balance on an asset-named advance account -- an over-recovery, a different project's advance netted here, or a liability wrongly grouped under an advances note.",
                    assertions=["Classification", "Existence"],
                    risk_basis=["nature", "context"],
                    risk_rating="medium",
                    data_sufficiency="low",
                    proposed_response="Obtain the ledger detail explaining the credit balance and reclassify if warranted.",
                    evidence_requested=[
                        "Underlying contract and mobilisation schedule",
                        "Ledger detail explaining the credit balance",
                        "Status and expiry of the securing bank guarantee",
                    ],
                    valid_reasons=[
                        "Genuine over-recovery pending refund",
                        "A different project's advance netted here in error",
                    ],
                    extra={"screen_id": "CONTRACT-MOB-ADV-SIGN"},
                ))

        if tag == "amounts_withheld_recoverable" and gl["balance"] > 0:
            findings.append(make_record(
                source_screen="build_contract_exposure_lens",
                fsli="Amounts withheld and recoverable",
                amount=gl["balance"],
                observation=(
                    f"{gl['account_count']} account(s) totalling {gl['balance']:,.2f} record amounts "
                    "withheld by customers or recoverable from customers/vendors/sub-contractors."
                ),
                expectation="A materially sized withheld/recoverable population ordinarily has a party-wise ageing and a stated basis for release or recovery.",
                gap="This population is otherwise unexamined -- no ageing, no stated contractual basis, on this trial balance alone.",
                assertions=["Existence", "Valuation", "Completeness"],
                risk_basis=["value", "context"],
                risk_rating="medium",
                data_sufficiency="low",
                proposed_response="Obtain a party-wise ageing and the contractual basis for each withholding/recoverable balance.",
                evidence_requested=[
                    "Party-wise ageing for each sub-account",
                    "Contractual basis for amounts withheld (performance retention, disputed billing, quality holdback)",
                    "External confirmations from the largest counterparties",
                ],
                valid_reasons=["Ordinary course retention/holdback within the normal contract cycle"],
                extra={"screen_id": "CONTRACT-WITHHELD-RECOVERABLE"},
            ))

    applicable = any(c["member_count"] > 0 for c in categories.values())
    payload = {
        "applicable": applicable,
        "reason": None if applicable else "No contract-exposure-shaped ledger (claims, mobilisation advance, withheld/recoverable, retention, LD) matched this trial balance.",
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "categories": categories,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "contract_exposure.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Contract exposure lens: {len(findings)} finding(s) across {sum(1 for c in categories.values() if c['member_count'] > 0)} matched categor(y/ies).",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 3 Fix 2: build_foreign_operations_lens rebuilt against the recovered JSON shape.
# The existing "foreign currency" sensitive-tag screen only catches currency-symbol
# keywords (client-confirmed: 3 accounts, Rs 2.74cr) -- this lens widens the net to
# geography tags already present in EPIL's own ledger names (Oman, Muscat, Sri Lanka),
# matching the entity's own AS-17 segment disclosure, so the finding is the scope gap
# itself, not a re-detection of the same 3 accounts under a new name.
_FOREIGN_OPS_LOCATION_KEYWORDS = {
    "oman": ("oman", "muscat", "omr"),
    "sri_lanka": ("sri lanka", "lkr"),
}
_FOREIGN_OPS_FX_KEYWORDS = ("us$", "usd", "fcnr", "forex", "foreign currency")


@pipeline_tool("build_foreign_operations_lens", domain="valueadd")
def build_foreign_operations_lens(canonical_tb_file: str, output_dir: str = None) -> dict:
    """Groups FX-denominated and geographically-tagged ledgers (Oman/Muscat/Sri Lanka
    location tags, USD/FCNR currency tags already present in gl_name) into one
    consolidated overseas-exposure view -- the existing foreign-currency sensitive-tag
    screen only catches a narrow currency-symbol keyword set and materially
    understates the entity's actual overseas exposure. Writes foreign_operations.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no foreign-operations screening performed.",
            "artifacts": [], "errors": [],
        }

    ledgers = []
    categories = {}
    narrow_screen_balance = 0.0
    for row in tb_df.iter_rows(named=True):
        name_lower = str(row.get("gl_name") or "").lower()
        try:
            balance = float(row.get("closing_balance") or 0.0)
        except (TypeError, ValueError):
            balance = 0.0

        location = next((loc for loc, kws in _FOREIGN_OPS_LOCATION_KEYWORDS.items() if any(k in name_lower for k in kws)), None)
        fx_denominated = any(k in name_lower for k in _FOREIGN_OPS_FX_KEYWORDS)
        if not location and not fx_denominated:
            continue

        if fx_denominated:
            narrow_screen_balance += abs(balance)

        ledgers.append({
            "gl_code": str(row.get("gl_code") or ""),
            "gl_name": row.get("gl_name"),
            "closing_balance": round(balance, 2),
            "location": location,
            "fx_denominated": fx_denominated,
            "overseas_guarantee": "guarantee" in name_lower or "bg" in name_lower,
        })
        cat_key = location or ("fx_only" if fx_denominated else "other")
        cat = categories.setdefault(cat_key, {"balance": 0.0, "member_count": 0})
        cat["balance"] += abs(balance)
        cat["member_count"] += 1

    for cat in categories.values():
        cat["balance"] = round(cat["balance"], 2)

    total_balance = sum(c["balance"] for c in categories.values())
    findings = []
    if ledgers:
        findings.append(make_record(
            source_screen="build_foreign_operations_lens",
            fsli="Foreign operations exposure",
            amount=round(total_balance, 2),
            observation=(
                f"{len(ledgers)} account(s) totalling {total_balance:,.2f} are FX-denominated or "
                "geographically tagged to an overseas location, against a narrower "
                f"currency-symbol-only screen that would catch {narrow_screen_balance:,.2f}."
            ),
            expectation="The entity's own annual report discloses Domestic and Foreign segments (AS-17); overseas exposure should be identified as a scope matter at the same scale.",
            gap="The existing foreign-currency screen materially understates this exposure by scoping on currency symbols alone, not geography.",
            assertions=["Existence", "Completeness"],
            risk_basis=["value", "context"],
            risk_rating="medium",
            data_sufficiency="low",
            proposed_response="Add a segment-level cut of risk analytics using the geographic tags already present in the account names.",
            evidence_requested=["Segment note from the audited financial statements", "Overseas branch/project registers"],
            valid_reasons=["Some tagged ledgers may be domestic accounts referencing an overseas counterparty only in name"],
            extra={"screen_id": "FOREIGN-OPS-SCOPE-GAP", "narrow_screen_balance": round(narrow_screen_balance, 2)},
        ))

    applicable = bool(ledgers)
    payload = {
        "applicable": applicable,
        "reason": None if applicable else "No FX-denominated or overseas-location-tagged ledger matched this trial balance.",
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "ledgers": ledgers[:20],
        "categories": categories,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "foreign_operations.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Foreign operations lens: {len(ledgers)} ledger(s) identified across {len(categories)} categor(y/ies).",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 3 Fix 3: deposit/margin-money cross-reference (Other Risk Area #4). Cross-
# references the margin-money-against-guarantees sub-balance against Fix 1's
# bank_guarantee category -- the client's own complaint was that this cross-reference
# was never made anywhere in the existing output.
_DEPOSIT_MARGIN_KEYWORDS = ("short term deposit", "short-term deposit", "fixed deposit against margin money", "margin money")


@pipeline_tool("build_deposit_margin_money_screen", domain="valueadd")
def build_deposit_margin_money_screen(canonical_tb_file: str, contract_exposure_file: str = None, output_dir: str = None) -> dict:
    """Screens short-term-deposit and margin-money-against-guarantee accounts, and
    cross-references the margin-money sub-balance against build_contract_exposure_lens's
    bank_guarantee category -- a shortfall would mean guarantee exposure is carried
    without adequate margin cover. Writes deposit_margin_money_screen.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no deposit/margin-money screening performed.",
            "artifacts": [], "errors": [],
        }

    deposits = gl_total_by_keywords(tb_df, _DEPOSIT_MARGIN_KEYWORDS)
    margin_money = gl_total_by_keywords(tb_df, ("margin money",))

    ce_path = Path(contract_exposure_file) if contract_exposure_file else out_dir / "contract_exposure.json"
    guarantee_balance = None
    if ce_path.exists():
        ce_data = safe_load_json(ce_path) or {}
        guarantee_balance = ((ce_data.get("categories") or {}).get("bank_guarantee") or {}).get("balance")

    findings = []
    if deposits["account_count"] > 0:
        cross_ref_note = (
            f"Guarantee book (bank_guarantee category) totals {guarantee_balance:,.2f}; margin money "
            f"of {margin_money['balance']:,.2f} covers "
            f"{(margin_money['balance'] / guarantee_balance * 100):.1f}% of it."
            if guarantee_balance else
            "build_contract_exposure_lens has not run for this output_dir -- guarantee-book cross-reference unavailable."
        )
        findings.append(make_record(
            source_screen="build_deposit_margin_money_screen",
            fsli="Short-term deposits / margin money",
            amount=deposits["balance"],
            observation=(
                f"{deposits['account_count']} account(s) totalling {deposits['balance']:,.2f} are "
                f"short-term deposits, including {margin_money['balance']:,.2f} specifically named as "
                "margin money against guarantees."
            ),
            expectation="Margin-money FD balances are ordinarily reconciled against the bank's guarantee-outstanding statement for the total value of guarantees they secure.",
            gap=cross_ref_note,
            assertions=["Existence", "Valuation"],
            risk_basis=["context"],
            risk_rating="medium" if guarantee_balance else "information_request",
            data_sufficiency="low",
            proposed_response="Obtain FDR certificates and the bank's guarantee-outstanding statement and reconcile margin money against total guarantee value.",
            evidence_requested=["FDR certificates with maturity dates and any lien", "Bank's guarantee-outstanding statement"],
            valid_reasons=["Deposits held for a purpose unrelated to any guarantee"],
            extra={"screen_id": "DEPOSIT-MARGIN-MONEY", "guarantee_balance": guarantee_balance},
        ))

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "deposits": {"balance": deposits["balance"], "member_count": deposits["account_count"], "accounts": deposits["accounts"][:_TOP_N_ACCOUNTS]},
        "margin_money": {"balance": margin_money["balance"], "member_count": margin_money["account_count"]},
        "guarantee_book_balance": guarantee_balance,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "deposit_margin_money_screen.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Deposit/margin-money screen: {deposits['account_count']} deposit account(s), guarantee cross-reference {'available' if guarantee_balance else 'unavailable'}.",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 3 Fix 4: provisions/write-offs (Other Risk Area #5) -- kept explicitly distinct
# from build_estimation_exposure's "Estimated Liabilities" figure per the client's own
# note that these are different categories never previously examined separately.
_PROVISIONS_KEYWORDS = ("provision",)
_WRITEOFF_KEYWORDS = ("write-off", "write off", "written off")


@pipeline_tool("build_provisions_writeoff_screen", domain="valueadd")
def build_provisions_writeoff_screen(canonical_tb_file: str, output_dir: str = None) -> dict:
    """Screens the Provisions note and the Write-Off note as their own distinct
    categories -- deliberately not merged with build_estimation_exposure's Estimated
    Liabilities figure, since the client's own remarks treat them as different
    categories that had never previously been examined separately. Writes
    provisions_writeoff_screen.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no provisions/write-off screening performed.",
            "artifacts": [], "errors": [],
        }

    # "estimated liab" (not the full word) tolerates a real source-data misspelling seen
    # live ("ESTIMATED LIABILTIES", missing an "i") that a longer exclude keyword missed.
    provisions = gl_total_by_keywords(tb_df, _PROVISIONS_KEYWORDS, exclude=("estimated liab",))
    writeoffs = gl_total_by_keywords(tb_df, _WRITEOFF_KEYWORDS)

    findings = []
    if provisions["account_count"] > 0:
        findings.append(make_record(
            source_screen="build_provisions_writeoff_screen",
            fsli="Provisions",
            amount=provisions["balance"],
            observation=f"{provisions['account_count']} account(s) totalling {provisions['balance']:,.2f} are tagged as provisions, distinct from Estimated Liabilities.",
            expectation="Each provision ordinarily has a stated computation basis tied to an underlying contract, claim or obligation.",
            gap="This population has not previously been examined as its own category, separate from Estimated Liabilities.",
            assertions=["Valuation", "Completeness"],
            risk_basis=["nature", "context"],
            risk_rating="medium",
            data_sufficiency="low",
            proposed_response="Obtain the computation basis for each provision and test for consistency with the underlying obligation.",
            evidence_requested=["Provision computation workings", "Underlying contracts/claims each provision relates to"],
            valid_reasons=["Genuine, well-supported provisions requiring no adjustment"],
            extra={"screen_id": "PROVISIONS"},
        ))
    if writeoffs["account_count"] > 0:
        findings.append(make_record(
            source_screen="build_provisions_writeoff_screen",
            fsli="Write-offs",
            amount=writeoffs["balance"],
            observation=f"{writeoffs['account_count']} account(s) totalling {writeoffs['balance']:,.2f} are tagged as write-offs for doubtful recovery.",
            expectation="A write-off ordinarily carries evidence of competent sanction and prior recovery efforts.",
            gap="Sanction and recovery-effort evidence cannot be established from the trial balance alone.",
            assertions=["Valuation", "Completeness"],
            risk_basis=["nature", "context"],
            risk_rating="medium",
            regularity_flag=True,
            data_sufficiency="low",
            proposed_response="Verify competent sanction was obtained and review recovery efforts made before the write-off was booked -- standard propriety testing in a public-sector entity.",
            evidence_requested=["Sanction/approval evidencing authority for each write-off", "Recovery-effort documentation"],
            valid_reasons=["Properly sanctioned, well-documented write-offs"],
            extra={"screen_id": "WRITEOFF"},
        ))

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "provisions": {"balance": provisions["balance"], "member_count": provisions["account_count"], "accounts": provisions["accounts"][:_TOP_N_ACCOUNTS]},
        "write_offs": {"balance": writeoffs["balance"], "member_count": writeoffs["account_count"], "accounts": writeoffs["accounts"][:_TOP_N_ACCOUNTS]},
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "provisions_writeoff_screen.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Provisions/write-off screen: {provisions['account_count']} provision(s), {writeoffs['account_count']} write-off account(s).",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 3 Fix 5: MSME interest disclosure gap (Other Risk Area #6). The finding here is
# the ABSENCE of any interest-on-delayed-payment account despite a materially-sized
# MSME payable balance -- a disclosure gap, not a computed ratio.
_MSME_KEYWORDS = ("msme", "micro enterprise", "small enterprise")
_MSME_INTEREST_KEYWORDS = ("interest on delayed payment", "msme interest", "interest to msme", "interest - msme")


@pipeline_tool("build_msme_interest_screen", domain="valueadd")
def build_msme_interest_screen(canonical_tb_file: str, materiality_file: str = None, output_dir: str = None) -> dict:
    """Screens for the MSMED Act's 45-day-payment-window interest disclosure -- flags
    a materially-sized MSME payable balance with no corresponding interest-on-delayed-
    payment account anywhere on the trial balance. Writes msme_interest_screen.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no MSME interest screening performed.",
            "artifacts": [], "errors": [],
        }

    mat_path = Path(materiality_file) if materiality_file else out_dir / "materiality.json"
    mat = safe_load_json(mat_path) if mat_path.exists() else {}
    trivial = float((mat.get("thresholds") or {}).get("clearly_trivial") or 0.0)

    msme_payables = gl_total_by_keywords(tb_df, _MSME_KEYWORDS)
    msme_interest = gl_total_by_keywords(tb_df, _MSME_INTEREST_KEYWORDS)

    findings = []
    disclosure_gap = msme_payables["account_count"] > 0 and msme_interest["account_count"] == 0 and (not trivial or msme_payables["balance"] >= trivial)
    if disclosure_gap:
        findings.append(make_record(
            source_screen="build_msme_interest_screen",
            fsli="MSME payables",
            amount=msme_payables["balance"],
            observation=(
                f"MSME payables of {msme_payables['balance']:,.2f} across {msme_payables['account_count']} "
                "account(s) exist, but no account anywhere on the trial balance records interest on "
                "delayed payment to micro/small enterprises."
            ),
            expectation="The MSMED Act requires disclosure of interest on delayed payments beyond the statutory 45-day window.",
            gap="No interest account exists -- either no payment breached the window, or the disclosure is simply absent.",
            assertions=["Completeness", "Presentation"],
            risk_basis=["nature", "context"],
            risk_rating="medium",
            regularity_flag=True,
            data_sufficiency="low",
            proposed_response="Obtain MSME vendor declarations and test payment dates against the 45-day window; quantify any omitted interest for disclosure.",
            evidence_requested=["MSME vendor registration declarations", "Payment-date testing against the 45-day window"],
            valid_reasons=["No payment actually breached the 45-day window this year"],
            extra={"screen_id": "MSME-INTEREST-DISCLOSURE-GAP"},
        ))

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "msme_payables": {"balance": msme_payables["balance"], "member_count": msme_payables["account_count"]},
        "msme_interest_accounts_found": msme_interest["account_count"],
        "disclosure_gap": disclosure_gap,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "msme_interest_screen.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"MSME interest screen: {'disclosure gap flagged' if disclosure_gap else 'no gap flagged'}.",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 8 remark #25: unbilled revenue growing while trade receivables fall (or vice
# versa) is a genuine Ind AS 115 timing signal independent of either balance's absolute
# materiality -- the pattern matters, not the size. Movement needs a prior-period TB;
# this degrades gracefully to a single-period balance report when none is supplied
# (the same optional-file convention used throughout fsli.py/risk.py).
_UNBILLED_REVENUE_KEYWORDS = ("unbilled revenue", "unbilled income")
_TRADE_RECEIVABLES_KEYWORDS = ("trade receivable",)
_DIVERGENT_MOVEMENT_THRESHOLD = 0.0


@pipeline_tool("build_unbilled_revenue_screen", domain="valueadd")
def build_unbilled_revenue_screen(canonical_tb_file: str, prior_canonical_tb_file: str = None, output_dir: str = None) -> dict:
    """Cross-references unbilled revenue against trade receivables movement (Ind AS 115
    timing question) -- unbilled revenue rising while billed receivables fall (or vice
    versa) beyond a threshold is the real signal, independent of either balance's
    absolute materiality gating it out of other screens. Writes unbilled_revenue_screen.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no unbilled revenue screening performed.",
            "artifacts": [], "errors": [],
        }

    unbilled = gl_total_by_keywords(tb_df, _UNBILLED_REVENUE_KEYWORDS)
    receivables = gl_total_by_keywords(tb_df, _TRADE_RECEIVABLES_KEYWORDS)

    prior_unbilled_balance = None
    prior_receivables_balance = None
    unbilled_movement = None
    receivables_movement = None
    divergent_movement = False
    if prior_canonical_tb_file:
        prior_path = Path(prior_canonical_tb_file)
        if prior_path.exists():
            prior_df = load_canonical_tb(prior_path)
            if prior_df is not None and not prior_df.is_empty():
                prior_unbilled = gl_total_by_keywords(prior_df, _UNBILLED_REVENUE_KEYWORDS)
                prior_receivables = gl_total_by_keywords(prior_df, _TRADE_RECEIVABLES_KEYWORDS)
                prior_unbilled_balance = prior_unbilled["balance"]
                prior_receivables_balance = prior_receivables["balance"]
                unbilled_movement = unbilled["balance"] - prior_unbilled_balance
                receivables_movement = receivables["balance"] - prior_receivables_balance
                divergent_movement = (
                    (unbilled_movement > _DIVERGENT_MOVEMENT_THRESHOLD and receivables_movement < -_DIVERGENT_MOVEMENT_THRESHOLD)
                    or (unbilled_movement < -_DIVERGENT_MOVEMENT_THRESHOLD and receivables_movement > _DIVERGENT_MOVEMENT_THRESHOLD)
                )

    findings = []
    if divergent_movement:
        findings.append(make_record(
            source_screen="build_unbilled_revenue_screen",
            fsli="Unbilled Revenue",
            amount=unbilled["balance"],
            observation=(
                f"Unbilled revenue moved by {unbilled_movement:,.2f} while trade receivables moved by "
                f"{receivables_movement:,.2f} in the opposite direction over the same period."
            ),
            expectation="Unbilled revenue and billed receivables ordinarily move together as work is billed -- a rising unbilled balance alongside falling receivables is a timing question, not a balance question.",
            gap="No billing/invoicing schedule is available on the trial balance alone to corroborate the recognition timing.",
            assertions=["Cut-off", "Valuation"],
            risk_basis=["nature", "context", "movement"],
            risk_rating="high",
            data_sufficiency="low",
            proposed_response="Obtain the billing schedule and contract milestones supporting unbilled revenue recognized in the period; corroborate against subsequent invoicing.",
            evidence_requested=["Billing/invoicing schedule", "Contract milestone completion evidence"],
            valid_reasons=["Genuine timing lag between milestone completion and invoice raising, evidenced by the contract schedule"],
            extra={"screen_id": "UNBILLED-REVENUE-DIVERGENCE"},
        ))
    elif unbilled["account_count"] > 0:
        findings.append(make_record(
            source_screen="build_unbilled_revenue_screen",
            fsli="Unbilled Revenue",
            amount=unbilled["balance"],
            observation=f"{unbilled['account_count']} account(s) totalling {unbilled['balance']:,.2f} are tagged as unbilled revenue.",
            expectation="Unbilled revenue ordinarily converts to billed receivables as milestones are invoiced.",
            gap="No prior-period trial balance was supplied, so no movement-correlation check against receivables could be performed.",
            assertions=["Cut-off", "Valuation"],
            risk_basis=["nature"],
            risk_rating="medium",
            data_sufficiency="low",
            proposed_response="Obtain a prior-period trial balance to test the movement-correlation pattern against receivables.",
            evidence_requested=["Prior-period trial balance"],
            valid_reasons=["Balance is genuinely new or immaterial this period"],
            extra={"screen_id": "UNBILLED-REVENUE-BALANCE-ONLY"},
        ))

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "unbilled_revenue": {"balance": unbilled["balance"], "member_count": unbilled["account_count"], "accounts": unbilled["accounts"][:_TOP_N_ACCOUNTS]},
        "trade_receivables": {"balance": receivables["balance"], "member_count": receivables["account_count"], "accounts": receivables["accounts"][:_TOP_N_ACCOUNTS]},
        "prior_unbilled_revenue_balance": prior_unbilled_balance,
        "prior_trade_receivables_balance": prior_receivables_balance,
        "unbilled_revenue_movement": unbilled_movement,
        "trade_receivables_movement": receivables_movement,
        "divergent_movement": divergent_movement,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "unbilled_revenue_screen.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"Unbilled revenue screen: {'divergent movement flagged' if divergent_movement else 'no divergence flagged'}.",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 8 remark #26: a dormant DTA (zero opening-to-closing movement) alongside no
# current-tax P&L line at all is an Ind AS 12 recoverability question -- two facts from
# two different populations that no existing screen joins.
_DTA_KEYWORDS = ("dta", "deferred tax asset")
# Scoped to bs_pl == "PL" only -- Schedule III's "Current Tax Liabilities (Net)" is a
# BALANCE-SHEET note (GST/TCS/cess payables) that false-matches "current tax" if this
# scan isn't restricted to P&L rows (caught during Wave 5's own live re-triage of this
# exact screen).
_CURRENT_TAX_KEYWORDS = ("current tax", "provision for tax", "income tax expense")


@pipeline_tool("build_dta_recoverability_screen", domain="valueadd")
def build_dta_recoverability_screen(canonical_tb_file: str, output_dir: str = None) -> dict:
    """Flags a DTA balance with zero opening-to-closing movement alongside the absence
    of any current-tax P&L line -- the Ind AS 12 recoverability question. Writes
    dta_recoverability_screen.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no DTA recoverability screening performed.",
            "artifacts": [], "errors": [],
        }

    dta = gl_total_by_keywords(tb_df, _DTA_KEYWORDS)
    pl_rows = tb_df.filter(pl.col("bs_pl") == "PL") if "bs_pl" in tb_df.columns else tb_df
    current_tax = gl_total_by_keywords(pl_rows, _CURRENT_TAX_KEYWORDS)

    zero_movement_accounts = []
    if dta["account_count"] > 0 and "opening_balance" in tb_df.columns:
        cols = [c for c in ("gl_name", "main_head", "sub_head_1", "sub_head_2") if c in tb_df.columns]
        mask = pl.lit(False)
        for col in cols:
            lowered = pl.col(col).cast(pl.Utf8).str.to_lowercase().fill_null("")
            for kw in _DTA_KEYWORDS:
                mask = mask | lowered.str.contains(kw, literal=True)
        dta_rows = tb_df.filter(mask)
        for r in dta_rows.iter_rows(named=True):
            opening = float(r.get("opening_balance") or 0.0)
            closing = float(r.get("closing_balance") or 0.0)
            if abs(opening - closing) < 0.01:
                zero_movement_accounts.append({"gl_code": str(r.get("gl_code") or ""), "gl_name": str(r.get("gl_name") or ""), "balance": closing})

    no_current_tax_line = current_tax["account_count"] == 0
    recoverability_flag = bool(zero_movement_accounts) and no_current_tax_line

    findings = []
    if recoverability_flag:
        findings.append(make_record(
            source_screen="build_dta_recoverability_screen",
            fsli="Deferred Tax Asset",
            amount=dta["balance"],
            observation=(
                f"{len(zero_movement_accounts)} DTA account(s) show zero opening-to-closing movement, "
                "and no current-tax P&L line exists anywhere on the trial balance."
            ),
            expectation="A recoverable DTA ordinarily unwinds against future taxable profit, evidenced by a current-tax P&L charge or credit each year.",
            gap="No current-tax P&L line exists to corroborate the DTA's continued recoverability.",
            assertions=["Valuation", "Existence"],
            risk_basis=["nature", "context", "movement"],
            risk_rating="high",
            data_sufficiency="low",
            proposed_response="Obtain management's taxable-profit projections and DTA recoverability workings under Ind AS 12; corroborate the absence of a current-tax charge with the tax computation.",
            evidence_requested=["DTA recoverability workings / taxable-profit projections", "Tax computation for the period"],
            valid_reasons=["Entity is in a tax-loss carryforward position where no current-tax charge is genuinely expected"],
            extra={"screen_id": "DTA-RECOVERABILITY"},
        ))

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "dta": {"balance": dta["balance"], "member_count": dta["account_count"], "accounts": dta["accounts"][:_TOP_N_ACCOUNTS]},
        "zero_movement_accounts": zero_movement_accounts,
        "current_tax_pl_accounts_found": current_tax["account_count"],
        "recoverability_flag": recoverability_flag,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "dta_recoverability_screen.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"DTA recoverability screen: {'recoverability question flagged' if recoverability_flag else 'no flag'}.",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }


# Wave 8 remark #27: isolates the WIP/contract-asset GL range and compares it against
# total revenue -- REL-09 (risk.py's expected_relationships.json) answers a genuinely
# different question (purchases/consumption vs combined inventory basket) and is left
# untouched; this screen's question is specifically "is WIP implausibly small relative
# to revenue scale".
_WIP_CONTRACT_ASSET_KEYWORDS = ("work in progress", "wip", "contract asset")
_REVENUE_KEYWORDS = ("revenue from operations",)
_WIP_REVENUE_IMPLAUSIBILITY_THRESHOLD = 0.0001


@pipeline_tool("build_wip_contract_asset_screen", domain="valueadd")
def build_wip_contract_asset_screen(canonical_tb_file: str, output_dir: str = None) -> dict:
    """Compares the isolated WIP/contract-asset total against total revenue and flags
    when it's implausibly small relative to revenue scale. Writes
    wip_contract_asset_screen.json."""
    out_dir = resolve_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tb_path = Path(canonical_tb_file)
    if not tb_path.exists():
        raise PipelineFileError(str(tb_path), "Canonical TB (supply canonical_tb_file)")

    tb_df = load_canonical_tb(tb_path)
    if tb_df is None or tb_df.is_empty():
        return {
            "execution_status": "SUCCESS", "pipeline_status": "SUCCESS", "can_continue": True,
            "message": "Canonical TB is empty -- no WIP/contract-asset screening performed.",
            "artifacts": [], "errors": [],
        }

    wip = gl_total_by_keywords(tb_df, _WIP_CONTRACT_ASSET_KEYWORDS)
    revenue = gl_total_by_keywords(tb_df, _REVENUE_KEYWORDS)

    ratio = (wip["balance"] / revenue["balance"]) if revenue["balance"] else None
    implausibly_small = bool(
        wip["account_count"] > 0 and revenue["balance"]
        and ratio is not None and ratio < _WIP_REVENUE_IMPLAUSIBILITY_THRESHOLD
    )

    findings = []
    if implausibly_small:
        findings.append(make_record(
            source_screen="build_wip_contract_asset_screen",
            fsli="Work in Progress / Contract Asset",
            amount=wip["balance"],
            observation=(
                f"WIP/contract asset totals {wip['balance']:,.2f} against revenue of {revenue['balance']:,.2f} "
                f"(ratio {ratio:.2e}) -- implausibly small for a construction/EPC entity mid-execution on active contracts."
            ),
            expectation="A construction/EPC entity ordinarily carries a WIP/contract-asset balance proportionate to unbilled work on its active contracts.",
            gap="No contract-progress schedule is available on the trial balance alone to explain why WIP is this small relative to revenue.",
            assertions=["Completeness", "Valuation"],
            risk_basis=["nature", "context"],
            risk_rating="high",
            data_sufficiency="low",
            proposed_response="Obtain the contract-progress/percentage-of-completion schedule and reconcile it against the WIP/contract-asset balance.",
            evidence_requested=["Contract-progress / percentage-of-completion schedule", "List of active contracts with billing status"],
            valid_reasons=["Entity genuinely bills concurrently with progress, leaving little unbilled WIP at period end"],
            extra={"screen_id": "WIP-CONTRACT-ASSET-VS-REVENUE"},
        ))

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "data_sufficiency": "low",
        "wip_contract_asset": {"balance": wip["balance"], "member_count": wip["account_count"], "accounts": wip["accounts"][:_TOP_N_ACCOUNTS]},
        "revenue": {"balance": revenue["balance"], "member_count": revenue["account_count"]},
        "ratio": ratio,
        "implausibly_small": implausibly_small,
        "finding_records": findings,
        "safe_limitation": SAFE_WORDING_DISCLAIMER,
    }

    out_path = out_dir / "wip_contract_asset_screen.json"
    with atomic_write(out_path) as tmp:
        Path(tmp).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "execution_status": "SUCCESS",
        "pipeline_status": "SUCCESS",
        "can_continue": True,
        "message": f"WIP/contract-asset screen: {'implausibly small vs revenue' if implausibly_small else 'no flag'}.",
        "artifacts": [str(out_path.resolve())],
        "errors": [],
    }

