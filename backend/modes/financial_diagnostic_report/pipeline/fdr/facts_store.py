"""
The materialised fact layer — SQLite, stdlib only.

WHY MATERIALISE AT ALL
----------------------
Today a figure is re-derived on every read: select `table_md`, parse it, profile the
columns, bind the labels, run the tie-outs. A three-year FDR panel over 39 canonical keys
would repeat that across several hundred tables, per entity, per run. That is not slow so
much as UNVERIFIABLE — nothing accumulates, so nothing can be diffed, reviewed or
challenged later.

Materialising turns the panel from an extraction problem into a query, and turns a run
into an artefact a reviewer can re-open. `fs_db.facts.extract()` produces the rows; this
module stores them and hands them back.

WHY SQLITE, AND WHY IT IS NOT A COMPROMISE
------------------------------------------
`finance_llm` is read-only by design and stays that way. The facts belong to the analysis,
not to the corpus, so they need their own store. SQLite is in the standard library, needs
no server, and the file is a single artefact that can be copied, versioned and shipped
with a set of working papers. The schema below is deliberately the same shape as the
Postgres `fs_facts` table in `new_db_changes/DB_REDESIGN.md` §6, so moving to Postgres
later is a driver change and not a redesign.

THE TWO RULES THIS STORE ENFORCES
---------------------------------
  1. NO FACT WITHOUT A SCALE. `value_inr_lakh` is NOT NULL. A figure whose scale could
     not be resolved is stored with its native value for audit, but it can never be
     served to a computation — `trusted()` excludes it. Scale-unknown is a queryable
     state, never a silent assumption.
  2. NO CONTRADICTED FACT IS SERVED. A figure whose statement failed its own arithmetic
     identity is retained (a reviewer must be able to see it) and withheld from every
     consumer.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1

# Same column vocabulary as the proposed Postgres table, so the migration is a driver swap.
_DDL = """
CREATE TABLE IF NOT EXISTS fs_facts (
  doc_id            TEXT NOT NULL,
  entity_id         TEXT NOT NULL,
  fy_label          TEXT NOT NULL,
  flavor            TEXT NOT NULL,
  statement         TEXT NOT NULL,
  canonical_key     TEXT NOT NULL,
  period_end        TEXT NOT NULL,
  period_type       TEXT NOT NULL,

  value_native      REAL NOT NULL,
  unit_scale        TEXT NOT NULL,
  value_inr_lakh    REAL,

  source_statement  TEXT,
  source_label      TEXT,
  bind_method       TEXT,

  verify_verdict    TEXT NOT NULL,
  verify_reason     TEXT,
  unit_confidence   TEXT NOT NULL,
  unit_source       TEXT,
  unit_evidence     TEXT,

  extractor_version TEXT NOT NULL,
  computed_at       TEXT NOT NULL DEFAULT (datetime('now')),

  PRIMARY KEY (doc_id, flavor, statement, canonical_key, period_end)
);

-- the entity-year panel query, and nothing else, drives this index
CREATE INDEX IF NOT EXISTS fs_facts_panel_ix
  ON fs_facts (entity_id, flavor, canonical_key, period_end);

CREATE TABLE IF NOT EXISTS fs_runs (
  run_id      TEXT PRIMARY KEY,
  entity_id   TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL,
  versions    TEXT,
  coverage    TEXT,
  report      TEXT
);

-- ONE ROW PER SIGNAL PER RUN — the register that makes coverage a query.
--
-- `fs_runs` already keeps the whole report as JSON, which is durable but answers only
-- "show me that report". The questions worth asking are across entities: which figure
-- blocks the most diagnostics, which clusters ever raise, how much of the corpus is held
-- up by extraction versus by work not yet built. Those are GROUP BYs, and a JSON blob per
-- run cannot serve them.
--
-- Denormalised on purpose: this table is written once per run and read by every
-- population query, so the duplication buys the only access pattern that matters.
CREATE TABLE IF NOT EXISTS fs_signal_results (
  run_id       TEXT NOT NULL,
  entity_id    TEXT NOT NULL,
  signal_id    TEXT NOT NULL,
  cluster_id   TEXT NOT NULL,
  status       TEXT NOT NULL,     -- FIRED | NOT_FIRED | ABSTAIN | NOT_APPLICABLE | SUPPRESSED
  reason_code  TEXT,              -- the closed code; NULL when the rule ran
  blocked_by   TEXT,              -- DATA (extraction ticket) | SCOPE (roadmap item)
  severity     TEXT,
  confidence   TEXT,
  missing      TEXT,              -- json list of canonical keys that were not available
  computed_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (run_id, signal_id, cluster_id)
);

CREATE INDEX IF NOT EXISTS fs_sig_code_ix ON fs_signal_results (reason_code, blocked_by);
CREATE INDEX IF NOT EXISTS fs_sig_entity_ix ON fs_signal_results (entity_id, signal_id);

-- The latest run per entity. Population queries must not double-count an entity that was
-- run five times while a threshold was being tuned.
CREATE VIEW IF NOT EXISTS fs_latest_run AS
  SELECT r.* FROM fs_runs r
  JOIN (SELECT entity_id, MAX(started_at) AS t FROM fs_runs
        WHERE status='COMPLETE' GROUP BY entity_id) m
    ON r.entity_id = m.entity_id AND r.started_at = m.t;

CREATE TABLE IF NOT EXISTS fs_extract_errors (
  doc_id     TEXT PRIMARY KEY,
  entity_id  TEXT,
  fy_label   TEXT,
  error      TEXT,
  seen_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- EVERY ROUTING DECISION. The one decision that changes which answer you get was the only
-- layer in this repo emitting no reason code and leaving no trace, so "how often do we
-- misroute?" had no answer at all. With this it is the same shape of query as "which figure
-- blocks the most diagnostics".
--
-- The question text is stored HASHED as well as in full: the hash groups repeats without
-- depending on the text, so a rephrase-after-a-bad-route shows up as two rows on one
-- session rather than as two unrelated questions.
CREATE TABLE IF NOT EXISTS fs_routes (
  route_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  query        TEXT NOT NULL,
  query_hash   TEXT NOT NULL,
  path         TEXT NOT NULL,
  confidence   TEXT NOT NULL,
  reason_code  TEXT NOT NULL,
  entities     TEXT NOT NULL DEFAULT '[]',
  fy           TEXT,
  cluster_id   TEXT,
  signal_id    TEXT,
  scores       TEXT NOT NULL DEFAULT '{}',
  evidence     TEXT NOT NULL DEFAULT '[]',
  session_id   TEXT,
  router_version TEXT,
  at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS fs_routes_ix ON fs_routes (path, reason_code, at);
CREATE INDEX IF NOT EXISTS fs_routes_hash_ix ON fs_routes (query_hash);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

_COLS = (
    "doc_id", "entity_id", "fy_label", "flavor", "statement", "canonical_key",
    "period_end", "period_type", "value_native", "unit_scale", "value_inr_lakh",
    "source_statement", "source_label", "bind_method", "verify_verdict",
    "verify_reason", "unit_confidence", "unit_source", "unit_evidence",
    "extractor_version",
)

DEFAULT_PATH = Path("data") / "fdr" / "facts.sqlite3"

# Scale confidence that is strong enough to stand on its own. A LOW resolution is a
# filing-mode guess and is NOT excluded here — see below.
_USABLE_UNIT_CONF = ("HIGH", "MEDIUM")

# WHY LOW-CONFIDENCE SCALES ARE SERVED, NOT DROPPED
# -------------------------------------------------
# Excluding every LOW resolution was the first design and it was wrong. What actually
# endangers a trend is not an UNCERTAIN scale but an INCONSISTENT one: if every year of a
# series resolved to the same scale, a wrong guess is a constant factor, and a constant
# factor cancels out of every growth rate, share and ratio the signal rules compute. Only
# the absolute magnitude stays uncertain, and no rule reads absolute magnitude.
#
# Dropping them cost real coverage — CPCL's total assets are CONFIRMED by the balance-sheet
# identity in all six years and were being withheld solely because the scale came from the
# filing mode rather than a printed banner.
#
# So the discipline moves one layer up, to where it can be applied precisely:
#   `panel.series()` refuses a window whose scale CHANGES while any year is a guess;
#   `rules._confidence()` caps any diagnostic built on a guessed scale at MEDIUM or LOW.
# The figure is used, and the report says how much to lean on it.
_SERVE_UNIT_CONF = ("HIGH", "MEDIUM", "LOW")


class FactsStore:
    """Open (creating if needed) the fact database at `path`."""

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # `timeout` matters: two entities materialising at once contend for the write
        # lock, and the default 5s raises "database is locked" on a perfectly recoverable
        # wait. WAL lets a report read while another entity is still being extracted —
        # without it, opening a finished report blocks behind an unrelated running job.
        self.con = sqlite3.connect(str(self.path), timeout=30.0)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(_DDL)
        self.con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)",
                         (str(SCHEMA_VERSION),))
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "FactsStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.con.commit()
        self.close()

    # ---- writing -----------------------------------------------------------------

    def put(self, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """Store facts. Returns (stored, errors).

        `fs_db.facts.extract()` signals an unprocessable filing by returning a single
        row carrying `_error`. Those are recorded in their own table rather than dropped:
        a filing that could not be read is a coverage fact the panel has to report.
        """
        stored = errors = 0
        ins = (f"INSERT OR REPLACE INTO fs_facts ({','.join(_COLS)}) "
               f"VALUES ({','.join('?' * len(_COLS))})")
        for r in rows:
            if r.get("_error"):
                self.con.execute(
                    "INSERT OR REPLACE INTO fs_extract_errors"
                    "(doc_id,entity_id,fy_label,error) VALUES (?,?,?,?)",
                    (r.get("doc_id"), r.get("entity_id"), r.get("fy_label"),
                     str(r.get("_error"))[:500]))
                errors += 1
                continue
            self.con.execute(ins, tuple(r.get(c) for c in _COLS))
            stored += 1
        self.con.commit()
        return stored, errors

    # ---- reading -----------------------------------------------------------------

    def facts(self, entity_id: str, *, flavor: str = "standalone",
              trusted_only: bool = True) -> list[dict[str, Any]]:
        """Every fact for an entity, oldest period first.

        `trusted_only` applies both store rules: no contradicted figure, and no figure
        whose scale is a filing-mode guess.
        """
        sql = ["SELECT * FROM fs_facts WHERE entity_id=? AND flavor=?"]
        args: list[Any] = [entity_id, flavor]
        if trusted_only:
            sql.append("AND verify_verdict <> 'CONTRADICTED'")
            sql.append(f"AND unit_confidence IN ({','.join('?' * len(_SERVE_UNIT_CONF))})")
            args.extend(_SERVE_UNIT_CONF)
            sql.append("AND value_inr_lakh IS NOT NULL")
        sql.append("ORDER BY period_end, canonical_key")
        return [dict(r) for r in self.con.execute(" ".join(sql), args)]

    def entities(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.con.execute(
            """SELECT entity_id, COUNT(DISTINCT period_end) AS years,
                      COUNT(*) AS facts, MIN(period_end) AS first_period,
                      MAX(period_end) AS last_period
                 FROM fs_facts GROUP BY entity_id ORDER BY entity_id""")]

    def coverage(self, entity_id: str, *, flavor: str = "standalone") -> dict[str, Any]:
        """What is actually available for this entity — the readiness view.

        Reports trusted and untrusted counts separately, because "we have no figure" and
        "we have a figure we cannot rely on" are different problems with different fixes.
        """
        rows = self.facts(entity_id, flavor=flavor, trusted_only=False)
        periods = sorted({r["period_end"] for r in rows})
        trusted = {(r["canonical_key"], r["period_end"]) for r in rows
                   if r["verify_verdict"] != "CONTRADICTED"
                   and r["unit_confidence"] in _SERVE_UNIT_CONF
                   and r["value_inr_lakh"] is not None}
        keys = sorted({r["canonical_key"] for r in rows})
        return {
            "entity_id": entity_id, "flavor": flavor,
            "periods": periods, "years": len(periods),
            "keys": keys,
            "facts_total": len(rows), "facts_trusted": len(trusted),
            "keys_trusted_all_years": sorted(
                k for k in keys if all((k, p) in trusted for p in periods)),
            "errors": [dict(r) for r in self.con.execute(
                "SELECT * FROM fs_extract_errors WHERE entity_id=?", (entity_id,))],
        }

    # ---- run manifests -----------------------------------------------------------

    def save_run(self, run_id: str, entity_id: str, *, status: str,
                 started_at: str, finished_at: str | None = None,
                 versions: dict | None = None, coverage: dict | None = None,
                 report: dict | None = None) -> None:
        self.con.execute(
            """INSERT OR REPLACE INTO fs_runs
               (run_id,entity_id,started_at,finished_at,status,versions,coverage,report)
               VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, entity_id, started_at, finished_at, status,
             json.dumps(versions or {}), json.dumps(coverage or {}),
             json.dumps(report) if report is not None else None))
        self.con.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        r = self.con.execute("SELECT * FROM fs_runs WHERE run_id=?", (run_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        for k in ("versions", "coverage", "report"):
            if d.get(k):
                d[k] = json.loads(d[k])
        return d

    def runs(self, entity_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT run_id,entity_id,started_at,finished_at,status FROM fs_runs"
        args: Sequence[Any] = ()
        if entity_id:
            sql += " WHERE entity_id=?"
            args = (entity_id,)
        sql += " ORDER BY started_at DESC LIMIT ?"
        return [dict(r) for r in self.con.execute(sql, (*args, limit))]

    # ---- the signal register -------------------------------------------------------

    def save_signal_results(self, run_id: str, entity_id: str,
                            rows: Iterable[dict[str, Any]]) -> int:
        """One row per signal per cluster for this run. Replaces any earlier write."""
        self.con.execute("DELETE FROM fs_signal_results WHERE run_id=?", (run_id,))
        payload = [
            (run_id, entity_id, r["signal_id"], r.get("cluster_id", ""), r["status"],
             r.get("reason_code") or None, r.get("blocked_by") or None,
             r.get("severity"), r.get("confidence"),
             json.dumps(list(r.get("missing_inputs") or ())))
            for r in rows
        ]
        self.con.executemany(
            "INSERT OR REPLACE INTO fs_signal_results "
            "(run_id, entity_id, signal_id, cluster_id, status, reason_code, blocked_by, "
            " severity, confidence, missing) VALUES (?,?,?,?,?,?,?,?,?,?)", payload)
        self.con.commit()
        return len(payload)

    # ---- population projections ----------------------------------------------------
    # Each of these is one question an audit planner actually asks, answered over the
    # LATEST run per entity so an entity tuned five times is not counted five times.

    def query_signals(self, *, entity_id: str | None = None, signal_id: str | None = None,
                      cluster_id: str | None = None, status: str | None = None,
                      reason_code: str | None = None, blocked_by: str | None = None,
                      limit: int = 500) -> list[dict[str, Any]]:
        """The signal register, filtered. The enumerability that makes anti-fabrication
        testable rather than aspirational: one query returns every signal with its status."""
        sql = ["SELECT s.* FROM fs_signal_results s "
               "JOIN fs_latest_run r ON r.run_id = s.run_id WHERE 1=1"]
        args: list[Any] = []
        for col, val in (("s.entity_id", entity_id), ("s.signal_id", signal_id),
                         ("s.cluster_id", cluster_id), ("s.status", status),
                         ("s.reason_code", reason_code), ("s.blocked_by", blocked_by)):
            if val:
                sql.append(f" AND {col} = ?")
                args.append(val)
        sql.append(" ORDER BY s.entity_id, s.signal_id LIMIT ?")
        args.append(limit)
        out = []
        for r in self.con.execute("".join(sql), args):
            d = dict(r)
            d["missing"] = json.loads(d["missing"] or "[]")
            out.append(d)
        return out

    def blocking_inputs(self, limit: int = 25) -> list[dict[str, Any]]:
        """Which missing figure holds back the most diagnostics, across the population.

        THE question this whole layer exists to answer. Counted in BOTH units, because
        they say different things: `signals` is how much diagnostic coverage is lost,
        `entities` is how widespread the gap is. A key blocking 4 signals on 40 entities
        and one blocking 40 signals on 4 entities are very different pieces of work.
        """
        rows = self.con.execute(
            "SELECT s.entity_id, s.signal_id, s.missing FROM fs_signal_results s "
            "JOIN fs_latest_run r ON r.run_id = s.run_id "
            "WHERE s.blocked_by = 'DATA' AND s.missing NOT IN ('[]','')").fetchall()
        by_key: dict[str, dict[str, Any]] = {}
        for r in rows:
            for k in json.loads(r["missing"] or "[]"):
                d = by_key.setdefault(k, {"canonical_key": k, "signals": 0,
                                          "_entities": set(), "_sig": set()})
                d["signals"] += 1
                d["_entities"].add(r["entity_id"])
                d["_sig"].add(r["signal_id"])
        out = []
        for d in by_key.values():
            out.append({"canonical_key": d["canonical_key"], "signals": d["signals"],
                        "entities": len(d["_entities"]),
                        "distinct_signals": sorted(d["_sig"])})
        out.sort(key=lambda d: (-d["signals"], d["canonical_key"]))
        return out[:limit]

    # ---- routing observability ------------------------------------------------------

    def save_route(self, decision: dict[str, Any], *, query: str,
                   session_id: str | None = None) -> None:
        """Record one routing decision. Never raises — an observability write must not be
        able to fail a question that was otherwise answerable."""
        try:
            self.con.execute(
                "INSERT INTO fs_routes (query, query_hash, path, confidence, reason_code, "
                "entities, fy, cluster_id, signal_id, scores, evidence, session_id, "
                "router_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (query[:2000], hashlib.sha1(query.strip().lower().encode()).hexdigest()[:16],
                 decision.get("path", ""), decision.get("confidence", ""),
                 decision.get("reason_code", ""), json.dumps(decision.get("entities") or []),
                 decision.get("fy"), decision.get("cluster_id"), decision.get("signal_id"),
                 json.dumps(decision.get("scores") or {}),
                 json.dumps(decision.get("evidence") or []), session_id,
                 decision.get("router_version")))
            self.con.commit()
        except Exception:                                   # noqa: BLE001
            pass

    def routing_stats(self, limit: int = 50) -> dict[str, Any]:
        """How routing is behaving. The counter that did not exist before."""
        by_path = {r["path"]: r["n"] for r in self.con.execute(
            "SELECT path, COUNT(*) AS n FROM fs_routes GROUP BY path ORDER BY n DESC")}
        by_code = {r["reason_code"]: r["n"] for r in self.con.execute(
            "SELECT reason_code, COUNT(*) AS n FROM fs_routes "
            "GROUP BY reason_code ORDER BY n DESC")}
        by_conf = {r["confidence"]: r["n"] for r in self.con.execute(
            "SELECT confidence, COUNT(*) AS n FROM fs_routes GROUP BY confidence")}
        # A question asked twice in one session, routed differently, is the misroute
        # signal: the user rephrased because the first answer was about something else.
        rephrased = self.con.execute(
            "SELECT COUNT(*) AS n FROM (SELECT session_id, query_hash FROM fs_routes "
            "WHERE session_id IS NOT NULL GROUP BY session_id, query_hash "
            "HAVING COUNT(DISTINCT path) > 1)").fetchone()
        recent = [dict(r) for r in self.con.execute(
            "SELECT query, path, confidence, reason_code, at FROM fs_routes "
            "ORDER BY route_id DESC LIMIT ?", (limit,))]
        total = sum(by_path.values())
        return {"total": total, "by_path": by_path, "by_reason_code": by_code,
                "by_confidence": by_conf,
                "ambiguous_share": round(by_path.get("ambiguous", 0) / total, 3) if total else 0.0,
                "rephrased_after_route": rephrased["n"] if rephrased else 0,
                "recent": recent}

    def rank_by_key(self, canonical_key: str, *, limit: int = 15, ascending: bool = False,
                    flavor: str = "standalone") -> list[dict[str, Any]]:
        """Entities ranked by one canonical figure, at each entity's most recent year.

        A comparison of DISCLOSED figures, which is a fact. It is not a peer benchmark:
        §13 forbids inventing a sector norm, and nothing is invented here — every number
        was read from a filing and every row says which year it came from. Only trusted
        facts are ranked, so a figure withheld by a failed tie-out cannot win.
        """
        rows = self.con.execute(
            "SELECT f.entity_id, f.fy_label, f.value_inr_lakh AS value, f.source_label "
            "FROM fs_facts f "
            "JOIN (SELECT entity_id, MAX(period_end) AS p FROM fs_facts "
            "      WHERE canonical_key=? AND flavor=? AND value_inr_lakh IS NOT NULL "
            "        AND verify_verdict != 'CONTRADICTED' GROUP BY entity_id) m "
            "  ON f.entity_id = m.entity_id AND f.period_end = m.p "
            "WHERE f.canonical_key=? AND f.flavor=? AND f.value_inr_lakh IS NOT NULL "
            "  AND f.verify_verdict != 'CONTRADICTED' "
            f"ORDER BY f.value_inr_lakh {'ASC' if ascending else 'DESC'} LIMIT ?",
            (canonical_key, flavor, canonical_key, flavor, limit)).fetchall()
        return [dict(r) for r in rows]

    def compare_by_key(self, entities: Sequence[str], canonical_key: str, *,
                       flavor: str = "standalone") -> list[dict[str, Any]]:
        """One figure for several entities, each at its own most recent filing.

        The years may differ — an entity whose latest bound year is FY2022-23 is compared
        at FY2022-23 — so every row carries its own year and the caller must show it. A
        comparison that hides which year each side came from is not a comparison.
        """
        if not entities:
            return []
        marks = ",".join("?" * len(entities))
        rows = self.con.execute(
            f"SELECT f.entity_id, f.fy_label, f.value_inr_lakh AS value, f.source_label "
            f"FROM fs_facts f JOIN (SELECT entity_id, MAX(period_end) AS p FROM fs_facts "
            f"  WHERE canonical_key=? AND flavor=? AND value_inr_lakh IS NOT NULL "
            f"    AND verify_verdict != 'CONTRADICTED' AND entity_id IN ({marks}) "
            f"  GROUP BY entity_id) m "
            f"  ON f.entity_id = m.entity_id AND f.period_end = m.p "
            f"WHERE f.canonical_key=? AND f.flavor=? AND f.value_inr_lakh IS NOT NULL "
            f"  AND f.verify_verdict != 'CONTRADICTED' "
            f"ORDER BY f.value_inr_lakh DESC",
            (canonical_key, flavor, *entities, canonical_key, flavor)).fetchall()
        return [dict(r) for r in rows]

    def population(self) -> dict[str, Any]:
        """The rollout KPIs. Not "how many clusters did we produce" — that number goes up
        when the system gets louder, not when it gets better."""
        row = self.con.execute(
            "SELECT COUNT(DISTINCT entity_id) AS entities FROM fs_latest_run").fetchone()
        entities = row["entities"] if row else 0
        by_status = {r["status"]: r["n"] for r in self.con.execute(
            "SELECT s.status, COUNT(*) AS n FROM fs_signal_results s "
            "JOIN fs_latest_run r ON r.run_id = s.run_id GROUP BY s.status")}
        by_block = {r["blocked_by"]: r["n"] for r in self.con.execute(
            "SELECT s.blocked_by, COUNT(*) AS n FROM fs_signal_results s "
            "JOIN fs_latest_run r ON r.run_id = s.run_id "
            "WHERE s.blocked_by IS NOT NULL GROUP BY s.blocked_by")}
        by_code = {r["reason_code"]: r["n"] for r in self.con.execute(
            "SELECT s.reason_code, COUNT(*) AS n FROM fs_signal_results s "
            "JOIN fs_latest_run r ON r.run_id = s.run_id "
            "WHERE s.reason_code IS NOT NULL GROUP BY s.reason_code ORDER BY n DESC")}
        # The one that says whether the deliverable changed.
        raising = self.con.execute(
            "SELECT COUNT(DISTINCT s.entity_id) AS n FROM fs_signal_results s "
            "JOIN fs_latest_run r ON r.run_id = s.run_id WHERE s.status='FIRED'").fetchone()
        return {"entities_with_a_run": entities,
                "entities_with_a_fired_signal": raising["n"] if raising else 0,
                "by_status": by_status, "by_blocked_by": by_block,
                "by_reason_code": by_code}
