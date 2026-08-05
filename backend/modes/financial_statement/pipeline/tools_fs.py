"""
tool_names.py — Consolidated tools/engines for the Financial Audit RAG system.

Single-file consolidation of (formerly) config.py, db.py, embedder.py,
reranker.py, tools.py, reports_tools.py, compliance_tools.py,
ratios_reference.py, ratio_extraction.py, ratio_tools.py,
audit_schedule_reference.py, audit_risk_tools.py, trend_analysis_tools.py,
and yukta_tools.py. Logic is migrated near-verbatim into classes; see
C:\\Users\\haris\\.claude\\plans\\hey-buddy-i-want-stateful-snowglobe.md for
the full rationale.

Organised with # ===== SECTION N: NAME ===== banners for navigability.

Dedup fixes applied during migration:
  - _strip_html: previously duplicated in reranker.py, llm.py, api_server.py,
    app.py. Canonical copy now lives on Reranker only.
  - tools.py's dead TOOL_DEFINITIONS / execute_tool (unused since
    yukta_tools.py took over tool registration) are dropped.
  - TrendAnalysisTools no longer reimplements _get_latest_fy_end /
    _format_ambiguous — both now live on DocumentResolver and are shared.
    TrendAnalysisTools._resolve_reports keeps its own multi-year-span
    orchestration (that part was never duplicate logic — it already called
    reports_tools._resolve_document per year label), but now calls
    DocumentResolver methods instead of local copies. Behaviour is
    unchanged — see class docstring for the verification notes.
"""

import json
import os
import re
import time
from collections import defaultdict

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()


# ===== SECTION 1: Config =====

class Config:
    """
    Central configuration for the Financial Audit RAG system.

    Loads all settings from environment variables (via .env). Mirrors the
    original config.py module-level constants as class attributes so
    existing call patterns (config.DB_HOST) become Config.DB_HOST with
    minimal call-site changes.

    TABLE_CONFIG is populated at runtime by Database.discover_table_config()
    (called from api_server.py / app.py startup) — starts as an empty list.

    EXCLUDED TABLES NOTE:
      spatial_ref_sys, topology.layer, and topology.topology are PostGIS
      system tables and are automatically excluded by discover_table_config.
    """

    # -----------------------------------------------------------------
    # Database connection
    # -----------------------------------------------------------------
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = os.environ.get("DB_PORT", "5432")
    DB_NAME = os.environ.get("DB_NAME", "financial_llm")
    DB_USER = os.environ.get("DB_USER", "financial_llm")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")

    if not DB_PASSWORD:
        raise EnvironmentError(
            "DB_PASSWORD is not set. "
            "Copy .env.example to .env and fill in your database password."
        )

    # -----------------------------------------------------------------
    # Reports database connection (annual reports — documents/text_chunks/etc.)
    #
    # Optional: unlike DB_PASSWORD above, this does NOT hard-fail at import
    # time. If unset, Database.get_reports_connection() raises when actually
    # called, and agent.py's Orchestrator skips registering the
    # reporting-framework tool — the rest of the pipeline (rules DB) keeps
    # working unaffected.
    # -----------------------------------------------------------------
    REPORTS_DB_HOST = os.environ.get("REPORTS_DB_HOST", "localhost")
    REPORTS_DB_PORT = os.environ.get("REPORTS_DB_PORT", "5432")
    REPORTS_DB_NAME = os.environ.get("REPORTS_DB_NAME", "finance_llm")
    REPORTS_DB_USER = os.environ.get("REPORTS_DB_USER", "bfl")
    REPORTS_DB_PASSWORD = os.environ.get("REPORTS_DB_PASSWORD")

    # -----------------------------------------------------------------
    # Embedding endpoint (bge-m3)
    # -----------------------------------------------------------------
    EMBEDDING_BASE_URL = os.environ.get(
        "EMBEDDING_BASE_URL", "http://localhost:8001/v1/embeddings"
    )
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")

    # -----------------------------------------------------------------
    # LLM endpoint (gemma-4-31b)
    # -----------------------------------------------------------------
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000")
    LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "gemma-4-31b")

    # -----------------------------------------------------------------
    # Reranker endpoint (bge-reranker)
    # -----------------------------------------------------------------
    RERANKER_BASE_URL = os.environ.get("RERANKER_BASE_URL", "http://localhost:8002")
    RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "bge-reranker")

    # -----------------------------------------------------------------
    # Retrieval settings
    # -----------------------------------------------------------------
    TOP_K_PER_TABLE = int(os.environ.get("TOP_K_PER_TABLE", "10"))    # Increased to feed reranker
    TOP_K_PRE_RERANK = int(os.environ.get("TOP_K_PRE_RERANK", "50"))  # Top N chunks sent to reranker
    TOP_K_OVERALL = int(os.environ.get("TOP_K_OVERALL", "15"))        # Final chunks sent to LLM
    SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.0"))

    # Maximum tool-calling iterations per query (guards against infinite loops).
    # Bumped from 5: a single fs-agent turn now has to do everything that used
    # to be split across two agents (classification + rules-DB retrieval +
    # specialized tools + citation validation), so it needs more headroom.
    MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "8"))

    # -----------------------------------------------------------------
    # Arize Phoenix tracing
    # -----------------------------------------------------------------
    # Send only the skill playbook matching the query instead of all ten.
    # Set false to load every playbook (pre-routing behaviour) — the rollback
    # switch if a routing miss is ever suspected.
    PLAYBOOK_ROUTING = os.environ.get("PLAYBOOK_ROUTING", "true").lower() == "true"

    # Send only the tool schemas the routed playbook needs (~4,879 -> ~1,150 tok).
    # DEFAULT OFF: measured to make the model answer general-knowledge questions
    # ("give me the formula for the quick ratio") from memory instead of calling
    # get_ratio_formula — 0-1 of 3 runs called the tool vs 3 of 3 ungated. The
    # answer looks right but is uncited, which is exactly what this system exists
    # to prevent. Adding tools back (9, 12) did not help, so the mechanism is not
    # simply tool count and is not understood. Enable only with an A/B behind it.
    TOOL_GATING = os.environ.get("TOOL_GATING", "false").lower() == "true"

    ENABLE_TRACING = os.environ.get("ENABLE_TRACING", "true").lower() == "true"
    PHOENIX_LAUNCH_LOCAL = os.environ.get("PHOENIX_LAUNCH_LOCAL", "true").lower() == "true"
    PHOENIX_ENDPOINT = os.environ.get("PHOENIX_ENDPOINT") or "http://localhost:4317"
    PHOENIX_PROTOCOL = os.environ.get("PHOENIX_PROTOCOL") or "grpc"
    PHOENIX_PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "simple-rag-financial-audit")

    # -----------------------------------------------------------------
    # TABLE_CONFIG — populated at runtime by Database.discover_table_config().
    # DO NOT edit this list manually.
    # -----------------------------------------------------------------
    TABLE_CONFIG: list[dict] = []

    # -----------------------------------------------------------------
    # HTML_CONTENT_TABLES — also populated at runtime by startup code.
    # Tables whose content columns contain raw HTML markup (strip before LLM).
    # -----------------------------------------------------------------
    HTML_CONTENT_TABLES: set[str] = set()


# ===== SECTION 2: Database =====

class Database:
    """
    PostgreSQL connection and vector similarity search.

    Uses psycopg2 + pgvector's cosine distance operator (<=>). Supports pure
    semantic search and hybrid search (metadata pre-filter + semantic
    ranking within results).
    """

    # Column names recognised as the primary text content of a chunk.
    # Order matters — first match wins.
    _CONTENT_COLUMN_PRIORITY = [
        "text", "chunk", "txt", "facts", "opinion",
        "table_html", "table_content",
    ]

    # Column names recognised as the row primary-key / identifier.
    _ID_COLUMN_PRIORITY = ["chunk_id", "id"]

    # Columns whose content is raw HTML markup (should be stripped before LLM).
    _HTML_CONTENT_COLUMNS = {"table_html", "table_content"}

    # Tables that must NEVER be included (PostGIS system tables, registry itself).
    _EXCLUDE_TABLES = {"spatial_ref_sys", "rag_labels"}
    _EXCLUDE_PREFIXES = ("topology",)

    # Known financial-domain acronyms kept UPPER-CASE in auto-generated labels.
    _KNOWN_ACRONYMS = {
        "irctc", "eac", "cag", "caro", "sa", "as", "ind",
        "html", "iii", "llm", "ias", "ifrs",
    }

    @staticmethod
    def _humanise_table_name(table_name: str) -> str:
        """Convert a snake_case table name to a human-readable label."""
        words = table_name.split("_")

        suffix_parts = []
        while words and words[-1] in ("chunks", "tables", "table", "general",
                                       "query", "appendix"):
            suffix_parts.insert(0, words.pop().title())
        suffix = (" — " + " ".join(suffix_parts)) if suffix_parts else ""

        result_words = [
            w.upper() if w.lower() in Database._KNOWN_ACRONYMS else w.title()
            for w in words
        ]
        return " ".join(result_words) + suffix

    @staticmethod
    def discover_table_config(conn) -> list[dict]:
        """
        Auto-discover all searchable tables from the PostgreSQL database.
        Returns [] on any error so startup never crashes.
        """
        try:
            sql = """
                SELECT table_name, column_name
                FROM   information_schema.columns
                WHERE  table_schema = 'public'
                ORDER  BY table_name, ordinal_position
            """
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as exc:
            print(f"[db] discover_table_config: information_schema query failed: {exc}")
            return []

        table_cols: dict[str, list[str]] = defaultdict(list)
        for table_name, col_name in rows:
            table_cols[table_name].append(col_name)

        custom_labels: dict[str, str] = {}
        if "rag_labels" in table_cols:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT table_name, label FROM public.rag_labels")
                    for tname, label in cur.fetchall():
                        custom_labels[tname] = label
                        custom_labels[tname.removeprefix("public.")] = label
            except Exception as exc:
                print(f"[db] discover_table_config: could not load rag_labels: {exc}")
                try:
                    conn.rollback()
                except Exception:
                    pass

        discovered: list[dict] = []

        for table_name, cols in table_cols.items():
            if table_name in Database._EXCLUDE_TABLES:
                continue
            if any(table_name.startswith(p) for p in Database._EXCLUDE_PREFIXES):
                continue
            if "embedding" not in cols:
                continue

            id_col = next((c for c in Database._ID_COLUMN_PRIORITY if c in cols), None)
            if id_col is None:
                print(
                    f"[db] discover_table_config: '{table_name}' skipped — "
                    f"no recognised id column {Database._ID_COLUMN_PRIORITY}."
                )
                continue

            content_col = next((c for c in Database._CONTENT_COLUMN_PRIORITY if c in cols), None)
            if content_col is None:
                skip = {id_col, "embedding"}
                candidates = [c for c in cols if c not in skip]
                content_col = candidates[0] if candidates else None
                if content_col:
                    print(
                        f"[db] discover_table_config: '{table_name}' — "
                        f"using fallback content column '{content_col}'."
                    )
            if content_col is None:
                print(
                    f"[db] discover_table_config: '{table_name}' skipped — "
                    f"no recognisable content column."
                )
                continue

            skip_cols = {id_col, content_col, "embedding"}
            display_cols = [c for c in cols if c not in skip_cols]

            full_name = f"public.{table_name}"
            label = custom_labels.get(full_name) or custom_labels.get(
                table_name, Database._humanise_table_name(table_name)
            )

            discovered.append({
                "table_name":       full_name,
                "content_column":   content_col,
                "embedding_column": "embedding",
                "id_column":        id_col,
                "display_columns":  display_cols,
                "label":            label,
            })

        discovered.sort(key=lambda t: t["table_name"])

        # The report is diagnostics; `discovered` is the whole rules-DB retrieval
        # layer. Those must not share a failure mode — and they did: on Windows
        # with stdout redirected to a file (cp1252), the '•' and '→' below raised
        # UnicodeEncodeError from INSIDE this function, so it never returned, the
        # caller's except swallowed it, TABLE_CONFIG stayed empty, and every
        # rules-DB search answered "No relevant chunks found in the rules
        # database" against a database that had the text all along.
        try:
            print(
                f"[db] discover_table_config: {len(discovered)} searchable table(s) found."
            )
            for t in discovered:
                print(
                    f"  - {t['table_name']} "
                    f"(id={t['id_column']}, content={t['content_column']}) "
                    f"-> '{t['label']}'"
                )
        except Exception as exc:  # console encoding, closed stream, anything
            print(f"[db] discover_table_config: {len(discovered)} table(s) found "
                  f"(report suppressed: {type(exc).__name__}).")
        return discovered

    @staticmethod
    def get_connection():
        """Open and return a psycopg2 connection to the financial_llm database."""
        try:
            conn = psycopg2.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                dbname=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
            )
            return conn
        except Exception as e:
            raise RuntimeError(
                f"Failed to connect to PostgreSQL at "
                f"{Config.DB_HOST}:{Config.DB_PORT} "
                f"(db={Config.DB_NAME}, user={Config.DB_USER}). "
                f"Error: {e}"
            ) from e

    @staticmethod
    def get_reports_connection():
        """Open and return a psycopg2 connection to the annual-reports database."""
        if not Config.REPORTS_DB_PASSWORD:
            raise RuntimeError(
                "REPORTS_DB_PASSWORD is not set — reports database is not configured."
            )
        try:
            conn = psycopg2.connect(
                host=Config.REPORTS_DB_HOST,
                port=Config.REPORTS_DB_PORT,
                dbname=Config.REPORTS_DB_NAME,
                user=Config.REPORTS_DB_USER,
                password=Config.REPORTS_DB_PASSWORD,
            )
            return conn
        except Exception as e:
            raise RuntimeError(
                f"Failed to connect to reports PostgreSQL at "
                f"{Config.REPORTS_DB_HOST}:{Config.REPORTS_DB_PORT} "
                f"(db={Config.REPORTS_DB_NAME}, user={Config.REPORTS_DB_USER}). "
                f"Error: {e}"
            ) from e

    @staticmethod
    def search_table(
        conn,
        table_config: dict,
        query_vector: list,
        top_k: int,
        similarity_threshold: float = 0.0,
        metadata_filters: list[dict] | None = None,
    ) -> list[dict]:
        """Run a cosine similarity search against a single table."""
        table_name = table_config["table_name"]
        content_col = table_config["content_column"]
        embed_col = table_config["embedding_column"]
        id_col = table_config["id_column"]
        display_cols = table_config["display_columns"]
        label = table_config["label"]

        display_cols_sql = ", ".join(display_cols) if display_cols else ""
        select_cols = f"{id_col}, {content_col}"
        if display_cols_sql:
            select_cols += f", {display_cols_sql}"
        select_cols += f", 1 - ({embed_col} <=> %s::vector) AS similarity"

        where_clause = f"{embed_col} IS NOT NULL"
        filter_params: list = []

        if metadata_filters:
            clauses = []
            for f in metadata_filters:
                col = f["column"]
                op = f.get("operator", "ILIKE").upper()
                val = f["value"]
                if op == "ILIKE":
                    clauses.append(f"{col}::text ILIKE %s")
                    filter_params.append(f"%{val}%")
                elif op == "=":
                    clauses.append(f"{col} = %s")
                    filter_params.append(val)
                elif op == "ILIKE_EXACT":
                    clauses.append(f"{col}::text ILIKE %s")
                    filter_params.append(val)
            if clauses:
                where_clause += f" AND ({' OR '.join(clauses)})"

        sql = (
            f"SELECT {select_cols} "
            f"FROM {table_name} "
            f"WHERE {where_clause} "
            f"ORDER BY {embed_col} <=> %s::vector "
            f"LIMIT %s"
        )

        vector_str = str(query_vector)
        params = [vector_str] + filter_params + [vector_str, top_k]

        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as e:
            print(f"[db] Error querying {table_name}: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return []

        results = []
        for row in rows:
            row_dict = dict(row)
            similarity = float(row_dict.pop("similarity", 0.0))

            if similarity < similarity_threshold:
                continue

            result = {
                "table_name": table_name,
                "table_label": label,
                "id": row_dict.pop(id_col, None),
                "content": row_dict.pop(content_col, ""),
                "similarity": similarity,
            }
            result.update(row_dict)
            results.append(result)

        return results

    @staticmethod
    def search_all_tables_hybrid(
        conn,
        query_vector: list,
        metadata_hints: dict | None = None,
    ) -> tuple[list[dict], int]:
        """Hybrid search across all content tables (metadata-targeted + semantic)."""
        seen: dict[tuple, dict] = {}
        db_calls = 0

        if metadata_hints:
            for tbl in Config.TABLE_CONFIG:
                tname = tbl["table_name"]
                if tname not in metadata_hints:
                    continue
                filters = metadata_hints[tname]

                wider_k = Config.TOP_K_PER_TABLE * 3
                rows = Database.search_table(
                    conn, tbl, query_vector,
                    top_k=wider_k,
                    similarity_threshold=Config.SIMILARITY_THRESHOLD,
                    metadata_filters=filters if filters else None,
                )
                db_calls += 1

                for r in rows:
                    key = (r["table_name"], str(r["id"]))
                    r["_matched_hint"] = True
                    if key not in seen or r["similarity"] > seen[key]["similarity"]:
                        seen[key] = r

        for tbl in Config.TABLE_CONFIG:
            rows = Database.search_table(
                conn, tbl, query_vector,
                top_k=Config.TOP_K_PER_TABLE,
                similarity_threshold=Config.SIMILARITY_THRESHOLD,
            )
            db_calls += 1

            for r in rows:
                key = (r["table_name"], str(r["id"]))
                if key not in seen or r["similarity"] > seen[key]["similarity"]:
                    r.setdefault("_matched_hint", False)
                    seen[key] = r

        all_results = sorted(seen.values(), key=lambda x: x["similarity"], reverse=True)
        return all_results, db_calls

    @staticmethod
    def search_all_tables(conn, query_vector: list) -> list[dict]:
        """Plain semantic search — no metadata filtering. Used in self-tests."""
        all_results = []
        for tbl in Config.TABLE_CONFIG:
            rows = Database.search_table(
                conn, tbl, query_vector,
                top_k=Config.TOP_K_PER_TABLE,
                similarity_threshold=Config.SIMILARITY_THRESHOLD,
            )
            all_results.extend(rows)
        all_results.sort(key=lambda r: r["similarity"], reverse=True)
        return all_results


# ===== SECTION 3: Embedder =====

class Embedder:
    """bge-m3 embedding endpoint wrapper."""

    @staticmethod
    def embed_text(text: str) -> list[float]:
        """Embed a string using the bge-m3 embedding endpoint."""
        payload = {
            "model": Config.EMBEDDING_MODEL,
            "input": text,
        }

        try:
            response = requests.post(
                Config.EMBEDDING_BASE_URL,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"Embedding request to {Config.EMBEDDING_BASE_URL} failed: {e}"
            ) from e

        body = response.json()

        if "data" in body and isinstance(body["data"], list) and body["data"]:
            return body["data"][0]["embedding"]

        if "embedding" in body:
            return body["embedding"]

        raise RuntimeError(
            f"Unrecognised embedding response shape. "
            f"Expected 'data[0].embedding' (OpenAI-style) or 'embedding' "
            f"(simple style). Actual response body:\n{body}\n\n"
            f"Edit Embedder.embed_text in tool_names.py to match your server."
        )


# ===== SECTION 4: Reranker =====

class Reranker:
    """
    bge-reranker endpoint wrapper.

    Holds the CANONICAL _strip_html implementation for the whole codebase
    (previously duplicated in llm.py, api_server.py, app.py).
    """

    _QUERY_CHAR_LIMIT = 150
    _DOC_CHAR_LIMIT = 400
    _MIN_DOC_CHAR_LIMIT = 100  # floor for the shrink-and-retry loop

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags and collapse whitespace, so truncation counts real content."""
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _looks_like_context_length_error(response: requests.Response) -> bool:
        """True if the server rejected the request for exceeding its token/context limit."""
        if response.status_code != 400:
            return False
        try:
            body = response.json()
            message = str(body.get("error", {}).get("message", "")).lower()
        except (ValueError, AttributeError):
            message = response.text.lower()
        return "context length" in message or "maximum context" in message

    @staticmethod
    def rerank_chunks(query: str, chunks: list[dict]) -> list[dict]:
        """Send the query and chunk contents to the reranker endpoint."""
        if not chunks:
            return []

        url = f"{Config.RERANKER_BASE_URL}/rerank"
        if not url.startswith("http"):
            url = f"http://{url}"

        safe_query = Reranker._strip_html(query)[: Reranker._QUERY_CHAR_LIMIT]

        cleaned_contents = []
        for c in chunks:
            t = c.get("content", "")
            t = Reranker._strip_html(str(t)) if t and str(t).strip() else " "
            cleaned_contents.append(t or " ")

        last_error: Exception | None = None
        doc_char_limit = Reranker._DOC_CHAR_LIMIT

        while True:
            texts = [t[:doc_char_limit] or " " for t in cleaned_contents]
            payload = {
                "model": Config.RERANKER_MODEL,
                "query": safe_query,
                "documents": texts,  # Cohere API format (Xinference/vLLM rerankers)
                "texts": texts,      # some servers expect this key instead
            }

            try:
                response = requests.post(url, json=payload, timeout=30)
                response.raise_for_status()
                break  # success
            except requests.exceptions.HTTPError as e:
                if Reranker._looks_like_context_length_error(e.response) and doc_char_limit > Reranker._MIN_DOC_CHAR_LIMIT:
                    last_error = e
                    doc_char_limit = max(Reranker._MIN_DOC_CHAR_LIMIT, doc_char_limit // 2)
                    print(
                        f"[reranker] Server rejected request as too long; "
                        f"retrying with doc_char_limit={doc_char_limit}"
                    )
                    continue
                err_body = e.response.text if e.response is not None else ""
                raise RuntimeError(
                    f"Reranker request to {url} failed: {e}\nServer response: {err_body}\n\n"
                    f"Please check the endpoint path (/rerank vs /v1/rerank) and expected payload format."
                ) from e
            except requests.exceptions.RequestException as e:
                err_body = ""
                if e.response is not None:
                    err_body = f"\nServer response: {e.response.text}"
                raise RuntimeError(
                    f"Reranker request to {url} failed: {e}{err_body}\n\n"
                    f"Please check the endpoint path (/rerank vs /v1/rerank) and expected payload format."
                ) from e

        body = response.json()

        try:
            results = body.get("results", [])
            if not results:
                raise ValueError("Empty 'results' array in response.")

            for res in results:
                idx = res["index"]
                score = float(res.get("relevance_score", 0.0))
                chunks[idx]["rerank_score"] = score

        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise RuntimeError(
                f"Unrecognised reranker response shape. "
                f"Expected 'results' array with 'index' and 'relevance_score'. "
                f"Actual response body:\n{body}\n\n"
                f"Edit Reranker.rerank_chunks in tool_names.py to match your server."
            ) from e

        chunks.sort(key=lambda x: x.get("rerank_score", -999.0), reverse=True)
        return chunks


# ===== SECTION 5: CoreTools =====

class CoreTools:
    """
    Tool executors for the "rules DB" (Ind AS / SA / CARO / EAC / Schedule III).

    Migrated from tools.py. The dead TOOL_DEFINITIONS (OpenAI function-schema
    list) and execute_tool dispatcher have been dropped — verified unused
    outside tools.py (grep -rn "TOOL_DEFINITIONS\\|execute_tool" *.py found no
    external callers; yukta_tools.py / ToolRegistry call the private
    executors directly).
    """

    @staticmethod
    def get_eac_opinion_by_topic(topic: str, conn) -> str:
        if not topic.strip():
            return "Please provide a topic to search EAC opinions."

        results = []

        try:
            sql = (
                "SELECT id, doc_name, query_no, query_subject, opinion_period, facts "
                "FROM public.eac_general "
                "WHERE query_subject ILIKE %s "
                "ORDER BY id "
                "LIMIT 5"
            )
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (f"%{topic}%",))
                for row in cur.fetchall():
                    results.append({"_source": "eac_general", **dict(row)})
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

        if not results:
            try:
                sql = (
                    "SELECT id, doc_name, query_no, query, opinion "
                    "FROM public.eac_query "
                    "WHERE query ILIKE %s "
                    "LIMIT 5"
                )
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, (f"%{topic}%",))
                    for row in cur.fetchall():
                        results.append({"_source": "eac_query", **dict(row)})
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        if not results:
            return f"No EAC opinions found for topic: '{topic}'."

        lines = [f"Found {len(results)} EAC opinion(s) matching topic '{topic}':\n"]
        for r in results:
            lines.append(
                f"--- {r.get('doc_name', '?')} | Query #{r.get('query_no', '?')} "
                f"| Period: {r.get('opinion_period', 'N/A')} ---"
            )
            if r.get("query_subject"):
                lines.append(f"Subject: {r['query_subject']}")
            content = r.get("facts") or r.get("opinion") or r.get("query") or ""
            lines.append(f"Content: {str(content)[:600]}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _fetch_chunks_by_docname(doc_name: str, conn, limit: int = 5) -> list[dict]:
        results = []
        for tbl in Config.TABLE_CONFIG:
            if "doc_name" not in tbl.get("display_columns", []):
                continue
            tname        = tbl["table_name"]
            content_col  = tbl["content_column"]
            id_col       = tbl["id_column"]
            try:
                sql = (
                    f"SELECT {id_col} AS id, {content_col} AS content, doc_name "
                    f"FROM {tname} "
                    f"WHERE doc_name ILIKE %s "
                    f"LIMIT %s"
                )
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, (f"%{doc_name}%", limit))
                    for row in cur.fetchall():
                        results.append({
                            "table_label": tbl["label"],
                            "doc_name": row.get("doc_name"),
                            "content": row.get("content", ""),
                        })
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if len(results) >= limit:
                break
        return results[:limit]

    @staticmethod
    def compare_standards(std1: str, std2: str, conn) -> str:
        if not std1 or not std2:
            return "Please provide two standard names to compare."

        chunks1 = CoreTools._fetch_chunks_by_docname(std1, conn, limit=5)
        chunks2 = CoreTools._fetch_chunks_by_docname(std2, conn, limit=5)

        lines = [f"=== {std1} ==="]
        if chunks1:
            for c in chunks1:
                content = re.sub(r"<[^>]+>", " ", str(c["content"])).strip()
                lines.append(f"[{c['table_label']}] doc_name={c.get('doc_name', '?')}")
                lines.append(content[:500])
                lines.append("")
        else:
            lines.append(f"No chunks found for '{std1}' in the knowledge base.")

        lines.append(f"\n=== {std2} ===")
        if chunks2:
            for c in chunks2:
                content = re.sub(r"<[^>]+>", " ", str(c["content"])).strip()
                lines.append(f"[{c['table_label']}] doc_name={c.get('doc_name', '?')}")
                lines.append(content[:500])
                lines.append("")
        else:
            lines.append(f"No chunks found for '{std2}' in the knowledge base.")

        return "\n".join(lines)

    @staticmethod
    def check_amendment_status(document: str, clause: str | None, conn) -> str:
        if not document.strip():
            return "Please provide a document name to check."

        amendment_sources = [
            {
                "table": "public.cag_caro_chunks",
                "content_col": "txt",
                "meta_cols": ["doc_name", "notification_no", "status", "effective_from", "effective_to", "is_amended"],
            },
            {
                "table": "public.cag_directions_chunks",
                "content_col": "txt",
                "meta_cols": ["doc_name", "effective_from", "effective_to", "is_amended"],
            },
            {
                "table": "public.ind_as_appendix",
                "content_col": "text",
                "meta_cols": ["doc_name", "standard_number", "status", "year"],
            },
            {
                "table": "public.sa_700_chunks",
                "content_col": "chunk",
                "meta_cols": ["doc_name", "version", "external_ref"],
            },
        ]

        all_rows = []
        for src in amendment_sources:
            tname       = src["table"]
            content_col = src["content_col"]
            meta_cols   = src["meta_cols"]
            try:
                params = [f"%{document}%"]
                sql = (
                    f"SELECT {', '.join(meta_cols)}, {content_col} "
                    f"FROM {tname} "
                    f"WHERE doc_name ILIKE %s"
                )
                if clause:
                    sql += f" AND {content_col} ILIKE %s"
                    params.append(f"%{clause}%")
                sql += " LIMIT 5"

                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    for row in cur.fetchall():
                        all_rows.append({"_table": tname, **dict(row)})
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        if not all_rows:
            clause_info = f" | clause: '{clause}'" if clause else ""
            return f"No amendment information found for '{document}'{clause_info}."

        lines = [
            f"Amendment / effective-date status for '{document}'"
            + (f" | clause: '{clause}'" if clause else "")
            + "\n"
        ]
        for r in all_rows:
            lines.append(f"[{r['_table']}]  doc_name={r.get('doc_name', '?')}")
            for field in ["status", "is_amended", "effective_from", "effective_to",
                          "notification_no", "version", "year", "external_ref"]:
                val = r.get(field)
                if val is not None and val != "":
                    lines.append(f"  {field}: {val}")
            if clause:
                content_snippet = ""
                for col in ["txt", "text", "chunk"]:
                    if col in r and r[col]:
                        content_snippet = str(r[col])[:400]
                        break
                if content_snippet:
                    lines.append(f"  Content snippet: {content_snippet}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def validate_answer(
        chunk_indices: list[int],
        claims: list[str],
        initial_chunks: list[dict],
    ) -> str:
        if not initial_chunks:
            return "No retrieved chunks available to validate against."
        if not chunk_indices:
            return "Please specify which chunk indices (1-indexed) to validate."

        lines = [
            f"Validation of {len(claims)} claim(s) against retrieved chunk(s) "
            f"{chunk_indices}:\n"
        ]

        for i, idx in enumerate(chunk_indices):
            claim = claims[i] if i < len(claims) else "(no claim provided)"
            if idx < 1 or idx > len(initial_chunks):
                lines.append(
                    f"Claim {i+1}: [Chunk {idx}] — INVALID index "
                    f"(only {len(initial_chunks)} chunks available)"
                )
                continue

            chunk   = initial_chunks[idx - 1]
            content = str(chunk.get("content", ""))
            content = re.sub(r"<[^>]+>", " ", content).strip()
            label   = chunk.get("table_label", chunk.get("table_name", "Unknown"))
            sim     = chunk.get("similarity", 0.0)

            lines.append(f"--- Chunk {idx} | {label} | similarity: {sim:.3f} ---")
            lines.append(f"Claim to verify: \"{claim}\"")
            lines.append(f"Full chunk content (first 800 chars):")
            lines.append(content[:800])
            lines.append(
                "→ Check whether the claim above is supported by this content.\n"
            )

        return "\n".join(lines)

    @staticmethod
    def cross_reference_lookup(
        standard: str,
        paragraph: str | None,
        topic: str,
        conn,
    ) -> str:
        if not standard.strip():
            return "Please provide a standard name for the cross-reference lookup."

        search_parts = [standard, topic]
        if paragraph:
            search_parts.append(f"paragraph {paragraph}")
        search_query = " ".join(search_parts)

        try:
            vector = Embedder.embed_text(search_query)
        except Exception as e:
            return f"Cross-reference lookup failed (embedding error): {e}"

        std_lower = standard.lower()
        if "ind as" in std_lower or "ias" in std_lower:
            target_tables = {"public.ind_as_chunks", "public.ind_as_appendix"}
        elif std_lower.startswith("sa ") or "standard on auditing" in std_lower:
            target_tables = {"public.sa_700_chunks"}
        elif "caro" in std_lower or "cag" in std_lower:
            target_tables = {
                "public.cag_caro_chunks", "public.cag_caro_tables",
                "public.cag_directions_chunks",
            }
        elif "eac" in std_lower:
            target_tables = {"public.eac_general", "public.eac_query", "public.eac_table"}
        elif "schedule iii" in std_lower or "schedule 3" in std_lower:
            target_tables = {
                "public.schedule_iii_chunks", "public.schedule_iii_table_chunks",
            }
        else:
            target_tables = {t["table_name"] for t in Config.TABLE_CONFIG}

        doc_filter = [{"column": "doc_name", "operator": "ILIKE", "value": standard}]

        all_results = []
        for tbl in Config.TABLE_CONFIG:
            if tbl["table_name"] not in target_tables:
                continue
            rows = Database.search_table(
                conn, tbl, vector,
                top_k=3,
                similarity_threshold=0.0,
                metadata_filters=doc_filter,
            )
            all_results.extend(rows)

        if not all_results:
            for tbl in Config.TABLE_CONFIG:
                if tbl["table_name"] not in target_tables:
                    continue
                rows = Database.search_table(conn, tbl, vector, top_k=3, similarity_threshold=0.0)
                all_results.extend(rows)

        if not all_results:
            return (
                f"No content found for cross-reference: {standard}"
                + (f" ¶{paragraph}" if paragraph else "")
                + f" on topic '{topic}'."
            )

        seen: dict[tuple, dict] = {}
        for r in all_results:
            key = (r["table_name"], str(r["id"]))
            if key not in seen or r["similarity"] > seen[key]["similarity"]:
                seen[key] = r
        results = sorted(seen.values(), key=lambda x: x["similarity"], reverse=True)[:5]

        header = (
            f"Cross-reference lookup: {standard}"
            + (f" ¶{paragraph}" if paragraph else "")
            + f" — {topic}\n"
        )
        lines = [header]
        for i, chunk in enumerate(results, 1):
            label   = chunk.get("table_label", chunk.get("table_name"))
            content = re.sub(r"<[^>]+>", " ", str(chunk.get("content", ""))).strip()
            sim     = chunk.get("similarity", 0.0)
            lines.append(f"[{i}] {label} | similarity: {sim:.3f}")
            lines.append(content[:500])
            lines.append("")

        return "\n".join(lines)


# ===== SECTION 5B: SourceRef =====

class SourceRef:
    """
    One citation format for every reports-DB tool.

    Answers used to cite raw database keys ("[Chunk ONGC_2024_2025_P0262_008]"),
    which a reader cannot act on. An auditor needs the report, the page and the
    section. Four different bracket syntaxes had grown up across the tools and
    four of them emitted no page at all, so the citation a user got depended on
    which tool happened to answer.

    Why page + section and not a paragraph number: annual reports have no
    paragraph numbering in this data. `note_refs` looked like a candidate but is
    only ~2% populated and records OUTBOUND references ("see Note 45") rather
    than the chunk's own identity. `page_pdf_start` is unusable — its offset from
    page_ocr_start swings from -264 to +301 within a single document. What IS
    reliable is page_ocr_start (100%) and section (99%), and section is usually
    the note number itself ("3.1. Investments in subsidiaries"), which is what a
    reader actually navigates by.

    Ind AS citations are unaffected — the rules DB has a real `paragraph_no` and
    keeps its own `[Ind AS 36 | IMPAIRMENT | para 12]` shape.

    IMPORTANT: the `[chunk_id:` and `[table_id:` prefixes are load-bearing.
    RatioExtractionEngine.parse_table_md and TieOutTools split blocks on
    `startswith("[table_id:")`, and Orchestrator._strip_tool_artifacts matches
    `\\[chunk_id:[^\\]]*\\]`. Fields may be ADDED inside the brackets; the prefix
    must not change.
    """

    _MAX_SECTION_CHARS = 70

    @staticmethod
    def _clean(value, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:limit] + "…" if len(text) > limit else text

    @staticmethod
    def _page(value) -> str:
        return str(value) if value not in (None, "") else "?"

    @staticmethod
    def chunk(chunk_id, page=None, section=None, extra: str = "") -> str:
        """Anchor for a narrative text chunk."""
        parts = [f"chunk_id: {chunk_id}", f"page: {SourceRef._page(page)}"]
        section_text = SourceRef._clean(section, SourceRef._MAX_SECTION_CHARS)
        if section_text:
            parts.append(f"section: {section_text}")
        if extra:
            parts.append(extra)
        return "[" + " | ".join(parts) + "]"

    @staticmethod
    def table(table_id, page=None, title=None) -> str:
        """Anchor for a table chunk. Title doubles as the section for tables."""
        parts = [f"table_id: {table_id}", f"page: {SourceRef._page(page)}"]
        title_text = SourceRef._clean(title, SourceRef._MAX_SECTION_CHARS)
        if title_text:
            parts.append(f"section: {title_text}")
        return "[" + " | ".join(parts) + "]"

    @staticmethod
    def instruction() -> str:
        """The single citation instruction every reports-DB tool emits."""
        return (
            'CITE every sentence you use as (Source: <doc_name>, page <N>, section: "<section>") '
            "— take the page and section from the bracket immediately preceding the text you "
            "used, and the doc_name from the Document line above. NEVER cite a bare chunk_id or "
            "table_id; those are database keys and mean nothing to a reader."
        )


# ===== SECTION 6: DocumentResolver =====

class DocumentResolver:
    """
    Tool executor for the annual-reports database ("reports DB") — resolves
    company + fiscal year to a `documents` row, and locates the "Statement
    of Compliance" / "Basis of Preparation" passage.

    Migrated from reports_tools.py. This is the canonical company+FY
    document resolver used by every other reports-DB tool class below.

    Dedup addition (not in the original reports_tools.py): latest_fy_end()
    and format_ambiguous() are shared helpers, moved here from
    trend_analysis_tools.py's local _get_latest_fy_end / _format_ambiguous
    so TrendAnalysisTools no longer keeps its own copies. Behaviour is
    byte-for-byte identical to the originals — only the call site moved.
    """

    @staticmethod
    def _parse_fy(financial_year: str) -> tuple[int | None, int | None]:
        """
        Extract one or two 4-digit fiscal years from a free-text string.

        Examples:
          "FY2022-23"   -> (2022, 2023)
          "2022-2023"   -> (2022, 2023)
          "FY23"        -> (None, 2023)  -- single 2-digit year, ambiguous end-only
          "2023"        -> (2023, 2023)  -- single 4-digit year, tried as both
        """
        if not financial_year:
            return None, None

        years_4digit = re.findall(r"(?<!\d)(20\d{2})(?!\d)", financial_year)
        if len(years_4digit) >= 2:
            return int(years_4digit[0]), int(years_4digit[1])
        if len(years_4digit) == 1:
            y = int(years_4digit[0])
            m = re.search(r"20\d{2}\s*[-/]\s*(\d{2})(?!\d)", financial_year)
            if m:
                return y, (y // 100) * 100 + int(m.group(1))
            return y, y

        years_2digit = re.findall(r"(?<!\d)(\d{2})(?!\d)", financial_year)
        if len(years_2digit) == 1:
            y = 2000 + int(years_2digit[0])
            return None, y

        return None, None

    @staticmethod
    def _resolve_document(company: str, financial_year: str, conn) -> dict | list[dict] | None:
        """
        Find the `documents` row matching company + financial_year.

        Returns:
          dict   — a single unambiguous match
          list   — multiple candidate matches (ambiguous company name)
          None   — no match found
        """
        if not company or not company.strip():
            return None

        fy_start, fy_end = DocumentResolver._parse_fy(financial_year or "")
        company_pattern = f"%{company.strip()}%"

        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                sql = (
                    "SELECT doc_id, company, fy_start, fy_end, doc_name "
                    "FROM public.documents "
                    "WHERE lower(replace(company, '_', ' ')) ILIKE lower(replace(%s, '_', ' ')) "
                    "ORDER BY company, fy_start"
                )
                cur.execute(sql, (company_pattern,))
                rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None

        if not rows:
            return None

        if fy_start is not None and fy_end is not None and fy_start != fy_end:
            exact = [r for r in rows if r["fy_start"] == fy_start and r["fy_end"] == fy_end]
            if exact:
                rows = exact
        elif fy_end is not None:
            exact = [r for r in rows if r["fy_start"] == fy_end or r["fy_end"] == fy_end]
            if exact:
                rows = exact

        if len(rows) == 1:
            return rows[0]

        companies = {r["company"] for r in rows}
        if len(companies) > 1 or len(rows) > 1:
            return rows

        return rows[0]

    @staticmethod
    def latest_fy_end(company: str, conn) -> int | None:
        """Latest fy_end on file for this company — seeds the default 3-year window."""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(fy_end) FROM public.documents "
                    "WHERE lower(replace(company, '_', ' ')) ILIKE lower(replace(%s, '_', ' '))",
                    (f"%{company.strip()}%",),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] is not None else None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None

    @staticmethod
    def format_ambiguous(company: str, label: str, matches: list[dict], ask: str = "the exact company name") -> str:
        """Format a "multiple documents matched" message. `ask` customises the closing instruction."""
        options = "; ".join(
            f"{r['company']} FY{r['fy_start']}-{str(r['fy_end'])[-2:]} ({r['doc_id']})" for r in matches[:10]
        )
        return (
            f"Multiple annual reports matched '{company}' / '{label}': {options}. "
            f"Please specify {ask}."
        )

    @staticmethod
    def _fetch_following_chunks(doc_id: str, anchor: dict, conn, limit: int = 4) -> list[dict]:
        """Fetch up to `limit` narrative chunks immediately after the anchor heading."""
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                sql = (
                    "SELECT chunk_id, page_ocr_start, chunk_type, content, section "
                    "FROM public.text_chunks "
                    "WHERE doc_id = %s "
                    "  AND chunk_type IN ('text', 'list') "
                    "  AND (page_ocr_start, chunk_id) > (%s, %s) "
                    "ORDER BY page_ocr_start, chunk_id "
                    "LIMIT %s"
                )
                cur.execute(sql, (doc_id, anchor["page_ocr_start"], anchor["chunk_id"], limit))
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return []

    @staticmethod
    def _format_anchored_passage(doc_header: str, anchor: dict, following: list[dict], cite_instruction: str) -> str:
        """
        Format a heading-anchor + following-chunks passage for the LLM —
        shared by DisclosureSearchTools.search_company_disclosures and
        AccountingPolicyTools.get_accounting_policy_note, which both anchor
        on a matching heading and pull the narrative chunks that follow it.
        """
        passage_chunks = [anchor] + following
        breadcrumb = anchor.get("section_breadcrumb") or []
        # Lead with the anchor's own matched heading text (always accurate —
        # it's literally what was matched) rather than section_breadcrumb,
        # which can lag/misalign with the actual heading on some documents;
        # breadcrumb is still shown as supplementary context when present.
        section_line = anchor.get("content") or "(unknown)"
        if breadcrumb:
            section_line += f" [breadcrumb: {' > '.join(breadcrumb)}]"
        lines = [
            doc_header,
            f"[Section: {section_line}]",
            f"[Page: {anchor.get('page_ocr_start', '?')}]",
            cite_instruction,
            "",
        ]
        for c in passage_chunks:
            if c.get("content"):
                lines.append(
                    SourceRef.chunk(c["chunk_id"], c.get("page_ocr_start"), c.get("section"))
                    + f" {c['content']}"
                )
        return "\n".join(lines)

    @staticmethod
    def _find_compliance_passage(doc_id: str, conn) -> str:
        """
        Locate the Statement of Compliance / Basis of Preparation passage under
        Notes to the Standalone Financial Statements.
        """
        anchor = None

        queries = [
            (
                "SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section "
                "FROM public.text_chunks "
                "WHERE doc_id = %s AND chunk_type = 'heading' "
                "  AND content ILIKE %s "
                "  AND section_breadcrumb::text ILIKE %s "
                "  AND section_breadcrumb::text NOT ILIKE %s "
                "ORDER BY page_ocr_start LIMIT 1",
                (doc_id, "%statement of compliance%", "%standalone%", "%consolidated%"),
            ),
            (
                "SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section "
                "FROM public.text_chunks "
                "WHERE doc_id = %s AND chunk_type = 'heading' "
                "  AND content ILIKE %s "
                "  AND section_breadcrumb::text ILIKE %s "
                "  AND section_breadcrumb::text NOT ILIKE %s "
                "ORDER BY page_ocr_start LIMIT 1",
                (doc_id, "%basis of preparation%", "%standalone%", "%consolidated%"),
            ),
            (
                "SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section "
                "FROM public.text_chunks "
                "WHERE doc_id = %s "
                "  AND section_breadcrumb::text ILIKE %s "
                "  AND section_breadcrumb::text NOT ILIKE %s "
                "  AND (content ILIKE %s OR content ILIKE %s OR content ILIKE %s) "
                "ORDER BY page_ocr_start LIMIT 1",
                (
                    doc_id, "%standalone%", "%consolidated%",
                    "%accordance with%accounting standard%", "%indian gaap%", "%ifrs%",
                ),
            ),
        ]

        for sql, params in queries:
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                    if row:
                        anchor = dict(row)
                        break
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        if not anchor:
            return (
                "No Statement of Compliance / Basis of Preparation passage was found "
                "under the Notes to the Standalone Financial Statements for this document."
            )

        following = DocumentResolver._fetch_following_chunks(doc_id, anchor, conn, limit=4)
        passage_chunks = [anchor] + following

        breadcrumb = anchor.get("section_breadcrumb") or []
        lines = [
            f"[Section: {' > '.join(breadcrumb) if breadcrumb else '(unknown)'}]",
            f"[Page: {anchor.get('page_ocr_start', '?')}]",
            f"[Chunk IDs in this passage: {', '.join(c['chunk_id'] for c in passage_chunks)}]",
            SourceRef.instruction(),
            "",
        ]
        for c in passage_chunks:
            if c.get("content"):
                lines.append(
                    SourceRef.chunk(c["chunk_id"], c.get("page_ocr_start"), c.get("section"))
                    + f" {c['content']}"
                )

        return "\n".join(str(l) for l in lines if l)

    @staticmethod
    def get_framework_and_doc(company: str, financial_year: str, conn) -> tuple[dict | None, str]:
        """
        Resolve company+financial_year and fetch the framework passage in one call.

        Returns (doc_row, framework_passage_text) on success.
        (None, error_message) if the reports DB isn't configured, no document
        matched, or the company/year match was ambiguous.
        """
        if conn is None:
            return None, "[tool error] Reports database is not configured."

        match = DocumentResolver._resolve_document(company, financial_year, conn)

        if match is None:
            return None, (
                f"No annual report found for company '{company}' "
                f"(financial year '{financial_year}')."
            )

        if isinstance(match, list):
            return None, DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        passage = DocumentResolver._find_compliance_passage(match["doc_id"], conn)
        return match, passage

    @staticmethod
    def _get_reporting_framework(company: str, financial_year: str, conn) -> str:
        doc, passage = DocumentResolver.get_framework_and_doc(company, financial_year, conn)
        if doc is None:
            return passage  # error/ambiguity message

        return (
            f"Document: {doc['doc_name']} (doc_id={doc['doc_id']}, "
            f"company={doc['company']}, FY{doc['fy_start']}-{str(doc['fy_end'])[-2:]})\n\n"
            f"{passage}"
        )


# ===== SECTION 6B: DisclosureSearchTools =====

class DisclosureSearchTools:
    """
    Tool executor for free-text semantic search over a specific company's
    annual report narrative (accounting policy notes, MD&A, disclosures).

    Gap this fills: the reports DB's `text_chunks` table has its own
    `embedding` column, but Config.TABLE_CONFIG (Stage B's rules-DB
    retrieval in agent.py) is only ever populated from the rules DB
    connection — it never indexes text_chunks. Before this class, the
    reports DB (conn_reports) was reachable only through the structured,
    pattern-matched tools below (ratio/audit/compliance/trend), which
    look for specific statement tables or note-title patterns. A query
    like "What is <company>'s revenue recognition policy?" had no
    retrieval path at all and correctly-but-unhelpfully came back "not
    found" — this tool adds a general-purpose semantic-search path for
    exactly that class of question.

    New addition — not present in the original tools.py / reports_tools.py.
    """

    _RESULT_LIMIT = 8
    # Calibrated against BPCL_2023_2024: the correct "1.12. Revenue
    # Recognition" heading scored 0.68 (standalone) / 0.66 (consolidated)
    # against the query "revenue recognition policy", with the next-best
    # unrelated heading at 0.53 — 0.55 sits cleanly in that gap.
    _HEADING_ANCHOR_THRESHOLD = 0.55

    @staticmethod
    def _find_heading_anchor(doc_id: str, vector_str: str, conn_reports) -> dict | None:
        """
        Best-matching heading chunk for the query topic, preferring standalone
        over consolidated sections when both exist (same convention as
        DocumentResolver._find_compliance_passage). Note: text_chunks'
        `title_embedding` column exists but is unpopulated in this DB — this
        deliberately searches heading rows' `embedding` (content embedding of
        the heading text itself), which is populated and works well for
        short heading strings like "1.12. Revenue Recognition".
        """
        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section, "
                    "1 - (embedding <=> %s::vector) AS similarity "
                    "FROM public.text_chunks "
                    "WHERE doc_id = %s AND chunk_type = 'heading' AND embedding IS NOT NULL "
                    "ORDER BY "
                    "  (section_breadcrumb::text ILIKE '%%consolidated%%') ASC, "
                    "  embedding <=> %s::vector "
                    "LIMIT 1",
                    (vector_str, doc_id, vector_str),
                )
                row = cur.fetchone()
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            return None

        if row and row["similarity"] is not None and row["similarity"] >= DisclosureSearchTools._HEADING_ANCHOR_THRESHOLD:
            return dict(row)
        return None

    @staticmethod
    def search_company_disclosures(
        company: str, financial_year: str, topic: str, conn_reports
    ) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)

        if match is None:
            return (
                f"No annual report found for company '{company}' "
                f"(financial year '{financial_year}')."
            )
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        try:
            query_vector = Embedder.embed_text(topic)
        except Exception as e:
            return f"Semantic search failed (embedding error): {e}"

        vector_str = str(query_vector)
        doc_id = match["doc_id"]
        doc_header = (
            f"Document: {match['doc_name']} (doc_id={doc_id}, "
            f"company={match['company']}, FY{match['fy_start']}-{str(match['fy_end'])[-2:]})"
        )
        cite_instruction = (
            SourceRef.instruction()
        )

        # Phase 1: anchor on a matching heading and pull the narrative chunks
        # that follow it — a coherent policy-note passage, same approach as
        # DocumentResolver._find_compliance_passage. Preferred when a
        # confident heading match exists.
        anchor = DisclosureSearchTools._find_heading_anchor(doc_id, vector_str, conn_reports)
        if anchor:
            following = DocumentResolver._fetch_following_chunks(doc_id, anchor, conn_reports, limit=4)
            return DocumentResolver._format_anchored_passage(doc_header, anchor, following, cite_instruction)

        # Phase 2: fallback — no confident heading match, so fall back to
        # plain content-chunk semantic search across the whole document.
        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT chunk_id, page_ocr_start, section, title, chunk_type, content, "
                    "1 - (embedding <=> %s::vector) AS similarity "
                    "FROM public.text_chunks "
                    "WHERE doc_id = %s AND embedding IS NOT NULL AND chunk_type IN ('text', 'list') "
                    "ORDER BY embedding <=> %s::vector "
                    "LIMIT %s",
                    (vector_str, doc_id, vector_str, DisclosureSearchTools._RESULT_LIMIT),
                )
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            return f"Semantic search failed (database error): {e}"

        if not rows:
            return (
                f"No narrative disclosure chunks matching '{topic}' were found in "
                f"{match['doc_name']} (doc_id={doc_id})."
            )

        lines = [doc_header, cite_instruction, ""]
        for r in rows:
            section = r.get("section") or r.get("title") or "(unknown section)"
            similarity = r.get("similarity")
            sim_str = f"{similarity:.3f}" if similarity is not None else "?"
            lines.append(
                SourceRef.chunk(
                    r["chunk_id"], r.get("page_ocr_start"),
                    r.get("section") or r.get("title"), extra=f"similarity: {sim_str}"
                ) + f" {r['content']}"
            )

        return "\n".join(lines)


# ===== SECTION 6C: AccountingPolicyReference =====

class AccountingPolicyReference:
    """
    Static reference data for AccountingPolicyTools.get_accounting_policy_note
    — canonical accounting-policy topics and the heading-text phrases used to
    locate each one deterministically via ILIKE, before falling back to
    DisclosureSearchTools' embedding-based heading search.

    New addition — not present in the original codebase.
    """

    # Canonical topic key -> heading-text phrases to ILIKE-match, in priority
    # order. Each list's first entry is the canonical display phrase itself.
    TOPIC_KEYWORDS: dict[str, list[str]] = {
        "revenue_recognition": ["revenue recognition", "revenue from operations", "revenue from contracts"],
        "depreciation": ["depreciation", "depreciation and amortisation", "depreciation and amortization"],
        "inventory_valuation": ["inventory valuation", "valuation of inventories", "inventories"],
        "employee_benefits": ["employee benefits", "post-employment benefits", "post employment benefits"],
        "foreign_currency": ["foreign currency", "foreign currency transactions", "foreign currency translation"],
        "taxation": ["taxation", "income tax", "income taxes", "current and deferred tax", "taxes on income"],
        "financial_instruments": ["financial instruments", "financial assets and financial liabilities", "financial assets", "financial liabilities"],
        "impairment": ["impairment", "impairment of assets", "impairment of non-financial assets"],
        "leases": ["leases", "lease accounting", "right-of-use assets"],
        "borrowing_costs": ["borrowing costs", "cost of borrowing"],
        "provisions": ["provisions", "contingent liabilities", "contingent assets"],
    }

    CANONICAL_TOPICS: list[str] = [key.replace("_", " ") for key in TOPIC_KEYWORDS]

    @staticmethod
    def normalize_topic(raw: str) -> str | None:
        """
        Map free-text input (e.g. "revenue recognition policy", "how they
        value inventory") to a canonical topic key, or None if no canonical
        topic matches.
        """
        if not raw:
            return None
        text = raw.strip().lower()
        for key, phrases in AccountingPolicyReference.TOPIC_KEYWORDS.items():
            for phrase in phrases:
                if phrase in text:
                    return key
        return None

    @staticmethod
    def display_name(key: str) -> str:
        return key.replace("_", " ")


# ===== SECTION 6D: AccountingPolicyTools =====

class AccountingPolicyTools:
    """
    Tool executor for get_accounting_policy_note — deterministic lookup of a
    named company's own filed Significant/Material Accounting Policies note
    text for one of AccountingPolicyReference's 11 canonical topics.

    More precise than DisclosureSearchTools.search_company_disclosures for
    these 11 topics: matches heading text directly via ILIKE against known
    phrasing, falling back to DisclosureSearchTools' embedding-based heading
    search only if no direct match is found. Use search_company_disclosures
    instead for narrative topics outside this fixed list (related party,
    contingent narrative beyond the Provisions note, MD&A, risk commentary).

    New addition — not present in the original codebase.
    """

    # Accounting-policy sub-notes are consistently numbered "N.M. <Topic>"
    # (e.g. "1.12. Revenue Recognition", "1.7. Borrowing Costs") — distinct
    # from disclosure/schedule notes, which are titled "NOTE ## <TITLE>"
    # (e.g. "NOTE 27 PROVISIONS", a numbers table, not the policy text).
    # Restricting to this pattern is what actually separates "the policy
    # note" from "a same-named disclosure note" — matched empirically
    # against BPCL_2023_2024's full heading list.
    _POLICY_SUBHEADING_PATTERN = r"^[0-9]+\.[0-9]+\.?\s"

    @staticmethod
    def _find_heading_by_keywords(doc_id: str, phrases: list[str], conn_reports) -> dict | None:
        """
        Deterministic ILIKE match on heading content, preferring standalone
        over consolidated. Restricted to headings whose breadcrumb is under a
        "Notes to ... Financial Statements" section AND whose own text follows
        the "N.M. Topic" policy-subheading numbering — without both filters,
        a phrase like "provisions" also matches "NOTE 27 PROVISIONS" (a
        disclosure/movement-schedule note, not the policy text) or "revenue
        from operations" also matches an MD&A/financial-highlights heading.
        """
        clauses = " OR ".join(["content ILIKE %s"] * len(phrases))
        params = [f"%{p}%" for p in phrases]
        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section "
                    f"FROM public.text_chunks "
                    f"WHERE doc_id = %s AND chunk_type = 'heading' AND ({clauses}) "
                    f"  AND section_breadcrumb::text ILIKE '%%notes to%%' "
                    f"  AND content ~ %s "
                    f"ORDER BY (section_breadcrumb::text ILIKE '%%consolidated%%') ASC, page_ocr_start "
                    f"LIMIT 1",
                    [doc_id] + params + [AccountingPolicyTools._POLICY_SUBHEADING_PATTERN],
                )
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            return None

    @staticmethod
    def _find_heading_by_embedding(doc_id: str, query_text: str, conn_reports) -> dict | None:
        """
        Embedding-based fallback, same threshold/standalone-preference
        convention as DisclosureSearchTools._find_heading_anchor, but with
        its own query restricted to numbered policy sub-headings under
        "Notes to ... Financial Statements" — kept separate from (not
        reusing) that shared method, since search_company_disclosures
        deliberately needs to match unrestricted heading types (MD&A,
        related-party, etc.) that this stricter filter would wrongly exclude.
        """
        try:
            query_vector = Embedder.embed_text(query_text)
        except Exception:
            return None
        vector_str = str(query_vector)
        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section, "
                    "1 - (embedding <=> %s::vector) AS similarity "
                    "FROM public.text_chunks "
                    "WHERE doc_id = %s AND chunk_type = 'heading' AND embedding IS NOT NULL "
                    "  AND section_breadcrumb::text ILIKE '%%notes to%%' "
                    "  AND content ~ %s "
                    "ORDER BY "
                    "  (section_breadcrumb::text ILIKE '%%consolidated%%') ASC, "
                    "  embedding <=> %s::vector "
                    "LIMIT 1",
                    (vector_str, doc_id, AccountingPolicyTools._POLICY_SUBHEADING_PATTERN, vector_str),
                )
                row = cur.fetchone()
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            return None

        if row and row["similarity"] is not None and row["similarity"] >= DisclosureSearchTools._HEADING_ANCHOR_THRESHOLD:
            return dict(row)
        return None

    @staticmethod
    def get_accounting_policy_note(company: str, financial_year: str, topic: str, conn_reports) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        canonical = AccountingPolicyReference.normalize_topic(topic)
        if canonical is None:
            return (
                f"'{topic}' is not one of the supported accounting policy topics. "
                f"Supported topics: {', '.join(AccountingPolicyReference.CANONICAL_TOPICS)}. "
                f"For other narrative content (e.g. related party transactions, MD&A, risk "
                f"factors), use search_company_disclosures instead."
            )

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return (
                f"No annual report found for company '{company}' "
                f"(financial year '{financial_year}')."
            )
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        doc_id = match["doc_id"]
        doc_header = (
            f"Document: {match['doc_name']} (doc_id={doc_id}, "
            f"company={match['company']}, FY{match['fy_start']}-{str(match['fy_end'])[-2:]})"
        )
        cite_instruction = (
            SourceRef.instruction()
        )
        topic_display = AccountingPolicyReference.display_name(canonical)

        # Phase 1: deterministic ILIKE match on heading text.
        phrases = AccountingPolicyReference.TOPIC_KEYWORDS[canonical]
        anchor = AccountingPolicyTools._find_heading_by_keywords(doc_id, phrases, conn_reports)

        # Phase 2: embedding-based fallback, still restricted to numbered
        # policy sub-headings (see _find_heading_by_embedding docstring).
        if anchor is None:
            anchor = AccountingPolicyTools._find_heading_by_embedding(
                doc_id, f"{topic_display} policy", conn_reports
            )

        if anchor is None:
            return (
                f"No '{topic_display}' accounting policy note was found in {match['doc_name']} "
                f"(doc_id={doc_id}) — this company may bundle it under a general Significant "
                f"Accounting Policies note without a dedicated subheading. Try "
                f"search_company_disclosures instead."
            )

        following = DocumentResolver._fetch_following_chunks(doc_id, anchor, conn_reports, limit=4)
        return DocumentResolver._format_anchored_passage(doc_header, anchor, following, cite_instruction)


# ===== SECTION 6E: ReportReferenceTools =====

class ReportReferenceTools:
    """
    Tool executor for following an explicit note-number or phrase cross-
    reference (e.g. "see Note 45", "related party transactions") to the
    real content it points to — literal keyword/note-number matching over
    BOTH table_chunks (data tables/schedules) and text_chunks (narrative/
    heading text), standalone only.

    Gap this fills: neither existing reports-DB tool can follow a bare
    cross-reference. get_schedule_note only accepts the 7 fixed schedule
    names in AuditScheduleReference — it has no notion of an arbitrary note
    number like "Note 45" (Related Party Transactions).
    search_company_disclosures is semantic-search-only over text_chunks —
    it never touches table_chunks, so it reliably finds a narrative
    cross-reference ("disclosed in Note No 45") but not the actual data
    table those narrative sentences point to. lookup_report_reference closes
    that gap by searching both, using literal ILIKE matching (not semantic
    similarity), so it reliably surfaces the real data (e.g. actual related-
    party transaction amounts), not just a pointer saying "see Note 45".

    New addition — not present in the original codebase.
    """

    @staticmethod
    def _note_number_prefix(reference: str) -> str | None:
        """
        First numeric-dotted sequence in `reference`, e.g. "Note 45" -> "45",
        "Note No. 45.2.1" -> "45.2.1", "related party transactions" -> None.
        Real headings/table titles in this data are numbered this way (e.g.
        "45.2.1. Transactions with Subsidiaries"), so a bare numbering-prefix
        match (title/content starting with "45.") catches them even when the
        reference text itself doesn't otherwise appear verbatim.
        """
        m = re.search(r"(\d+(?:\.\d+)*)", reference or "")
        return m.group(1) if m else None

    @staticmethod
    def lookup_report_reference(
        company: str, financial_year: str, reference: str, conn_reports
    ) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."
        if not reference or not reference.strip():
            return "Please provide a note number (e.g. 'Note 45') or a reference phrase to look up."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        doc_id = match["doc_id"]
        reference = reference.strip()
        note_prefix = ReportReferenceTools._note_number_prefix(reference)
        phrase_pattern = f"%{reference}%"
        prefix_pattern = f"{note_prefix}.%" if note_prefix else None

        doc_header = (
            f"Document: {match['doc_name']} (doc_id={doc_id}, "
            f"company={match['company']}, FY{match['fy_start']}-{str(match['fy_end'])[-2:]})\n"
            f"{UnitResolver.units_line(doc_id, conn_reports)}\n"
            f"Reference lookup: '{reference}' (standalone only)\n"
        )

        # Data tables — table_chunks (titles/descriptions), plus a bare
        # numbering-prefix match on the title (e.g. "45.%").
        table_rows: list[dict] = []
        try:
            clauses = ["table_title ILIKE %s", "table_description ILIKE %s"]
            params: list = [doc_id, phrase_pattern, phrase_pattern]
            if prefix_pattern:
                clauses.append("table_title ILIKE %s")
                params.append(prefix_pattern)
            sql = (
                "SELECT table_id, table_title, table_description, table_md, page_ocr_start "
                "FROM public.table_chunks "
                "WHERE doc_id = %s "
                "  AND (toc_section IS NULL OR toc_section NOT ILIKE '%%consolidated%%') "
                f"  AND ({' OR '.join(clauses)}) "
                "ORDER BY table_id LIMIT 10"
            )
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                table_rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass

        # Narrative/heading text — text_chunks, plus the same numbering-
        # prefix match restricted to heading rows.
        text_rows: list[dict] = []
        try:
            clauses = ["content ILIKE %s"]
            params = [doc_id, phrase_pattern]
            if prefix_pattern:
                clauses.append("(chunk_type = 'heading' AND content ILIKE %s)")
                params.append(prefix_pattern)
            sql = (
                "SELECT chunk_id, page_ocr_start, chunk_type, content, section_breadcrumb, section "
                "FROM public.text_chunks "
                "WHERE doc_id = %s "
                "  AND (section_breadcrumb::text IS NULL OR section_breadcrumb::text NOT ILIKE '%%consolidated%%') "
                f"  AND ({' OR '.join(clauses)}) "
                "ORDER BY page_ocr_start, chunk_id LIMIT 10"
            )
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                text_rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass

        if not table_rows and not text_rows:
            return (
                doc_header
                + f"\nNo data tables or narrative content matching '{reference}' were found in "
                f"{match['doc_name']} (doc_id={doc_id})."
            )

        lines = [doc_header]
        if table_rows:
            lines.append("--- Data tables ---")
            for r in table_rows:
                title = r.get("table_description") or r.get("table_title") or ""
                lines.append(SourceRef.table(r["table_id"], r.get("page_ocr_start"), title))
                lines.append(r.get("table_md") or "")
                lines.append("")
        if text_rows:
            lines.append("--- Narrative / heading text ---")
            for r in text_rows:
                lines.append(
                    SourceRef.chunk(
                        r["chunk_id"], r.get("page_ocr_start"),
                        r.get("section"), extra=str(r.get("chunk_type") or "")
                    ) + f" {r.get('content', '')}"
                )
            lines.append("")

        return "\n".join(lines)


# ===== SECTION 7: ComplianceTools =====

class ComplianceTools:
    """
    Financial statement Schedule III / Ind AS line-item completeness check.

    Migrated from compliance_tools.py. Needs BOTH databases at once:
      - conn_reports (reports DB): the company's actual statement tables
        + the framework lookup (DocumentResolver.get_framework_and_doc).
      - conn_rules (rules DB): the Schedule III proforma line items and,
        for Cash Flow, Ind AS 7.

    Scope note (intentional, per design): LINE-ITEM COMPLETENESS check only.
    """

    _COMPLETENESS_DISCLAIMER = (
        "NOTE: This is a line-item COMPLETENESS check only — it verifies whether "
        "the required line items/headings are present. It does NOT verify "
        "correct current/non-current classification, measurement basis, or "
        "disclosure adequacy."
    )

    _STATEMENT_LABELS = {
        "balance_sheet": "Balance Sheet",
        "profit_loss": "Statement of Profit and Loss",
        "cash_flow": "Statement of Cash Flows",
        "statement_of_equity": "Statement of Changes in Equity",
    }

    _STATEMENT_TITLE_KEYWORDS = {
        "balance_sheet": "balance sheet",
        "profit_loss": "profit and loss",
        "cash_flow": "cash flow",
        "statement_of_equity": "changes in equity",
    }

    _PART_ID_BY_STATEMENT = {
        "balance_sheet": "PART I",
        "profit_loss": "PART II",
    }

    _IND_AS_OCI_REQUIREMENT_NOTE = (
        "IMPORTANT — Ind AS-specific structural requirement: under Ind AS "
        "(Division II), the Statement of Profit and Loss MUST include a "
        "separate 'Other Comprehensive Income (OCI)' line item/section (Ind AS "
        "1). This is a structural requirement, not a materiality-governed "
        "optional item — unlike Goodwill or Investment Property, its absence "
        "IS a compliance defect. Check the actual statement above for an "
        "'Other Comprehensive Income' or 'OCI' line item; if it is not present, "
        "flag it as a missing required line item regardless of the general "
        "materiality instructions."
    )

    @staticmethod
    def _classify_framework_division(framework_text: str) -> str:
        """Classify a framework passage into a Schedule III division."""
        text = (framework_text or "").lower()
        if "ind as" in text or "indian accounting standard" in text:
            return "Division II"
        return "Division I"

    @staticmethod
    def _get_materiality_note(division_no: str, conn_rules) -> str:
        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT chunk FROM public.schedule_iii_chunks "
                    "WHERE division_no = %s "
                    "  AND section_title ILIKE %s "
                    "  AND (chunk ILIKE %s OR chunk ILIKE %s) "
                    "ORDER BY chunk_id",
                    (
                        division_no, "%GENERAL INSTRUCTIONS FOR PREPARATION OF FINANCIAL STATEMENTS%",
                        "%material%", "%minimum requirements%",
                    ),
                )
                rows = cur.fetchall()
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return ""

        if not rows:
            return ""

        return "\n".join(r["chunk"] for r in rows if r.get("chunk"))

    @staticmethod
    def _get_schedule_iii_line_items(division_no: str, statement_type: str, conn_rules) -> str:
        """Balance Sheet / P&L: the literal blank Schedule III proforma table(s)."""
        part_id = ComplianceTools._PART_ID_BY_STATEMENT.get(statement_type)
        if part_id is None:
            return ""

        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT table_id FROM public.schedule_iii_chunks "
                    "WHERE division_no = %s AND part_id = %s AND has_table = true "
                    "ORDER BY chunk_id LIMIT 1",
                    (division_no, part_id),
                )
                row = cur.fetchone()
                if not row or not row.get("table_id"):
                    return (
                        f"No Schedule III proforma table found for {division_no} {part_id}."
                    )

                table_ids = [t.strip() for t in row["table_id"].split(",") if t.strip()]
                cur.execute(
                    "SELECT table_id, table_html FROM public.schedule_iii_table_chunks "
                    "WHERE table_id = ANY(%s) ORDER BY table_id",
                    (table_ids,),
                )
                html_parts = [r["table_html"] for r in cur.fetchall() if r.get("table_html")]
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return f"[error fetching Schedule III proforma for {division_no} {part_id}]"

        if not html_parts:
            return f"No Schedule III proforma table content found for {division_no} {part_id}."

        return "\n".join(html_parts)

    @staticmethod
    def _get_schedule_iii_text_requirement(division_no: str, conn_rules) -> str:
        """Statement of Changes in Equity: textual instructions only, no proforma table."""
        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT chunk FROM public.schedule_iii_chunks "
                    "WHERE division_no = %s AND section_title ILIKE %s "
                    "ORDER BY chunk_id LIMIT 3",
                    (division_no, "%changes in equity%"),
                )
                rows = cur.fetchall()
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return f"[error fetching Schedule III Statement of Changes in Equity requirements for {division_no}]"

        if not rows:
            return f"No Schedule III Statement of Changes in Equity requirement found for {division_no}."

        return "\n\n".join(r["chunk"] for r in rows if r.get("chunk"))

    @staticmethod
    def _get_ind_as7_requirements(conn_rules) -> str:
        """Cash Flow: Schedule III has no proforma — the benchmark is Ind AS 7 itself."""
        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT section_title, paragraph_no, text FROM public.ind_as_chunks "
                    "WHERE standard_number = 7 "
                    "  AND (section_title ILIKE %s OR section_title ILIKE %s "
                    "       OR section_title ILIKE %s OR section_title ILIKE %s) "
                    "ORDER BY seq LIMIT 8",
                    ("%presentation%", "%operating activities%", "%direct method%", "%indirect method%"),
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        "SELECT section_title, paragraph_no, text FROM public.ind_as_chunks "
                        "WHERE standard_number = 7 ORDER BY seq LIMIT 8"
                    )
                    rows = cur.fetchall()
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return "[error fetching Ind AS 7 requirements]"

        if not rows:
            return "No Ind AS 7 content found in the rules database."

        return "\n\n".join(
            f"[{r.get('section_title', '')} | para {r.get('paragraph_no', '?')}] {r.get('text', '')}"
            for r in rows
        )

    @staticmethod
    def _find_statement_tables(doc_id: str, statement_type: str, conn_reports) -> str:
        """Find the company's actual statement table(s) (standalone only)."""
        keyword = ComplianceTools._STATEMENT_TITLE_KEYWORDS.get(statement_type)
        if keyword is None:
            return f"Unknown statement_type '{statement_type}'."

        queries = [
            (
                "SELECT table_id, table_title, table_md, page_ocr_start "
                "FROM public.table_chunks "
                "WHERE doc_id = %s AND financial_stmt_type = %s "
                "  AND position(%s in lower(table_title)) BETWEEN 1 AND 25 "
                "  AND table_title NOT ILIKE %s "
                "ORDER BY table_id",
                (doc_id, statement_type, keyword, "%consolidated%"),
            ),
            (
                "SELECT table_id, table_title, table_md, page_ocr_start "
                "FROM public.table_chunks "
                "WHERE doc_id = %s "
                "  AND position(%s in lower(table_title)) BETWEEN 1 AND 25 "
                "  AND table_title NOT ILIKE %s "
                "ORDER BY table_id",
                (doc_id, keyword, "%consolidated%"),
            ),
            (
                "SELECT table_id, table_title, table_md, page_ocr_start "
                "FROM public.table_chunks "
                "WHERE doc_id = %s AND financial_stmt_type = %s "
                "  AND toc_section ILIKE %s AND toc_section NOT ILIKE %s "
                "ORDER BY table_id",
                (doc_id, statement_type, f"%{keyword}%", "%consolidated%"),
            ),
            # Many entities prefix the statement title with their own name —
            # "STEEL AUTHORITY OF INDIA LIMITED Standalone Balance Sheet" puts the
            # keyword well past character 25, so every position-bounded query above
            # misses it and the whole document reads as having no statements.
            # Dropping the position bound is safe HERE because financial_stmt_type
            # is already an independent classification of the table.
            (
                "SELECT table_id, table_title, table_md, page_ocr_start "
                "FROM public.table_chunks "
                "WHERE doc_id = %s AND financial_stmt_type = %s "
                "  AND lower(table_title) LIKE %s "
                "  AND table_title NOT ILIKE %s "
                "ORDER BY table_id",
                (doc_id, statement_type, f"%{keyword}%", "%consolidated%"),
            ),
            # Last resort, title-only: financial_stmt_type is NOT reliable — SAIL
            # 2023-24 tags its "Standalone Balance Sheet" as statement_of_equity —
            # so fall back to an explicitly self-describing title. Requiring BOTH
            # "standalone" and the statement keyword keeps this precise: it admits
            # "... Standalone Balance Sheet" while rejecting incidental mentions
            # such as "Amount of CWIP ... from initial recognition in Balance Sheet".
            (
                "SELECT table_id, table_title, table_md, page_ocr_start "
                "FROM public.table_chunks "
                "WHERE doc_id = %s "
                "  AND table_title ILIKE %s "
                "  AND lower(table_title) LIKE %s "
                "  AND table_title NOT ILIKE %s "
                "ORDER BY table_id",
                (doc_id, "%standalone%", f"%{keyword}%", "%consolidated%"),
            ),
        ]

        for sql, params in queries:
            try:
                with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]
                    if rows:
                        rows += ComplianceTools._fetch_untitled_continuations(
                            doc_id, rows[-1]["table_id"], rows[-1].get("page_ocr_start"), conn_reports
                        )
                        parts = []
                        for r in rows:
                            md = r.get("table_md") or ""
                            parts.append(
                                SourceRef.table(
                                    r["table_id"], r.get("page_ocr_start"), r.get("table_title")
                                ) + f"\n{md}"
                            )
                        return "\n\n".join(parts)
            except Exception:
                try:
                    conn_reports.rollback()
                except Exception:
                    pass

        return (
            f"No standalone {ComplianceTools._STATEMENT_LABELS.get(statement_type, statement_type)} "
            f"table was found for this document."
        )

    @staticmethod
    def _fetch_untitled_continuations(
        doc_id: str, after_table_id: str, anchor_page: int | None, conn_reports, limit: int = 1
    ) -> list[dict]:
        """Statement tables can be split across a page break into a titled primary
        row plus an immediately-following UNTITLED continuation row."""
        if anchor_page is None:
            return []

        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT table_id, table_title, table_md, page_ocr_start "
                    "FROM public.table_chunks "
                    "WHERE doc_id = %s AND table_id > %s "
                    "  AND (table_title IS NULL OR table_title = '') "
                    "  AND page_ocr_start BETWEEN %s AND %s "
                    "ORDER BY table_id LIMIT %s",
                    (doc_id, after_table_id, anchor_page, anchor_page + 2, limit),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            return []

    @staticmethod
    def _check_one_statement(doc_id: str, division_no: str, statement_type: str, conn_reports, conn_rules) -> str:
        label = ComplianceTools._STATEMENT_LABELS.get(statement_type, statement_type)
        actual = ComplianceTools._find_statement_tables(doc_id, statement_type, conn_reports)

        if statement_type in ComplianceTools._PART_ID_BY_STATEMENT:
            benchmark = ComplianceTools._get_schedule_iii_line_items(division_no, statement_type, conn_rules)
            benchmark_label = f"Schedule III {division_no} required line items ({ComplianceTools._PART_ID_BY_STATEMENT[statement_type]})"
        elif statement_type == "cash_flow":
            benchmark = ComplianceTools._get_ind_as7_requirements(conn_rules)
            benchmark_label = "Ind AS 7 presentation requirements (Schedule III prescribes no format for this statement)"
        else:  # statement_of_equity
            benchmark = ComplianceTools._get_schedule_iii_text_requirement(division_no, conn_rules)
            benchmark_label = f"Schedule III {division_no} textual requirements (no proforma table exists for this statement)"

        extra_note = ""
        if statement_type == "profit_loss" and division_no == "Division II":
            extra_note = f"\n[{ComplianceTools._IND_AS_OCI_REQUIREMENT_NOTE}]\n"

        return (
            f"=== {label} ===\n\n"
            f"[ACTUAL STATEMENT — from the company's annual report]\n{actual}\n\n"
            f"[REQUIRED LINE ITEMS / REQUIREMENTS — {benchmark_label}]\n{benchmark}\n"
            f"{extra_note}"
        )

    @staticmethod
    def _check_statement_compliance(
        company: str,
        financial_year: str,
        statement_type: str,
        conn_reports,
        conn_rules,
    ) -> str:
        if conn_reports is None or conn_rules is None:
            return "[tool error] Both the reports database and rules database must be configured for this check."

        doc, framework_passage = DocumentResolver.get_framework_and_doc(company, financial_year, conn_reports)
        if doc is None:
            return framework_passage  # not-found / ambiguous message

        division_no = ComplianceTools._classify_framework_division(framework_passage)

        statement_type = (statement_type or "all").strip().lower()
        if statement_type in ("", "all"):
            types_to_check = list(ComplianceTools._STATEMENT_LABELS.keys())
        elif statement_type in ComplianceTools._STATEMENT_LABELS:
            types_to_check = [statement_type]
        else:
            return (
                f"Unknown statement_type '{statement_type}'. Valid values: "
                f"{', '.join(ComplianceTools._STATEMENT_LABELS.keys())}, or 'all'."
            )

        sections = [
            ComplianceTools._check_one_statement(doc["doc_id"], division_no, st, conn_reports, conn_rules)
            for st in types_to_check
        ]

        materiality_note = ComplianceTools._get_materiality_note(division_no, conn_rules)

        header = (
            f"Document: {doc['doc_name']} (doc_id={doc['doc_id']}, company={doc['company']}, "
            f"FY{doc['fy_start']}-{str(doc['fy_end'])[-2:]})\n"
            f"{UnitResolver.units_line(doc['doc_id'], conn_reports)}\n"
            f"Framework classification: {division_no} (derived from the Statement of Compliance "
            f"passage — call get_reporting_framework for the exact citable wording)\n"
            f"{ComplianceTools._COMPLETENESS_DISCLAIMER}\n\n"
            f"[SCHEDULE III GENERAL INSTRUCTIONS ON MATERIALITY — READ BEFORE FLAGGING ANY ITEM AS MISSING]\n"
            f"{materiality_note}\n"
            f"^ The proforma below is a MINIMUM/FORMAT template, not a mandatory checklist. An absent "
            f"line item is NOT a compliance defect unless it is a structural heading/subtotal (e.g. "
            f"ASSETS, LIABILITIES, EQUITY, Total Assets, Total Equity and Liabilities, Non-current/"
            f"Current classification) — those are always required. Specific line items (e.g. Goodwill, "
            f"Investment Property, Biological Assets) are commonly and correctly omitted when nil/not "
            f"applicable. Several items are EITHER/OR pairs based on the company's actual position — "
            f"e.g. 'Deferred tax assets (net)' OR 'Deferred tax liabilities (net)', and 'Current Tax "
            f"Assets (Net)' OR 'Current Tax Liabilities (Net)' — presenting only the applicable one of "
            f"the pair is fully compliant, not a partial match.\n"
        )

        return header + "\n" + "\n".join(sections)


# ===== SECTION 8: RatioReference =====

class RatioReference:
    """
    Static reference data for financial ratio analysis. Migrated from
    ratios_reference.py — all 41 ratios (name, category, formula, ideal
    level, implications). Ratios with `implemented: True` have a
    `components` dict wired up for actual computation.
    """

    RATIOS = {
        # -------------------------------------------------------------------
        # Liquidity Ratios
        # -------------------------------------------------------------------
        "current_ratio": {
            "name": "Current Ratio",
            "category": "liquidity",
            "formula_text": "Current Assets / Current Liabilities",
            "ideal_level": "≈ 2 : 1",
            "implication_higher": "Idle current assets, excess inventory/cash lying unused, inefficient working capital management, lower return on assets.",
            "implication_lower": "Difficulty in meeting short-term obligations as they fall due; higher liquidity/credit risk; possible strain on supplier relations.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "current_assets_total", "denominator": "current_liabilities_total"},
        },
        "quick_ratio": {
            "name": "Quick Ratio (Acid-Test Ratio)",
            "category": "liquidity",
            "formula_text": "Quick Assets / Current Liabilities, where Quick Assets = Current Assets - Inventories - Other Current Assets (Prepaid Expenses)",
            "ideal_level": "≈ 1 : 1",
            "implication_higher": "Excess of liquid resources (cash/receivables) kept idle instead of being invested for returns.",
            "implication_lower": "Inability to meet immediate obligations if inventory cannot be quickly converted to cash; increased default risk.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "quick_assets", "denominator": "current_liabilities_total"},
        },
        "cash_ratio": {
            "name": "Cash Ratio (Absolute Liquidity Ratio)",
            "category": "liquidity",
            "formula_text": "(Cash and Cash Equivalents + Current Investments) / Current Liabilities",
            "ideal_level": "No universal norm; generally 0.5-1 considered comfortable",
            "implication_higher": "Large idle cash/near-cash balances - opportunity cost of funds not invested/earning returns.",
            "implication_lower": "Very limited capacity to pay off liabilities immediately without realising other current assets.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "cash_and_investments", "denominator": "current_liabilities_total"},
        },
        "basic_defense_interval": {
            "name": "Basic Defense Interval / Interval Measure",
            "category": "liquidity",
            "formula_text": "(Cash and Cash Equivalents + Trade Receivables + Current Investments) / Daily Operating Expenses",
            "ideal_level": "Higher is better; often compared to 30-90 days",
            "implication_higher": "Business can sustain operations for a long period without fresh financing - strong buffer, but may indicate idle resources.",
            "implication_lower": "Business would run out of cash to meet operating expenses very quickly if revenues stopped - vulnerable to shocks.",
            "computable_phase": 2,
            "implemented": True,
            "components": {"numerator": "quick_assets_plus_receivables", "denominator": "daily_operating_expenses"},
            "approximate": True,
            "approximation_note": (
                "Daily Operating Expenses approximated as (Total Expenses - Finance Costs) / 365 "
                "from the face Statement of Profit and Loss — not a detailed cash-operating-expense "
                "build-up (which would separately exclude non-cash items like depreciation)."
            ),
        },
        "net_working_capital": {
            "name": "Net Working Capital",
            "category": "liquidity",
            "formula_text": "Current Assets - Current Liabilities (excluding Short-term Borrowings)",
            "ideal_level": "Must be positive; adequate cushion expected as per business/banker norms",
            "implication_higher": "Large surplus of current assets over current liabilities - funds may be lying unused instead of being productively deployed.",
            "implication_lower": "Negative/low NWC indicates the firm may struggle to meet short-term obligations - risk of financial distress/insolvency.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"value": "net_working_capital"},
            "is_difference": True,
        },

        # -------------------------------------------------------------------
        # Leverage / Capital Structure Ratios
        # -------------------------------------------------------------------
        "equity_ratio": {
            "name": "Equity Ratio",
            "category": "leverage",
            "formula_text": "Total Equity / Net Assets (Total Assets - Current Liabilities)",
            "ideal_level": "Higher considered safer; no fixed number (~0.5+ often preferred)",
            "implication_higher": "Firm largely financed by owners' funds - lower risk to lenders, but may forgo benefits of financial leverage (lower ROE).",
            "implication_lower": "Firm heavily dependent on borrowed funds - higher risk perception for lenders and shareholders.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "total_equity", "denominator": "net_assets"},
        },
        "debt_ratio": {
            "name": "Debt Ratio",
            "category": "leverage",
            "formula_text": "Total Borrowings / Net Assets (Total Assets - Current Liabilities)",
            "ideal_level": "< 1 generally considered safe",
            "implication_higher": "Ratio > 1 means a greater portion of assets is funded by debt - risky, higher solvency concern.",
            "implication_lower": "Conservative financing; low financial risk, but may be under-utilising the leverage benefit to boost returns.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "total_borrowings", "denominator": "net_assets"},
        },
        "debt_to_equity_ratio": {
            "name": "Debt-to-Equity Ratio",
            "category": "leverage",
            "formula_text": "Total Borrowings / Total Equity",
            "ideal_level": "≈ 2:1 acceptable in general; 1:1 considered conservative/ideal for many industries",
            "implication_higher": "Less protection/safety cushion for creditors; higher financial risk and interest burden; higher volatility of equity returns.",
            "implication_lower": "Wider safety cushion for creditors; conservative capital structure but may mean under-utilisation of cheaper debt (lower ROE via leverage).",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "total_borrowings", "denominator": "total_equity"},
        },
        "debt_to_total_assets_ratio": {
            "name": "Debt to Total Assets Ratio",
            "category": "leverage",
            "formula_text": "Total Borrowings / Total Assets",
            "ideal_level": "Lower preferred; generally < 0.5-0.6 considered safe",
            "implication_higher": "Assets less backed by equity - higher financial leverage and insolvency risk.",
            "implication_lower": "Assets predominantly equity-financed - lower risk, but possibly conservative use of leverage.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "total_borrowings", "denominator": "total_assets"},
        },
        "capital_gearing_ratio": {
            "name": "Capital Gearing Ratio",
            "category": "leverage",
            "formula_text": "(Preference Share Capital + Debentures + Other Borrowings) / (Equity Share Capital + Other Equity - Losses)",
            "ideal_level": "Lower ratio (low-geared) considered safer; no universal number",
            "implication_higher": "High-geared company - higher fixed interest/dividend burden; risky in periods of low profits, though beneficial (magnifies EPS) when profits are high.",
            "implication_lower": "Low-geared company - lower financial risk but equity shareholders forgo the potential leverage benefit on EPS during good years.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "capital_gearing_numerator", "denominator": "total_equity"},
            "approximate": True,
            "approximation_note": (
                "Numerator approximated as Total Borrowings (Long-term + Short-term) because the "
                "Preference Share Capital / Debentures / Other Borrowings split usually only appears "
                "in detailed Notes, not the face Balance Sheet this tool parses."
            ),
        },
        "proprietary_ratio": {
            "name": "Proprietary Ratio",
            "category": "leverage",
            "formula_text": "Total Equity (Equity Share Capital + Preference Share Capital + Other Equity) / Total Assets",
            "ideal_level": "Higher considered safer; often ≥ 0.5 preferred",
            "implication_higher": "Total assets mostly financed by shareholders - safer for creditors, but possibly conservative/under-leveraged.",
            "implication_lower": "Greater reliance on outside liabilities to finance assets - higher risk for creditors and long-term solvency concern.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "total_equity_with_pref", "denominator": "total_assets"},
        },

        # -------------------------------------------------------------------
        # Coverage Ratios
        # -------------------------------------------------------------------
        "dscr": {
            "name": "Debt Service Coverage Ratio (DSCR)",
            "category": "coverage",
            "formula_text": "Earnings Available for Debt Service / (Finance Costs + Instalments)",
            "ideal_level": "≈ 1.5 to 2",
            "implication_higher": "Strong ability to service debt comfortably, good buffer for lenders - but very high ratio may signal excess conservative borrowing capacity unused.",
            "implication_lower": "Ratio below 1 (or well below 1.5) indicates inadequate earnings to service debt obligations - high default risk.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "earnings_available_for_debt_service", "denominator": "finance_costs_plus_instalments"},
        },
        "interest_coverage_ratio": {
            "name": "Interest Coverage Ratio (Times Interest Earned)",
            "category": "coverage",
            "formula_text": "EBIT / Finance Costs",
            "ideal_level": "> 1 required; generally 3-5 times considered comfortable",
            "implication_higher": "Large margin of safety for paying interest even if earnings decline - financially strong on debt-servicing.",
            "implication_lower": "Ratio close to or below 1 shows inability to cover interest from operating earnings - signals excessive debt/inefficient operations and high default risk.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "ebit", "denominator": "finance_costs"},
        },
        "preference_dividend_coverage_ratio": {
            "name": "Preference Dividend Coverage Ratio",
            "category": "coverage",
            "formula_text": "Profit for the Period / Preference Dividend",
            "ideal_level": "> 1; higher preferred",
            "implication_higher": "Strong margin of safety for preference shareholders' dividend.",
            "implication_lower": "Ratio below 1 indicates the company may be unable to pay the fixed preference dividend from current earnings.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "profit_for_period", "denominator": "preference_dividend"},
            "not_applicable_if_denominator_zero": True,
        },
        "fixed_charges_coverage_ratio": {
            "name": "Fixed Charges Coverage Ratio",
            "category": "coverage",
            "formula_text": "(EBIT + Depreciation and Amortisation Expense) / (Finance Costs + Repayment of Loan)",
            "ideal_level": "> 1 considered safe",
            "implication_higher": "Strong cash flow cover for all fixed financing charges (interest + principal).",
            "implication_lower": "Ratio below 1 indicates cash generation is insufficient to meet fixed financing commitments - risk of default.",
            "computable_phase": 1,
            "implemented": True,
            "components": {"numerator": "ebit_plus_depreciation", "denominator": "finance_costs_plus_instalments"},
        },

        # -------------------------------------------------------------------
        # Activity / Efficiency / Turnover Ratios — Phase 2 (implemented)
        # -------------------------------------------------------------------
        "total_assets_turnover_ratio": {"name": "Total Assets Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Average Total Assets", "ideal_level": "Higher is better; benchmark against industry average", "implication_higher": "Very efficient utilisation of total assets to generate sales - though could also reflect very low fixed-asset base (asset-light model).", "implication_lower": "Assets not efficiently utilised to generate sales - sign of idle capacity or over-investment in assets.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "average_total_assets"}},
        "fixed_assets_turnover_ratio": {"name": "Fixed Assets Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Property, Plant and Equipment (Net Block)", "ideal_level": "Higher is better; industry-specific benchmark", "implication_higher": "Efficient use of fixed assets in generating sales - note: firms with old, substantially depreciated assets can show artificially high ratios.", "implication_lower": "Sales/profit generated per rupee of fixed assets is low - may indicate overcapacity, idle capacity, or under-performing equipment.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "property_plant_equipment"}},
        "capital_turnover_ratio": {"name": "Capital Turnover Ratio / Net Assets Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Capital Employed (Total Assets - Current Liabilities - Investments)", "ideal_level": "Higher is better; compare to industry norm", "implication_higher": "Efficient utilisation of owners' and long-term creditors' funds to generate sales.", "implication_lower": "Poor utilisation of long-term capital employed in generating sales - funds may be under-deployed.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "capital_employed"}},
        "current_assets_turnover_ratio": {"name": "Current Assets Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Current Assets", "ideal_level": "Higher is better", "implication_higher": "Efficient utilisation of current assets in generating sales.", "implication_lower": "Current assets (cash, receivables, stock) not being efficiently used to generate sales - excess/idle current assets.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "current_assets_total"}},
        "working_capital_turnover_ratio": {"name": "Working Capital Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Working Capital (Current Assets - Current Liabilities)", "ideal_level": "Higher is better, but not excessively high", "implication_higher": "Efficient use of working capital; however, an excessively high ratio may indicate overtrading and need for additional working capital.", "implication_lower": "Working capital not being effectively deployed to generate sales - funds tied up unproductively.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "working_capital_plain"}},
        "inventory_turnover_ratio": {"name": "Inventory / Stock Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Average Inventory", "ideal_level": "Higher is better; industry-specific (e.g., perishables high, capital goods low)", "implication_higher": "Inventory moving/selling fast - good from a liquidity standpoint, but too high may risk stock-outs/lost sales.", "implication_lower": "Inventory is not being sold/used quickly - risk of obsolescence, higher holding costs, and funds blocked in stock.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "average_inventory"}},
        "receivables_turnover_ratio": {"name": "Receivables (Debtors) Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations (Credit Sales portion) / Average Trade Receivables", "ideal_level": "Higher is better; should be in line with the firm's credit terms", "implication_higher": "Collections are being made rapidly (tight/efficient credit and collection policy).", "implication_lower": "Liberal credit terms extended to customers - larger funds blocked in receivables and greater risk of bad debts.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "average_trade_receivables"}, "approximate": True, "approximation_note": "Full Revenue from Operations used as a Credit Sales proxy — the credit-vs-cash sales split is not disclosed on any face statement."},
        "receivables_collection_period": {"name": "Receivables (Debtors) Collection Period", "category": "activity", "formula_text": "365 / Receivables Turnover Ratio", "ideal_level": "Should match/be close to the credit period officially allowed to customers", "implication_higher": "Longer than the stated credit period - indicates poor collection efforts and increased blockage of working capital.", "implication_lower": "Very short collection period than industry practice may indicate an overly strict credit policy that could restrict sales growth.", "computable_phase": 2, "implemented": True, "derived_from_ratio": "receivables_turnover_ratio"},
        "payables_turnover_ratio": {"name": "Payables (Creditors) Turnover Ratio", "category": "activity", "formula_text": "Revenue from Operations / Average Trade Payables", "ideal_level": "Should be compared with the credit period the firm itself allows to its customers", "implication_higher": "Firm settles its dues to suppliers very quickly (high ratio) - may forgo cheap trade credit financing.", "implication_lower": "Firm is taking longer credit from suppliers (low ratio/liberal terms received) - improves cash flow but may harm supplier relations if excessive.", "computable_phase": 2, "implemented": True, "components": {"numerator": "revenue_from_operations", "denominator": "average_trade_payables"}},
        "payables_payment_period": {"name": "Payables Velocity / Average Payment Period", "category": "activity", "formula_text": "365 / Payables Turnover Ratio", "ideal_level": "Compared with credit period offered by the firm to its own customers", "implication_higher": "Firm is taking very long to pay suppliers - may indicate cash constraints or straining supplier relationships.", "implication_lower": "Firm pays suppliers very quickly - reduces available free short-term financing from trade credit.", "computable_phase": 2, "implemented": True, "derived_from_ratio": "payables_turnover_ratio"},

        # -------------------------------------------------------------------
        # Profitability Ratios — Phase 2 (implemented, except EPS/DPS/Payout)
        # -------------------------------------------------------------------
        "gross_profit_ratio": {"name": "Gross Profit Ratio (Gross Profit Margin)", "category": "profitability", "formula_text": "(Gross Profit / Revenue from Operations) x 100", "ideal_level": "Industry-specific benchmark; higher is generally favourable", "implication_higher": "Strong control over production costs / good pricing power - favourable sign of good management.", "implication_lower": "Poor control over production costs, intense price competition, or unfavourable product mix.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "gross_profit", "denominator": "revenue_from_operations"}, "approximate": True, "approximation_note": "Gross Profit = Revenue - (Cost of Materials Consumed + Purchases + Changes in Inventories), where any of those three is treated as 0 when not separately disclosed."},
        "net_profit_ratio": {"name": "Net Profit Ratio (Net Profit Margin)", "category": "profitability", "formula_text": "(Profit for the Period / Revenue from Operations) x 100", "ideal_level": "Industry-specific benchmark; higher is favourable", "implication_higher": "Strong overall profitability after all expenses and taxes - positive returns from the business.", "implication_lower": "High operating/financial expenses or tax burden eroding profitability; may signal inefficiency.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "profit_for_period", "denominator": "revenue_from_operations"}},
        "operating_profit_ratio": {"name": "Operating Profit Ratio", "category": "profitability", "formula_text": "(EBIT / Revenue from Operations) x 100", "ideal_level": "Industry-specific benchmark; higher is favourable", "implication_higher": "Efficient core/operating performance excluding financing and tax effects.", "implication_lower": "Weak operating performance - high operating costs relative to sales, independent of financing structure.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "ebit", "denominator": "revenue_from_operations"}},
        "expense_ratio": {"name": "Expense Ratios", "category": "profitability", "formula_text": "Respective Expense / Revenue from Operations x 100", "ideal_level": "Lower is generally favourable; compare with industry norm", "implication_higher": "High proportion of sales absorbed by that expense head - cost/efficiency concern, margin pressure.", "implication_lower": "Low proportion of sales absorbed by expenses - good cost control, though unusually low levels should be checked for under-investment.", "computable_phase": 2, "implemented": True, "is_breakdown": True},
        "return_on_investment": {"name": "Return on Investment (ROI)", "category": "profitability", "formula_text": "(Return/Profit/Earnings / Investment) x 100", "ideal_level": "Should exceed the firm's cost of capital / borrowing cost", "implication_higher": "Strong overall efficiency in generating returns on funds invested.", "implication_lower": "Poor utilisation of invested funds; return below cost of capital indicates value erosion.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "profit_for_period", "denominator": "net_assets"}, "approximate": True, "approximation_note": "Return = Profit for the Period (standalone P&L); Investment = Total Assets - Current Liabilities — this specific mapping was confirmed with the user (the reference sheet's own ROI formula does not specify these precisely; it differs from ROCE only in using Profit for the Period instead of EBIT as the numerator)."},
        "return_on_assets": {"name": "Return on Assets (ROA)", "category": "profitability", "formula_text": "[Profit for the Period (+ Finance Costs)] / Average Total Assets", "ideal_level": "Higher is better; compare with industry & cost of capital", "implication_higher": "Efficient use of the asset base to generate profit.", "implication_lower": "Assets are not being efficiently utilised to generate profits - possible idle/underperforming assets.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "profit_for_period_plus_finance_costs", "denominator": "average_total_assets"}},
        "return_on_capital_employed": {"name": "Return on Capital Employed (ROCE)", "category": "profitability", "formula_text": "[EBIT / Capital Employed (Total Assets - Current Liabilities - Investments)] x 100", "ideal_level": "Should always be higher than the firm's average cost of borrowed funds", "implication_higher": "Strong overall return generated on all long-term funds (equity + debt) employed in the business.", "implication_lower": "ROCE below the cost of borrowing indicates the firm is destroying value by borrowing at a rate higher than what it earns.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "ebit", "denominator": "capital_employed"}},
        "return_on_equity": {"name": "Return on Equity (ROE)", "category": "profitability", "formula_text": "[(Profit for the Period - Preference Dividend) / Total Equity] x 100", "ideal_level": "Higher is favourable; compare with industry & cost of equity", "implication_higher": "Strong profitability for equity shareholders - but check via DuPont analysis whether driven by high margins/turnover or merely high financial leverage (risk).", "implication_lower": "Poor returns to equity shareholders relative to funds invested by them; may reflect low margins, poor asset turnover or excess equity base.", "computable_phase": 2, "implemented": True, "is_percentage": True, "components": {"numerator": "profit_for_period_less_pref_div", "denominator": "total_equity"}},
        "earnings_per_share": {"name": "Earnings Per Share (EPS)", "category": "profitability", "formula_text": "Profit for the Period Available to Equity Shareholders / Number of Equity Shares Outstanding", "ideal_level": "Higher is favourable; track trend over years and vs. peers", "implication_higher": "Strong per-share earning power of the company.", "implication_lower": "Weak per-share profitability - may depress share price and investor confidence.", "computable_phase": 2},
        "dividend_per_share": {"name": "Dividend Per Share (DPS)", "category": "profitability", "formula_text": "Total Dividend Paid to Equity Shareholders / Number of Equity Shares Outstanding", "ideal_level": "Depends on company's dividend policy; compared over time/peers", "implication_higher": "High cash returned to shareholders currently - but may reduce funds retained for reinvestment/growth.", "implication_lower": "Low current income to shareholders - but more earnings retained for growth.", "computable_phase": 2},
        "dividend_payout_ratio": {"name": "Dividend Payout Ratio (DP)", "category": "profitability", "formula_text": "Dividend Per Share (DPS) / Earnings Per Share (EPS)", "ideal_level": "No fixed norm - depends on the stage/policy of the company", "implication_higher": "Large share of profits distributed as dividend - lower retained earnings available for reinvestment/growth.", "implication_lower": "Small share of profits distributed - higher retention for growth, but may disappoint income-seeking investors.", "computable_phase": 2},

        # -------------------------------------------------------------------
        # Market / Valuation Ratios — out of scope entirely
        # -------------------------------------------------------------------
        "price_earnings_ratio": {"name": "Price-Earnings (P/E) Ratio", "category": "market", "formula_text": "Market Price Per Share (MPS) / Earnings Per Share (EPS)", "ideal_level": "Varies by sector/market; compared with sector or market average", "implication_higher": "Market has high growth expectations from the stock, or the stock may be overvalued.", "implication_lower": "Market has low growth expectations, higher perceived risk, or the stock may be undervalued.", "computable_phase": None},
        "dividend_yield": {"name": "Dividend Yield", "category": "market", "formula_text": "(Dividend Per Share / Market Price Per Share) x 100", "ideal_level": "Compared with prevailing interest/FD rates and peer yields", "implication_higher": "High current cash return relative to price - often typical of mature, stable-income stocks.", "implication_lower": "Low current cash return - often typical of growth-oriented stocks reinvesting profits.", "computable_phase": None},
        "earnings_yield": {"name": "Earnings Yield (EP Ratio)", "category": "market", "formula_text": "(Earnings Per Share / Market Price Per Share) x 100", "ideal_level": "Compared with market/sector average and bond yields", "implication_higher": "Stock appears attractively/cheaply valued relative to its earnings.", "implication_lower": "Stock appears expensively valued relative to its earnings.", "computable_phase": None},
        "market_value_to_book_value": {"name": "Market Value / Book Value Per Share (MV/BV)", "category": "market", "formula_text": "Market Price Per Share / Book Value Per Share", "ideal_level": "> 1 generally viewed favourably", "implication_higher": "Market values the company well above its accounting net worth - reflects strong investor confidence/goodwill.", "implication_lower": "Ratio below 1 suggests the market values the company below its book net worth - possible undervaluation or distress signal.", "computable_phase": None},
        "tobins_q_ratio": {"name": "Q Ratio (Tobin's Q)", "category": "market", "formula_text": "Market Value of Equity and Liabilities / Estimated Replacement Cost of Assets", "ideal_level": "≈ 1 (equilibrium)", "implication_higher": "Ratio > 1 suggests the stock/company may be overvalued relative to the replacement cost of its assets.", "implication_lower": "Ratio < 1 suggests the stock/company may be undervalued relative to the replacement cost of its assets.", "computable_phase": None},
    }

    IMPLEMENTED_RATIOS = [rid for rid, r in RATIOS.items() if r.get("implemented")]
    CATEGORIES = ["liquidity", "leverage", "coverage", "activity", "profitability"]

    @classmethod
    def ratios_in_category(cls, category: str) -> list[str]:
        return [rid for rid in cls.IMPLEMENTED_RATIOS if cls.RATIOS[rid]["category"] == category]


# ===== SECTION 9: RatioExtractionEngine =====

def _re_parse_number(raw: str) -> float | None:
    """'-' / blank -> None. '(123.45)' -> -123.45. Strips ₹ and commas."""
    if raw is None:
        return None
    s = raw.strip().replace("₹", "").replace(",", "").strip()
    if s in ("", "-", "—", "–"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1].strip()
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _re_clean_label(raw: str) -> str:
    """Strip leading bullet markers like '(a)', '(i)', 'A', roman numerals, dashes."""
    s = raw.strip()
    s = re.sub(r"^\(?[ivxIVX]{1,4}\)?[\.\)]\s*", "", s)   # (i), (iv), i.
    s = re.sub(r"^\([a-zA-Z]\)\s*", "", s)                  # (a), (B)
    s = re.sub(r"^[A-Z]\s+(?=[A-Z][a-z])", "", s)           # "A Sales" -> "Sales"
    s = re.sub(r"^-\s*", "", s)                              # "- Receivables" -> "Receivables"
    return s.strip()


# Trailing cross-reference arithmetic that real filed statements append to
# line-item captions: "(iii - iv)", "(v+vi)", "(5+6)", "(vii+viii)". The charset
# deliberately allows ONLY roman letters/digits/space/+/-/slash so that genuine
# qualifiers — "(net)", "(net of tax)", "(Net)" — are never stripped.
_RE_ROW_ARITHMETIC = re.compile(r"\s*\([ivx\d\s+\-–—/]+\)\s*$", re.IGNORECASE)
# Leading ordinal used as the row number: "vii Profit before tax", "7 Net cash
# flow from operating activities", "f. Closing cash". Arabic forms are included
# because cash-flow statements number their rows that way.
_RE_LEADING_ORDINAL = re.compile(
    r"^(?:i{1,3}|iv|vi{0,3}|ix|xi{0,3}|xiv|xvi{0,3}|\d{1,2}|[a-z])[\.\)]?\s+",
    re.IGNORECASE,
)
# "/(Loss)", "/Loss", "/ loss", " (Loss)" inside a caption — "Profit/(Loss) before
# tax", "Profit/Loss before tax". The slash-less form REQUIRES parentheses so that
# "items reclassified to profit or loss" is left untouched.
_RE_LOSS_PAREN = re.compile(r"\s*/\s*\(?\s*loss\s*\)?|\s*\(\s*loss\s*\)", re.IGNORECASE)


def _re_normalise_label(label: str) -> str:
    """
    Aggressively normalised form of an already-cleaned label, used ONLY for
    pattern matching (never for display or citation — `raw_label` stays intact).

    Exists because real face-of-statement captions almost never match the plain
    strings the label maps were written against. Measured on the live corpus,
    `profit_before_tax` failed to extract on 65% of documents that DID have the
    row, because captions look like:
        "iii Profit/(Loss) before exceptional items and tax (i-ii)"
        "vii Profit before tax (v + vi)"
        "Profit (Loss) before extraordinary items and taxes(5+6)"
    """
    s = _RE_ROW_ARITHMETIC.sub("", label)
    s = _RE_LEADING_ORDINAL.sub("", s)
    # Substitute a SPACE, not "" — the pattern's trailing \s* would otherwise eat
    # the separator and fuse words ("profit/loss before tax" -> "profitbefore tax").
    s = _RE_LOSS_PAREN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


class Row:
    """One parsed markdown-table row: cleaned label + numeric values. Migrated
    from ratio_extraction.py (used by both RatioExtractionEngine and
    TrendAnalysisTools — kept as a small standalone data class per the plan)."""

    __slots__ = ("label", "raw_label", "norm", "values")

    def __init__(self, raw_label: str, values: list[float | None]):
        self.raw_label = raw_label
        self.label = _re_clean_label(raw_label).lower()
        # Additional match-only form; `label` is left byte-identical to what it
        # always was so no existing caller changes behaviour unexpectedly.
        self.norm = _re_normalise_label(self.label)
        self.values = values


class Source:
    """
    Provenance for one resolved figure, for the audit trail shown alongside
    each ratio's value. Either a direct citation — `statement` + the exact
    `label` text matched in the company's own filed statement (optionally
    with a `note`) — or, for a compound/derived figure, `derived_from`: the
    list of other figure keys it was computed from.
    """

    __slots__ = ("statement", "label", "note", "derived_from")

    def __init__(self, statement=None, label=None, note=None, derived_from=None):
        self.statement = statement
        self.label = label
        self.note = note
        self.derived_from = derived_from


class RatioExtractionEngine:
    """
    Numeric extraction engine for financial ratio analysis. Migrated from
    ratio_extraction.py. Reuses ComplianceTools._find_statement_tables() for
    locating Balance Sheet, P&L, and Cash Flow tables — this class only adds
    NUMERIC PARSING and LABEL MATCHING on top of that existing fetch.
    """

    _NOTE_COL_PATTERN = re.compile(r"^note\.?s?( no\.?)?$", re.IGNORECASE)
    # How many leading pipe-rows to inspect when looking for the real header.
    _HEADER_SCAN_DEPTH = 5

    STATEMENT_BALANCE_SHEET = "Balance Sheet"
    STATEMENT_PROFIT_LOSS = "Statement of Profit and Loss"
    STATEMENT_CASH_FLOW = "Cash Flow Statement"

    BALANCE_SHEET_LABELS = {
        "total_assets": ["total assets"],
        "total_equity": ["total equity"],
        # The balancing side of the balance sheet. Measured on the live corpus:
        # available on 69% of documents vs only 26% for a bare "Total liabilities"
        # row — so the BS-equation tie-out checks total_assets against THIS.
        "total_equity_and_liabilities": ["total equity and liabilities", "total equity & liabilities"],
        "total_liabilities": ["total liabilities"],
        # Ind AS 114 entities (electricity distribution, oil marketing) carry
        # "Regulatory deferral account balances" BELOW the ordinary asset total,
        # so "Total assets" alone does not balance against equity+liabilities —
        # this fuller caption does. Captured separately rather than folded into
        # total_assets so the tie-out can explain the difference instead of
        # silently selecting whichever figure happens to balance.
        "total_assets_incl_regulatory": [
            "total assets and regulatory", "total assets including regulatory",
        ],
        "current_assets_total_direct": ["total current assets"],
        "current_liabilities_total_direct": ["total current liabilities"],
        "inventories": ["inventories"],
        "cash_and_bank": ["cash and cash equivalents", "cash & bank"],
        "current_investments": ["current investments"],
        "trade_receivables": ["trade receivables"],
        "other_current_assets_prepaid": ["prepaid expenses"],
        "equity_share_capital": ["equity share capital"],
        "other_equity": ["other equity", "reserves and surplus"],
        "preference_share_capital": ["preference share capital"],
        "borrowings": ["borrowings"],  # section-scoped — see _extract_borrowings
        # property_plant_equipment is NOT looked up here — see _extract_ppe.
        # A plain substring match lands on "Other Property, Plant and Equipment"
        # when the real PPE line is a valueless heading with sub-lines.
        # trade_payables is NOT looked up via this direct map — see _extract_trade_payables
    }

    PROFIT_LOSS_LABELS = {
        "profit_before_tax": [
            "profit before tax", "profit before taxes", "profit before the taxes",
            "profit before income tax",
        ],
        # Distinct figure from profit_before_tax — a P&L that reports both shows
        # PBT-before-exceptional first, then exceptional items, then true PBT.
        # Conflating them silently overstates/understates PBT-derived ratios.
        "profit_before_exceptional_and_tax": [
            "profit before exceptional item", "profit before extraordinary item",
            "profit before exceptional and extraordinary item",
        ],
        "profit_for_period": ["profit for the year", "profit for the period"],
        "finance_costs": ["finance costs", "finance cost"],
        "depreciation_amortisation": ["depreciation"],
        "preference_dividend": ["preference dividend"],
        "revenue_from_operations": ["revenue from operations", "sale of products", "sale of product"],
        "other_income": ["other income"],
        "total_expenses": ["total expenses", "total expense"],
        "cost_of_materials_consumed": ["cost of materials consumed"],
        "purchases_of_stock_in_trade": ["purchase of stock-in-trade", "purchases of stock-in-trade"],
        "changes_in_inventories": ["changes in inventories"],
        # tax_expense / eps_basic / eps_diluted are NOT in this flat map — on real
        # filed statements they are a valueless HEADING followed by sub-rows
        # ("Tax expense:" -> "Current tax", "Deferred tax"; "Earnings per equity
        # share:" -> "Basic", "Diluted"). See _extract_tax_expense / _extract_eps.
    }

    # Terms that must NOT appear in a matched row for the given figure key.
    # Guards the substring matcher against landing on a neighbouring line item
    # that merely contains the pattern. See _row_matches.
    LABEL_EXCLUSIONS = {
        # "total equity" is a substring of "Total equity and liabilities" — without
        # this the equity figure silently picks up the balance-sheet total.
        "total_equity": ["and liabilities", "& liabilities"],
        # Keep the plain asset total plain; the regulatory-inclusive caption is
        # captured by total_assets_incl_regulatory instead.
        "total_assets": ["regulatory"],
        "profit_before_tax": [
            "exceptional", "extraordinary", "discontinued", "share of profit",
            "share of net profit", "associate", "joint venture",
            "regulatory deferral", "before share of",
        ],
        "profit_for_period": ["discontinued", "attributable", "per share"],
        "revenue_from_operations": ["other operating"],
        "other_income": ["comprehensive"],
        "depreciation_amortisation": ["accumulated"],
    }

    # Rows that look like the target but belong to a different reporting scope.
    _DISCONTINUED_EXCLUDE = ["discontinued", "discontinuing"]

    CASH_FLOW_LABELS = {
        "loan_repayment": ["repayment of non-current borrowing", "repayment of borrowing", "repayment of loan"],
        # Added for the cash-flow tie-out and the going-concern indicator screen.
        "cash_from_operating": [
            "net cash from operating", "net cash generated from operating",
            "net cash flow from operating", "cash generated from operating",
            "net cash inflow from operating", "net cash used in operating",
        ],
        # Closing-cash captions vary widely ("...at the end of the year",
        # "Closing cash & cash equivalents", "Cash and cash equivalents (closing)",
        # "... - closing balance"). Within a cash-flow statement these phrases only
        # ever denote closing cash, so the broad forms are safe here.
        "cash_at_end": [
            "at the end", "at the close", "closing cash", "(closing)", "closing balance",
        ],
    }

    _NON_CURRENT_LIAB_HEADING = ["(1) non-current liabilities", "non-current liabilities"]
    _CURRENT_LIAB_HEADING = ["(2) current liabilities", "current liabilities"]
    _NON_CURRENT_ASSETS_HEADING = ["(1) non-current assets", "non-current assets"]
    _CURRENT_ASSETS_HEADING = ["(2) current assets", "current assets"]

    # ------------------------------------------------------------------
    # Markdown table parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_label_col_count(header: list[str]) -> int:
        """Returns 2 if header[1] looks like "Particulars", else 1."""
        if len(header) >= 2 and "particular" in header[1].strip().lower():
            return 2
        return 1

    @staticmethod
    def _parse_single_table_block(raw_lines: list[list[str]]) -> list[Row]:
        """Parse the pipe-rows of ONE markdown table (own header, own Note-column position)."""
        if not raw_lines:
            return []

        # The first pipe-row is frequently a units caption spanning the table
        # ("|  | Amount in ₹ Crore |") rather than the real column header, so
        # taking raw_lines[0] blindly leaves the Note column undetected — and
        # note numbers then get parsed as if they were the figures (verified:
        # NALCO 2024-25 read tax expense as 36.0, its note number).
        # Prefer the first leading row that actually carries a Note marker.
        header = raw_lines[0]
        for candidate in raw_lines[:RatioExtractionEngine._HEADER_SCAN_DEPTH]:
            if any(RatioExtractionEngine._NOTE_COL_PATTERN.match(c.strip()) for c in candidate):
                header = candidate
                break

        label_col_count = RatioExtractionEngine._detect_label_col_count(header)

        note_col_idx = None
        header_value_cells = header[label_col_count:]
        for i, cell in enumerate(header_value_cells):
            if RatioExtractionEngine._NOTE_COL_PATTERN.match(cell):
                note_col_idx = i
                break

        rows = []
        for cells in raw_lines:
            label = cells[label_col_count - 1] if len(cells) >= label_col_count else (cells[0] if cells else "")
            value_cells = cells[label_col_count:]
            if note_col_idx is not None and 0 <= note_col_idx < len(value_cells):
                value_cells = value_cells[:note_col_idx] + value_cells[note_col_idx + 1:]
            values = [_re_parse_number(c) for c in value_cells]
            if not label and all(v is None for v in values):
                continue
            rows.append(Row(label, values))
        return rows

    @staticmethod
    def parse_table_md(combined_text: str) -> list[Row]:
        """
        Parse the (possibly multi-block) text returned by
        ComplianceTools._find_statement_tables() into Row objects. Detects
        and DROPS a "Note"/"Note No."/"Notes" column per block if present.
        """
        all_rows: list[Row] = []
        current_block: list[list[str]] = []

        def _flush():
            if current_block:
                all_rows.extend(RatioExtractionEngine._parse_single_table_block(current_block))
                current_block.clear()

        for line in combined_text.splitlines():
            line = line.strip()
            if line.startswith("[table_id:"):
                _flush()
                continue
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or (cells[0] and set(cells[0]) <= {"-", " ", ":"}):
                continue  # separator row
            current_block.append(cells)
        _flush()

        return all_rows

    @staticmethod
    def _first_value(row: Row) -> float | None:
        for v in row.values:
            if v is not None:
                return v
        return None

    @staticmethod
    def _second_value(row: Row) -> float | None:
        """The prior-year comparative column (e.g. "As at 31/03/2022" alongside
        "As at 31/03/2023")."""
        return row.values[1] if len(row.values) > 1 else None

    # ------------------------------------------------------------------
    # Label matching
    # ------------------------------------------------------------------

    @staticmethod
    def _row_matches(row: Row, patterns: list[str], exclude: list[str] | None = None) -> bool:
        """
        Substring match against EITHER the cleaned label or its normalised form
        (see _re_normalise_label), rejected if any `exclude` term is present in
        either form.

        `exclude` is what stops a substring pattern landing on a different line
        item that merely contains it — e.g. "profit before tax" must not match
        "Profit before exceptional items and tax" (a genuinely different figure)
        or "Profit before tax from discontinued operations".
        """
        if exclude and any(x in row.label or x in row.norm for x in exclude):
            return False
        return any(p in row.label or p in row.norm for p in patterns)

    @staticmethod
    def _find_row(
        rows: list[Row], patterns: list[str], start: int = 0, end: int | None = None,
        exclude: list[str] | None = None,
    ) -> tuple[int, Row] | None:
        """First row (within [start:end)) whose label matches any pattern (substring, case-insensitive)."""
        end = len(rows) if end is None else end
        for i in range(start, end):
            if RatioExtractionEngine._row_matches(rows[i], patterns, exclude):
                return i, rows[i]
        return None

    @staticmethod
    def _find_row_with_value(
        rows: list[Row], patterns: list[str], start: int = 0, end: int | None = None,
        exclude: list[str] | None = None,
    ) -> tuple[int, Row] | None:
        """Like _find_row, but skips a matching row that has no parseable value and
        keeps searching for a later match that does."""
        end = len(rows) if end is None else end

        # Pass 1 — EXACT label match. A statement can contain several rows whose
        # labels merely CONTAIN the pattern, often from an unrelated schedule that
        # got pulled in alongside the face statement (verified: IRCON 2023-24's
        # balance-sheet fetch leads with a segment schedule whose "Total assets (A)"
        # row is 332.61, while the real "Total assets" is 14,084.13). Preferring an
        # exact hit picks the face-statement row instead of whichever came first.
        for i in range(start, end):
            row = rows[i]
            if exclude and any(x in row.label or x in row.norm for x in exclude):
                continue
            if any(p == row.label or p == row.norm for p in patterns):
                if RatioExtractionEngine._first_value(row) is not None:
                    return i, row

        # Pass 2 — substring match, skipping valueless rows.
        fallback = None
        for i in range(start, end):
            if RatioExtractionEngine._row_matches(rows[i], patterns, exclude):
                if fallback is None:
                    fallback = (i, rows[i])
                if RatioExtractionEngine._first_value(rows[i]) is not None:
                    return i, rows[i]
        return fallback

    @staticmethod
    def _find_subtotal_before(rows: list[Row], anchor_patterns: list[str]) -> tuple[float | None, "Source | None"]:
        """The blank-label numeric row immediately preceding a row matching anchor_patterns."""
        anchor = RatioExtractionEngine._find_row(rows, anchor_patterns)
        if anchor is None:
            return None, None
        idx, anchor_row = anchor
        if idx == 0:
            return None, None
        prev = rows[idx - 1]
        if prev.label == "":
            value = RatioExtractionEngine._first_value(prev)
            if value is None:
                return None, None
            source = Source(
                statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET,
                label="(unlabeled subtotal row)",
                note=f"row immediately preceding '{anchor_row.raw_label.strip()}'",
            )
            return value, source
        return None, None

    @staticmethod
    def _find_heading_row(rows: list[Row], patterns: list[str], start: int = 0) -> tuple[int, Row] | None:
        """Like _find_row, but matches a WHOLE-LABEL heading (startswith/equality)."""
        for i in range(start, len(rows)):
            label = rows[i].label
            for p in patterns:
                if label == p or label.startswith(p):
                    return i, rows[i]
        return None

    @staticmethod
    def _section_bounds(rows: list[Row], heading_patterns: list[str]) -> tuple[int, int] | None:
        """(start, end) index range of rows belonging to a section."""
        found = RatioExtractionEngine._find_heading_row(rows, heading_patterns)
        if found is None:
            return None
        start, _ = found
        for i in range(start + 1, len(rows)):
            label = rows[i].label
            if label.startswith("(1)") or label.startswith("(2)") or "total " in label:
                return start + 1, i
        return start + 1, len(rows)

    @staticmethod
    def _extract_direct(
        rows: list[Row], label_map: dict[str, list[str]], statement_name: str
    ) -> tuple[dict[str, float | None], dict[str, "Source"]]:
        values, sources = {}, {}
        for key, patterns in label_map.items():
            found = RatioExtractionEngine._find_row_with_value(
                rows, patterns, exclude=RatioExtractionEngine.LABEL_EXCLUSIONS.get(key)
            )
            if found is not None and RatioExtractionEngine._first_value(found[1]) is not None:
                values[key] = RatioExtractionEngine._first_value(found[1])
                sources[key] = Source(statement=statement_name, label=found[1].raw_label.strip())
            else:
                values[key] = None
        return values, sources

    @staticmethod
    def _average(rows: list[Row], patterns: list[str], statement_name: str) -> tuple[float | None, bool, "Source | None"]:
        """Average of current + prior-year columns of the first matching row."""
        found = RatioExtractionEngine._find_row_with_value(rows, patterns)
        if found is None:
            return None, False, None
        current = RatioExtractionEngine._first_value(found[1])
        if current is None:
            return None, False, None
        label = found[1].raw_label.strip()
        prior = RatioExtractionEngine._second_value(found[1])
        if prior is None:
            return current, True, Source(
                statement=statement_name, label=label,
                note="current-year closing balance only — no prior-year comparative column found",
            )
        return (current + prior) / 2, False, Source(
            statement=statement_name, label=label,
            note="average of the current-year and prior-year comparative columns",
        )

    @staticmethod
    def _extract_borrowings(rows: list[Row]) -> tuple[float | None, float | None, "Source | None", "Source | None"]:
        """(long_term_borrowings, short_term_borrowings, long_source, short_source), section-scoped."""
        nc_bounds = RatioExtractionEngine._section_bounds(rows, RatioExtractionEngine._NON_CURRENT_LIAB_HEADING)
        c_bounds = RatioExtractionEngine._section_bounds(rows, RatioExtractionEngine._CURRENT_LIAB_HEADING)

        long_term, long_source = None, None
        if nc_bounds:
            found = RatioExtractionEngine._find_row(rows, ["borrowings"], *nc_bounds)
            if found:
                long_term = RatioExtractionEngine._first_value(found[1])
                long_source = Source(
                    statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET, label=found[1].raw_label.strip(),
                    note="within the Non-current liabilities section",
                )

        short_term, short_source = None, None
        if c_bounds:
            found = RatioExtractionEngine._find_row(rows, ["borrowings"], *c_bounds)
            if found:
                short_term = RatioExtractionEngine._first_value(found[1])
                short_source = Source(
                    statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET, label=found[1].raw_label.strip(),
                    note="within the Current liabilities section",
                )

        return long_term, short_term, long_source, short_source

    # Sub-lines that roll up into Property, Plant and Equipment when a company
    # presents PPE as a heading rather than a single total. Capital work in
    # progress is deliberately EXCLUDED — Schedule III presents it as its own
    # line item, not part of PPE.
    _PPE_COMPONENT_PATTERNS = [
        "tangible", "other property, plant and equipment",
        "right-of-use", "right of use", "rou asset",
    ]
    _PPE_HEADING = ["property, plant and equipment", "property plant and equipment"]

    @staticmethod
    def _extract_ppe(rows: list[Row]) -> tuple[float | None, "Source | None"]:
        """
        (total_ppe, source) from the face of the balance sheet.

        Many companies present PPE as a single valued row. Others — ONGC among
        them — present it as a VALUELESS HEADING with sub-lines:

            Property, Plant and Equipment          <- no value
              Oil and Gas Assets
                Tangible                            1,483,525.09
                Intangible                              3,292.16
              Other Property, Plant and Equipment     133,331.05
              Right-of-Use Assets                     279,116.50

        A plain substring match skips the empty heading and returns the next row
        containing the phrase — "Other Property, Plant and Equipment" — so total
        PPE came back as 133,331.05 instead of ~1,895,972.64, a sub-component
        reported as the whole. Same failure class as the total_equity /
        total_equity_and_liabilities collision.

        Strategy: take a directly-valued PPE row if one exists; otherwise sum the
        component sub-lines beneath the heading, stopping at the next top-level
        item so Capital Work in Progress is not swept in.
        """
        # Step 1 must not settle for the "Other PPE" sub-line when looking for a
        # single total. Step 2 must include it — it is a genuine component.
        direct_exclude = ["other property", "capital work", "under development", "intangible"]
        # "tangible" is a substring of "intangible", so an intangible-assets row
        # would otherwise be summed into PPE. Schedule III presents intangibles as
        # their own line item, separate from PPE, so they are excluded.
        component_exclude = ["intangible", "under development", "capital work"]

        # 1. A single valued "Property, Plant and Equipment" row.
        direct = RatioExtractionEngine._find_row_with_value(
            rows, RatioExtractionEngine._PPE_HEADING, exclude=direct_exclude
        )
        if direct and RatioExtractionEngine._first_value(direct[1]) is not None:
            return RatioExtractionEngine._first_value(direct[1]), Source(
                statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET,
                label=direct[1].raw_label.strip(),
            )

        # 2. Heading + sub-lines. Scan forward to the next unrelated top-level row.
        heading = RatioExtractionEngine._find_row(rows, RatioExtractionEngine._PPE_HEADING)
        if heading is None:
            return None, None
        start = heading[0] + 1

        total, parts = 0.0, []
        for i in range(start, len(rows)):
            row = rows[i]
            if RatioExtractionEngine._row_matches(row, ["capital work", "total ", "(2)"]):
                break
            value = RatioExtractionEngine._first_value(row)
            if value is None:
                continue
            if RatioExtractionEngine._row_matches(
                row, RatioExtractionEngine._PPE_COMPONENT_PATTERNS, exclude=component_exclude
            ):
                total += value
                parts.append(row.raw_label.strip())

        if not parts:
            return None, None
        return total, Source(
            statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET,
            label=heading[1].raw_label.strip(),
            note="summed the sub-lines beneath this heading: " + "; ".join(parts),
        )

    @staticmethod
    def _sum_section_rows(
        rows: list[Row], patterns: list[str], bounds: tuple[int, int] | None, section_note: str
    ) -> tuple[float | None, "Source | None"]:
        """Sum every row within `bounds` whose label matches any of `patterns`."""
        if bounds is None:
            return None, None
        start, end = bounds
        total = 0.0
        found_any = False
        labels_used = []
        for i in range(start, end):
            for p in patterns:
                if p in rows[i].label:
                    val = RatioExtractionEngine._first_value(rows[i])
                    if val is not None:
                        total += val
                        found_any = True
                        labels_used.append(rows[i].raw_label.strip())
                    break
        if not found_any:
            return None, None
        return total, Source(
            statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET,
            label="; ".join(labels_used),
            note=section_note,
        )

    @staticmethod
    def _extract_investments(rows: list[Row]) -> tuple[float | None, float | None, "Source | None", "Source | None"]:
        """(non_current_investments, current_investments, nc_source, c_source), section-scoped and summed."""
        nc_bounds = RatioExtractionEngine._section_bounds(rows, RatioExtractionEngine._NON_CURRENT_ASSETS_HEADING)
        c_bounds = RatioExtractionEngine._section_bounds(rows, RatioExtractionEngine._CURRENT_ASSETS_HEADING)
        non_current, nc_source = RatioExtractionEngine._sum_section_rows(
            rows, ["investments"], nc_bounds, "within the Non-current assets section"
        )
        current, c_source = RatioExtractionEngine._sum_section_rows(
            rows, ["investments"], c_bounds, "within the Current assets section"
        )
        return non_current, current, nc_source, c_source

    @staticmethod
    def _extract_trade_payables(rows: list[Row]) -> tuple[float | None, float | None, "Source | None"]:
        """(current, prior, source) Trade Payables — direct row, or summed MSME/non-MSME sub-rows."""
        found = RatioExtractionEngine._find_row_with_value(rows, ["trade payables"])
        if found is None:
            return None, None, None
        idx, row = found
        direct_current = RatioExtractionEngine._first_value(row)
        if direct_current is not None:
            return direct_current, RatioExtractionEngine._second_value(row), Source(
                statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET, label=row.raw_label.strip(),
            )
        current_total, prior_total = 0.0, 0.0
        found_current, found_prior = False, False
        for i in range(idx + 1, len(rows)):
            if rows[i].raw_label.strip().startswith("("):
                break
            v_cur, v_pri = RatioExtractionEngine._first_value(rows[i]), RatioExtractionEngine._second_value(rows[i])
            if v_cur is not None:
                current_total += v_cur
                found_current = True
            if v_pri is not None:
                prior_total += v_pri
                found_prior = True
        if not found_current:
            return None, None, None
        source = Source(
            statement=RatioExtractionEngine.STATEMENT_BALANCE_SHEET, label=row.raw_label.strip(),
            note="summed the MSME/non-MSME sub-rows beneath this heading",
        )
        return current_total, (prior_total if found_prior else None), source

    # How far past a valueless heading to scan for its sub-rows. Real P&Ls put
    # 2-4 sub-rows under "Tax expense:" / "Earnings per equity share:".
    _SUBROW_WINDOW = 6

    # Line items that mark the END of the tax-expense block on a P&L.
    _POST_TAX_BOUNDARY = [
        "profit for the", "profit after tax", "net profit", "other comprehensive",
        "loss for the", "profit / (loss) for the",
    ]

    @staticmethod
    def _extract_tax_expense(rows: list[Row]) -> tuple[float | None, "Source | None"]:
        """
        (value, source) TOTAL tax expense.

        The tax block is nested and its depth varies by company, so this reads the
        whole block between the "Tax expense" heading and the next major line item
        rather than looking for fixed captions. Verified shapes:

            NALCO 2024-25                     NMDC 2018-19
            VIII Tax expense                  Tax expense :
              Current tax        (no value)     (1) Current year   2,752.70
                Current year      1,858.73      (2) Earlier years      0.85
                Earlier years         1.98      (3) Deferred tax    -197.02
              Deferred tax        -50.28        <unlabelled total>  2,556.53

        Resolution order inside the block:
          1. an explicit "Total tax expense" row;
          2. a row whose label contains "total" (a labelled subtotal);
          3. a trailing UNLABELLED row — the subtotal NMDC-style statements use;
          4. otherwise sum every valued row in the block.
        Discontinued-operations tax is always excluded.
        """
        exclude = RatioExtractionEngine._DISCONTINUED_EXCLUDE

        direct = RatioExtractionEngine._find_row_with_value(
            rows, ["total tax expense", "total tax expenses"], exclude=exclude
        )
        if direct is not None and RatioExtractionEngine._first_value(direct[1]) is not None:
            return RatioExtractionEngine._first_value(direct[1]), Source(
                statement=RatioExtractionEngine.STATEMENT_PROFIT_LOSS,
                label=direct[1].raw_label.strip(),
            )

        heading = RatioExtractionEngine._find_row(rows, ["tax expense", "tax expenses"], exclude=exclude)
        if heading is None:
            return None, None

        start = heading[0] + 1
        end = min(start + RatioExtractionEngine._SUBROW_WINDOW, len(rows))
        for i in range(start, end):
            if RatioExtractionEngine._row_matches(rows[i], RatioExtractionEngine._POST_TAX_BOUNDARY):
                end = i
                break

        valued = [
            (i, rows[i]) for i in range(start, end)
            if RatioExtractionEngine._first_value(rows[i]) is not None
            and not any(x in rows[i].label for x in exclude)
        ]
        if not valued:
            return None, None

        heading_label = heading[1].raw_label.strip()

        # A labelled total inside the block — use it rather than summing, or the
        # components would be double-counted alongside it.
        for _i, row in valued:
            if "total" in row.label:
                return RatioExtractionEngine._first_value(row), Source(
                    statement=RatioExtractionEngine.STATEMENT_PROFIT_LOSS,
                    label=row.raw_label.strip(),
                )

        # A trailing unlabelled row is the block's own subtotal.
        last_row = valued[-1][1]
        if last_row.label == "":
            return RatioExtractionEngine._first_value(last_row), Source(
                statement=RatioExtractionEngine.STATEMENT_PROFIT_LOSS,
                label=heading_label,
                note="unlabelled subtotal row closing the tax-expense block",
            )

        total = sum(RatioExtractionEngine._first_value(r) for _i, r in valued)
        return total, Source(
            statement=RatioExtractionEngine.STATEMENT_PROFIT_LOSS,
            label=heading_label,
            note="summed the sub-rows: " + "; ".join(r.raw_label.strip() for _i, r in valued),
        )

    @staticmethod
    def _extract_eps(rows: list[Row]) -> tuple[float | None, float | None, "Source | None", "Source | None"]:
        """
        (basic, diluted, basic_source, diluted_source) Earnings per share.

        Usually a valueless "Earnings per equity share:" heading followed by bare
        "Basic" / "Diluted" rows; sometimes a single self-describing row
        ("Basic earnings per share (₹)"). Continuing-operations EPS is preferred —
        discontinued-operations blocks are skipped.
        """
        exclude = RatioExtractionEngine._DISCONTINUED_EXCLUDE

        def _direct(kind: str) -> tuple[float | None, "Source | None"]:
            found = RatioExtractionEngine._find_row_with_value(
                rows, [f"{kind} earnings per", f"{kind} eps"], exclude=exclude
            )
            if found and RatioExtractionEngine._first_value(found[1]) is not None:
                return RatioExtractionEngine._first_value(found[1]), Source(
                    statement=RatioExtractionEngine.STATEMENT_PROFIT_LOSS,
                    label=found[1].raw_label.strip(),
                )
            return None, None

        basic, basic_src = _direct("basic")
        diluted, diluted_src = _direct("diluted")
        if basic is not None and diluted is not None:
            return basic, diluted, basic_src, diluted_src

        heading = RatioExtractionEngine._find_row(rows, ["earnings per", "earning per"], exclude=exclude)
        if heading is None:
            return basic, diluted, basic_src, diluted_src

        start = heading[0] + 1
        end = min(start + RatioExtractionEngine._SUBROW_WINDOW, len(rows))
        for i in range(start, end):
            row = rows[i]
            value = RatioExtractionEngine._first_value(row)
            if value is None or any(x in row.label for x in exclude):
                continue
            src = Source(
                statement=RatioExtractionEngine.STATEMENT_PROFIT_LOSS,
                label=row.raw_label.strip(),
                note=f"sub-row beneath '{heading[1].raw_label.strip()}'",
            )
            if basic is None and RatioExtractionEngine._row_matches(row, ["basic"]):
                basic, basic_src = value, src
            elif diluted is None and RatioExtractionEngine._row_matches(row, ["diluted"]):
                diluted, diluted_src = value, src

        return basic, diluted, basic_src, diluted_src

    # ------------------------------------------------------------------
    # Top-level: extract every figure Phase-1/Phase-2 ratios need
    # ------------------------------------------------------------------

    @staticmethod
    def _sanity_check_total(
        figures: dict[str, float | None], total_key: str, component_keys: list[str], inconsistent: set
    ) -> None:
        """A "total" must be >= any single component that rolls up into it."""
        total = figures.get(total_key)
        if total is None:
            return
        known = [figures.get(k) for k in component_keys]
        largest_component = max((v for v in known if v is not None), default=None)
        if largest_component is not None and total < largest_component * 0.99:
            figures[total_key] = None
            inconsistent.add(total_key)

    @staticmethod
    def extract_all_figures(doc_id: str, conn_reports) -> dict[str, float | None]:
        """
        Fetches Balance Sheet, P&L, and Cash Flow (reusing
        ComplianceTools._find_statement_tables) and resolves every figure
        the Phase-1/Phase-2 ratios need. A None value always means "not
        found" — never a guessed zero.
        """
        figures: dict[str, float | None] = {}
        sources: dict[str, Source] = {}
        inconsistent_totals: set = set()
        approximate_averages: set = set()

        def _set_derived(key, value, *component_keys):
            figures[key] = value
            if value is not None:
                sources[key] = Source(derived_from=list(component_keys))

        bs_text = ComplianceTools._find_statement_tables(doc_id, "balance_sheet", conn_reports)
        bs_rows = RatioExtractionEngine.parse_table_md(bs_text) if "No standalone" not in bs_text else []
        bs_values, bs_sources = RatioExtractionEngine._extract_direct(
            bs_rows, RatioExtractionEngine.BALANCE_SHEET_LABELS, RatioExtractionEngine.STATEMENT_BALANCE_SHEET
        )
        figures.update(bs_values)
        sources.update(bs_sources)

        if bs_values.get("current_assets_total_direct") is not None:
            figures["current_assets_total"] = bs_values["current_assets_total_direct"]
            sources["current_assets_total"] = bs_sources["current_assets_total_direct"]
        else:
            figures["current_assets_total"], ca_source = RatioExtractionEngine._find_subtotal_before(bs_rows, ["total assets"])
            if ca_source:
                sources["current_assets_total"] = ca_source

        if bs_values.get("current_liabilities_total_direct") is not None:
            figures["current_liabilities_total"] = bs_values["current_liabilities_total_direct"]
            sources["current_liabilities_total"] = bs_sources["current_liabilities_total_direct"]
        else:
            cl_value, cl_source = RatioExtractionEngine._find_subtotal_before(bs_rows, ["total liabilities"])
            if cl_value is None:
                cl_value, cl_source = RatioExtractionEngine._find_subtotal_before(bs_rows, ["total equity and liabilities"])
            figures["current_liabilities_total"] = cl_value
            if cl_source:
                sources["current_liabilities_total"] = cl_source

        (
            figures["long_term_borrowings"], figures["short_term_borrowings"],
            long_borrow_source, short_borrow_source,
        ) = RatioExtractionEngine._extract_borrowings(bs_rows)
        if long_borrow_source:
            sources["long_term_borrowings"] = long_borrow_source
        if short_borrow_source:
            sources["short_term_borrowings"] = short_borrow_source

        (
            figures["non_current_investments"], figures["current_investments_actual"],
            nc_inv_source, c_inv_source,
        ) = RatioExtractionEngine._extract_investments(bs_rows)
        if nc_inv_source:
            sources["non_current_investments"] = nc_inv_source
        if c_inv_source:
            sources["current_investments_actual"] = c_inv_source

        RatioExtractionEngine._sanity_check_total(
            figures, "current_assets_total",
            ["inventories", "cash_and_bank", "current_investments", "trade_receivables"],
            inconsistent_totals,
        )
        RatioExtractionEngine._sanity_check_total(
            figures, "current_liabilities_total",
            ["short_term_borrowings"],
            inconsistent_totals,
        )

        figures["property_plant_equipment"], ppe_source = RatioExtractionEngine._extract_ppe(bs_rows)
        if ppe_source:
            sources["property_plant_equipment"] = ppe_source

        trade_payables_current, trade_payables_prior, trade_payables_source = RatioExtractionEngine._extract_trade_payables(bs_rows)
        figures["trade_payables"] = trade_payables_current
        if trade_payables_source:
            sources["trade_payables"] = trade_payables_source
        if trade_payables_current is not None and trade_payables_prior is not None:
            figures["average_trade_payables"] = (trade_payables_current + trade_payables_prior) / 2
            if trade_payables_source:
                sources["average_trade_payables"] = Source(
                    statement=trade_payables_source.statement, label=trade_payables_source.label,
                    note="average of the current-year and prior-year comparative columns"
                    + (f" ({trade_payables_source.note})" if trade_payables_source.note else ""),
                )
        elif trade_payables_current is not None:
            figures["average_trade_payables"] = trade_payables_current
            approximate_averages.add("average_trade_payables")
            if trade_payables_source:
                sources["average_trade_payables"] = Source(
                    statement=trade_payables_source.statement, label=trade_payables_source.label,
                    note="current-year closing balance only — no prior-year comparative found",
                )
        else:
            figures["average_trade_payables"] = None

        for fig_key, patterns in (
            ("average_total_assets", ["total assets"]),
            ("average_inventory", ["inventories"]),
            ("average_trade_receivables", ["trade receivables"]),
        ):
            value, is_approx, avg_source = RatioExtractionEngine._average(bs_rows, patterns, RatioExtractionEngine.STATEMENT_BALANCE_SHEET)
            figures[fig_key] = value
            if avg_source:
                sources[fig_key] = avg_source
            if is_approx:
                approximate_averages.add(fig_key)

        pl_text = ComplianceTools._find_statement_tables(doc_id, "profit_loss", conn_reports)
        pl_rows = RatioExtractionEngine.parse_table_md(pl_text) if "No standalone" not in pl_text else []
        pl_values, pl_sources = RatioExtractionEngine._extract_direct(
            pl_rows, RatioExtractionEngine.PROFIT_LOSS_LABELS, RatioExtractionEngine.STATEMENT_PROFIT_LOSS
        )
        figures.update(pl_values)
        sources.update(pl_sources)

        # Heading + sub-row figures (see _extract_tax_expense / _extract_eps).
        figures["tax_expense"], tax_source = RatioExtractionEngine._extract_tax_expense(pl_rows)
        if tax_source:
            sources["tax_expense"] = tax_source

        (
            figures["eps_basic"], figures["eps_diluted"], eps_b_source, eps_d_source,
        ) = RatioExtractionEngine._extract_eps(pl_rows)
        if eps_b_source:
            sources["eps_basic"] = eps_b_source
        if eps_d_source:
            sources["eps_diluted"] = eps_d_source

        cf_text = ComplianceTools._find_statement_tables(doc_id, "cash_flow", conn_reports)
        cf_rows = RatioExtractionEngine.parse_table_md(cf_text) if "No standalone" not in cf_text else []
        cf_values, cf_sources = RatioExtractionEngine._extract_direct(
            cf_rows, RatioExtractionEngine.CASH_FLOW_LABELS, RatioExtractionEngine.STATEMENT_CASH_FLOW
        )
        loan_repayment = cf_values.get("loan_repayment")
        figures["loan_repayment"] = abs(loan_repayment) if loan_repayment is not None else None
        if loan_repayment is not None:
            sources["loan_repayment"] = cf_sources["loan_repayment"]

        # Carry the remaining cash-flow figures through as well. Historically only
        # loan_repayment was copied out of cf_values and every other CASH_FLOW_LABELS
        # entry was silently discarded — which is why newly-added keys read 0%.
        for key in RatioExtractionEngine.CASH_FLOW_LABELS:
            if key == "loan_repayment":
                continue
            figures[key] = cf_values.get(key)
            if cf_sources.get(key):
                sources[key] = cf_sources[key]

        # ---- Derived / compound figures ----
        def _sum(*keys, treat_missing_as_zero=True):
            vals = [figures.get(k) for k in keys]
            if treat_missing_as_zero:
                if all(v is None for v in vals):
                    return None
                return sum(v for v in vals if v is not None)
            if any(v is None for v in vals):
                return None
            return sum(vals)

        def _diff(a, b):
            va, vb = figures.get(a), figures.get(b)
            return va - vb if va is not None and vb is not None else None

        quick_assets = _diff("current_assets_total", "inventories")
        quick_assets_components = ["current_assets_total", "inventories"]
        if quick_assets is not None and figures.get("other_current_assets_prepaid"):
            quick_assets -= figures["other_current_assets_prepaid"]
            quick_assets_components.append("other_current_assets_prepaid")
        _set_derived("quick_assets", quick_assets, *quick_assets_components)

        _set_derived("cash_and_investments", _sum("cash_and_bank", "current_investments"), "cash_and_bank", "current_investments")
        _set_derived("net_assets", _diff("total_assets", "current_liabilities_total"), "total_assets", "current_liabilities_total")

        total_investments_for_capital_employed = _sum(
            "non_current_investments", "current_investments_actual", treat_missing_as_zero=True
        )
        capital_employed = (
            figures["total_assets"] - figures["current_liabilities_total"] - (total_investments_for_capital_employed or 0)
            if figures.get("total_assets") is not None and figures.get("current_liabilities_total") is not None
            else None
        )
        _set_derived(
            "capital_employed", capital_employed,
            "total_assets", "current_liabilities_total", "non_current_investments", "current_investments_actual",
        )
        _set_derived("total_borrowings", _sum("long_term_borrowings", "short_term_borrowings"), "long_term_borrowings", "short_term_borrowings")
        _set_derived(
            "total_equity_with_pref", _sum("equity_share_capital", "preference_share_capital", "other_equity"),
            "equity_share_capital", "preference_share_capital", "other_equity",
        )
        figures["capital_gearing_numerator"] = figures.get("total_borrowings")
        if figures["capital_gearing_numerator"] is not None:
            sources["capital_gearing_numerator"] = Source(derived_from=["total_borrowings"])

        net_working_capital = _diff("current_assets_total", "current_liabilities_total")
        nwc_components = ["current_assets_total", "current_liabilities_total"]
        if net_working_capital is not None and figures.get("short_term_borrowings"):
            net_working_capital += figures["short_term_borrowings"]
            nwc_components.append("short_term_borrowings")
        _set_derived("net_working_capital", net_working_capital, *nwc_components)

        _set_derived("ebit", _sum("profit_before_tax", "finance_costs", treat_missing_as_zero=False), "profit_before_tax", "finance_costs")
        _set_derived("ebit_plus_depreciation", _sum("ebit", "depreciation_amortisation", treat_missing_as_zero=False), "ebit", "depreciation_amortisation")
        _set_derived(
            "earnings_available_for_debt_service",
            _sum("profit_for_period", "depreciation_amortisation", "finance_costs", treat_missing_as_zero=False),
            "profit_for_period", "depreciation_amortisation", "finance_costs",
        )
        _set_derived(
            "finance_costs_plus_instalments", _sum("finance_costs", "loan_repayment", treat_missing_as_zero=False),
            "finance_costs", "loan_repayment",
        )

        # ---- Phase 2 derived figures ----
        _set_derived(
            "cost_of_materials_used_total",
            _sum("cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories", treat_missing_as_zero=True),
            "cost_of_materials_consumed", "purchases_of_stock_in_trade", "changes_in_inventories",
        )
        _set_derived("gross_profit", _diff("revenue_from_operations", "cost_of_materials_used_total"), "revenue_from_operations", "cost_of_materials_used_total")
        _set_derived("working_capital_plain", _diff("current_assets_total", "current_liabilities_total"), "current_assets_total", "current_liabilities_total")
        _set_derived(
            "profit_for_period_plus_finance_costs", _sum("profit_for_period", "finance_costs", treat_missing_as_zero=False),
            "profit_for_period", "finance_costs",
        )
        profit_less_pref_div = (
            figures["profit_for_period"] - (figures.get("preference_dividend") or 0)
            if figures.get("profit_for_period") is not None
            else None
        )
        _set_derived("profit_for_period_less_pref_div", profit_less_pref_div, "profit_for_period", "preference_dividend")
        daily_opex = (
            (figures["total_expenses"] - figures["finance_costs"]) / 365
            if figures.get("total_expenses") is not None and figures.get("finance_costs") is not None
            else None
        )
        _set_derived("daily_operating_expenses", daily_opex, "total_expenses", "finance_costs")
        _set_derived(
            "quick_assets_plus_receivables",
            _sum("cash_and_bank", "current_investments", "trade_receivables", treat_missing_as_zero=True),
            "cash_and_bank", "current_investments", "trade_receivables",
        )

        figures["_inconsistent_totals"] = inconsistent_totals
        figures["_approximate_averages"] = approximate_averages
        figures["_sources"] = sources
        return figures


# ===== SECTION 10: RatioTools =====

class RatioTools:
    """
    Financial ratio analysis tool executor. Migrated from ratio_tools.py.
    NEVER hands the LLM raw numbers to do arithmetic on — every ratio's
    value is computed here and handed to the LLM as a finished, citable
    number.
    """

    _COMPLETENESS_DISCLAIMER = (
        "NOTE: All ratio values below were computed by deterministic Python "
        "arithmetic directly from the figures extracted from the company's "
        "actual filed statements — never estimated or computed by the language "
        "model. A ratio marked 'cannot compute' means a required figure was not "
        "found on the face of the statement (a genuine disclosure gap for that "
        "company), not a computation error. Cost of Materials Consumed, "
        "Purchases of Stock-in-Trade, and Changes in Inventories are each "
        "treated as 0 when not separately disclosed on the face Statement of "
        "Profit and Loss (some companies bundle these into a different, "
        "industry-specific expense caption) — this affects Gross Profit Ratio "
        "and the Cost of Materials Consumed Ratio (within Expense Ratios)."
    )

    _EXPENSE_RATIO_HEADS = [
        ("Finance Cost Ratio", "finance_costs"),
        ("Depreciation & Amortisation Ratio", "depreciation_amortisation"),
        ("Cost of Materials Consumed Ratio", "cost_of_materials_used_total"),
    ]

    @staticmethod
    def _format_value(v: float | None) -> str:
        return "N/A" if v is None else f"{v:,.4f}"

    @staticmethod
    def _cite(key: str, sources: dict[str, "Source"], seen: frozenset = frozenset()) -> str:
        """Recursively expand a figure's provenance into a citation string."""
        if key in seen:
            return key  # defensive: never recurse into a cycle
        src = sources.get(key)
        if src is None:
            return f"{key} (not disclosed on the face statement — contributed 0 to this figure)"
        if src.derived_from:
            parts = [RatioTools._cite(k, sources, seen | {key}) for k in src.derived_from]
            return f"{key} — derived from: " + "; ".join(parts)
        citation = f"{src.statement}, row: '{src.label}'"
        if src.note:
            citation += f" [{src.note}]"
        return citation

    @staticmethod
    def _missing_reason(key: str, figures: dict[str, float | None]) -> str:
        if key in figures.get("_inconsistent_totals", set()):
            return f"{key} (source table inconsistency detected — value discarded, not used)"
        return key

    @staticmethod
    def _compute_ratio(ratio_id: str, figures: dict[str, float | None]) -> dict:
        definition = RatioReference.RATIOS[ratio_id]

        if definition.get("is_difference"):
            value_key = definition["components"]["value"]
            value = figures.get(value_key)
            if value is None:
                return {"ratio_id": ratio_id, "error": f"cannot compute — missing: {RatioTools._missing_reason(value_key, figures)}"}
            return {
                "ratio_id": ratio_id,
                "name": definition["name"],
                "value": value,
                "formula_used": definition["formula_text"],
                "figures_used": {value_key: figures.get(value_key)},
                "implication_note": (
                    definition["implication_higher"] if value >= 0 else definition["implication_lower"]
                ),
                "worked_calculation": (
                    f"{RatioTools._format_value(value)} — computed directly during figure extraction "
                    f"(a sum/difference of statement line items; see citation below for its inputs)"
                ),
            }

        num_key = definition["components"]["numerator"]
        den_key = definition["components"]["denominator"]
        numerator = figures.get(num_key)
        denominator = figures.get(den_key)

        if definition.get("not_applicable_if_denominator_zero") and (denominator is None or denominator == 0):
            return {"ratio_id": ratio_id, "name": definition["name"], "not_applicable": True,
                    "note": "Preference dividend not disclosed/zero — ratio not applicable for this company."}

        missing = []
        if numerator is None:
            missing.append(RatioTools._missing_reason(num_key, figures))
        if denominator is None:
            missing.append(RatioTools._missing_reason(den_key, figures))
        if denominator == 0:
            return {"ratio_id": ratio_id, "error": "cannot compute — denominator is zero"}
        if missing:
            return {"ratio_id": ratio_id, "error": f"cannot compute — missing: {', '.join(missing)}"}

        value = numerator / denominator
        is_percentage = definition.get("is_percentage")
        value_str = f"{value * 100:,.2f}%" if is_percentage else f"{value:,.4f}"
        worked = (
            f"{RatioTools._format_value(numerator)} / {RatioTools._format_value(denominator)}"
            + (" x 100" if is_percentage else "")
            + f" = {value_str}"
        )
        result = {
            "ratio_id": ratio_id,
            "name": definition["name"],
            "value": value,
            "formula_used": definition["formula_text"],
            "figures_used": {num_key: numerator, den_key: denominator},
            "worked_calculation": worked,
        }
        if definition.get("approximate"):
            result["approximation_note"] = definition["approximation_note"]
        approximate_averages = figures.get("_approximate_averages", set())
        avg_caveats = [
            f"{key} uses the current-year closing balance only (no prior-year "
            f"comparative found on this statement for a true two-year average)."
            for key in (num_key, den_key) if key in approximate_averages
        ]
        if avg_caveats:
            result["average_caveat"] = " ".join(avg_caveats)
        return result

    @staticmethod
    def _compute_period_ratio(ratio_id: str, base_result: dict) -> dict:
        """Receivables/Payables Collection/Payment Period = 365 / <turnover ratio's own computed value>."""
        definition = RatioReference.RATIOS[ratio_id]
        base_id = definition["derived_from_ratio"]
        base_name = RatioReference.RATIOS[base_id]["name"]
        if base_result.get("error") or base_result.get("not_applicable"):
            return {"ratio_id": ratio_id, "error": f"cannot compute — {base_name} unavailable"}
        base_value = base_result["value"]
        if base_value == 0:
            return {"ratio_id": ratio_id, "error": f"cannot compute — {base_name} is zero"}
        period = 365 / base_value
        return {
            "ratio_id": ratio_id,
            "name": definition["name"],
            "value": period,
            "formula_used": definition["formula_text"],
            "figures_used": dict(base_result["figures_used"]),
            "worked_calculation": f"365 / {RatioTools._format_value(base_value)} = {RatioTools._format_value(period)}",
        }

    @staticmethod
    def _compute_expense_ratio_breakdown(figures: dict[str, float | None]) -> list[str]:
        """"Expense Ratios" is a formula family, not one number."""
        revenue = figures.get("revenue_from_operations")
        sources = figures.get("_sources", {})
        if revenue is None or revenue == 0:
            return ["- Expense Ratios: cannot compute — missing: revenue_from_operations"]
        lines = []
        for label, key in RatioTools._EXPENSE_RATIO_HEADS:
            value = figures.get(key)
            if value is None:
                lines.append(f"- {label}: cannot compute — missing: {key}")
                continue
            pct = value / revenue * 100
            lines.append(
                f"- {label} = {pct:,.2f}%  (formula: {key} / Revenue from Operations x 100)\n"
                f"    Calculation: {RatioTools._format_value(value)} / {RatioTools._format_value(revenue)} x 100 = {pct:,.2f}%\n"
                f"    Figures used: {key}={RatioTools._format_value(value)}, revenue_from_operations={RatioTools._format_value(revenue)}\n"
                f"    Source — {key}: {RatioTools._cite(key, sources)}\n"
                f"    Source — revenue_from_operations: {RatioTools._cite('revenue_from_operations', sources)}"
            )
        return lines

    @staticmethod
    def _format_ratio_result(r: dict, sources: dict[str, "Source"]) -> str:
        name = RatioReference.RATIOS[r["ratio_id"]]["name"]
        if r.get("error"):
            return f"- {name}: {r['error']}"
        if r.get("not_applicable"):
            return f"- {name}: Not applicable — {r['note']}"
        is_percentage = RatioReference.RATIOS[r["ratio_id"]].get("is_percentage")
        value_str = f"{r['value'] * 100:,.2f}%" if is_percentage else RatioTools._format_value(r["value"])
        lines = [
            f"- {name} = {value_str}  (formula: {r['formula_used']})",
        ]
        if r.get("worked_calculation"):
            lines.append(f"    Calculation: {r['worked_calculation']}")
        lines.append(
            "    Figures used: " + ", ".join(f"{k}={RatioTools._format_value(v)}" for k, v in r["figures_used"].items())
        )
        for k in r["figures_used"]:
            lines.append(f"    Source — {k}: {RatioTools._cite(k, sources)}")
        if r.get("approximation_note"):
            lines.append(f"    APPROXIMATION: {r['approximation_note']}")
        if r.get("average_caveat"):
            lines.append(f"    NOTE: {r['average_caveat']}")
        return "\n".join(lines)

    @staticmethod
    def _compute_and_format(ratio_id: str, figures: dict[str, float | None]) -> str:
        definition = RatioReference.RATIOS[ratio_id]
        sources = figures.get("_sources", {})
        if definition.get("is_breakdown"):
            return "\n".join(RatioTools._compute_expense_ratio_breakdown(figures))
        if definition.get("derived_from_ratio"):
            base_result = RatioTools._compute_ratio(definition["derived_from_ratio"], figures)
            return RatioTools._format_ratio_result(RatioTools._compute_period_ratio(ratio_id, base_result), sources)
        return RatioTools._format_ratio_result(RatioTools._compute_ratio(ratio_id, figures), sources)

    @staticmethod
    def get_ratio_formula(ratio_name: str = "all") -> str:
        """Pure reference lookup — a ratio's formula and higher/lower implications.

        Deliberately excludes any "ideal"/benchmark level: what counts as a
        healthy value for a ratio depends on industry, business model and
        context that this tool has no way to know, so stating one as if it
        were universal would be misleading. The model must not invent or
        infer one either.
        """
        term = (ratio_name or "all").strip().lower()
        if term == "all":
            rids = list(RatioReference.RATIOS.keys())
        elif term in RatioReference.CATEGORIES or term == "market":
            rids = [rid for rid in RatioReference.RATIOS if RatioReference.RATIOS[rid]["category"] == term]
        else:
            term_as_id = term.replace(" ", "_")
            rids = [
                rid for rid in RatioReference.RATIOS
                if term_as_id in rid or term in RatioReference.RATIOS[rid]["name"].lower()
            ]
            if not rids:
                return (
                    f"No ratio found matching '{ratio_name}'. Valid categories: "
                    f"{', '.join(RatioReference.CATEGORIES)}, 'market', or a specific ratio name, or 'all'."
                )

        lines = []
        for rid in rids:
            r = RatioReference.RATIOS[rid]
            status = (
                "computable now by compute_ratio_analysis" if r.get("implemented")
                else "formula known, not yet computable by this tool"
            )
            line = (
                f"- {r['name']} ({r['category']}) — {status}\n"
                f"    Formula: {r['formula_text']}\n"
                f"    If the value is relatively high: {r['implication_higher']}\n"
                f"    If the value is relatively low: {r['implication_lower']}"
            )
            if r.get("approximation_note"):
                line += f"\n    APPROXIMATION (when computed): {r['approximation_note']}"
            lines.append(line)

        return "\n".join(lines) if lines else "(no ratios found)"

    @staticmethod
    def compute_ratio_analysis(company: str, financial_year: str, category: str = "all", conn_reports=None) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            options = "; ".join(
                f"{r['company']} FY{r['fy_start']}-{str(r['fy_end'])[-2:]} ({r['doc_id']})" for r in match[:10]
            )
            return (
                f"Multiple annual reports matched '{company}' / '{financial_year}': {options}. "
                "Please specify the exact company and financial year."
            )

        category = (category or "all").strip().lower()
        if category == "all":
            ratio_ids = list(RatioReference.IMPLEMENTED_RATIOS)
        elif category in RatioReference.CATEGORIES:
            ratio_ids = RatioReference.ratios_in_category(category)
        else:
            category_as_id = category.replace(" ", "_")
            matched = [
                rid for rid in RatioReference.RATIOS
                if category_as_id in rid or category in RatioReference.RATIOS[rid]["name"].lower()
            ]
            if not matched:
                return (
                    f"Unknown category or ratio '{category}'. Valid categories: "
                    f"{', '.join(RatioReference.CATEGORIES)}, or 'all'."
                )
            ratio_ids = matched

        out_of_scope = [rid for rid in ratio_ids if not RatioReference.RATIOS[rid].get("implemented")]
        ratio_ids = [rid for rid in ratio_ids if RatioReference.RATIOS[rid].get("implemented")]

        figures = RatioExtractionEngine.extract_all_figures(match["doc_id"], conn_reports)
        body_lines = [RatioTools._compute_and_format(rid, figures) for rid in ratio_ids]

        header = (
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})\n"
            f"{UnitResolver.units_line(match['doc_id'], conn_reports)}\n"
            "Ratios themselves are unitless unless the row says otherwise — a ratio is shown "
            "as 'times', a percentage as '%', and a days measure as 'days'. The INPUT amounts "
            "behind them carry the unit above.\n"
            f"{RatioTools._COMPLETENESS_DISCLAIMER}\n"
        )

        body = "\n".join(body_lines) if body_lines else "(no ratios computed)"

        footer = ""
        if out_of_scope:
            names = ", ".join(RatioReference.RATIOS[rid]["name"] for rid in out_of_scope)
            footer = (
                f"\n\nNot yet supported by this tool (needs per-share/declared-dividend "
                f"data or market price data not sourced from this database): {names}"
            )

        return header + "\n" + body + footer


# ===== SECTION 11: AuditScheduleReference =====

class AuditScheduleReference:
    """
    Static reference data for the audit-risk-analysis feature: which
    financial-statement schedule maps to which Ind AS standard(s), and what
    note-table title/description keywords locate that schedule's data in
    the reports DB. Migrated from audit_schedule_reference.py.
    """

    SCHEDULES = {
        "ppe": {
            "name": "Property, Plant and Equipment",
            "note_title_patterns": [
                "property, plant and equipment", "tangible", "right of use", "rou assets",
            ],
            "ind_as_standards": [16, 36, 116],
            "topic_keywords": [
                "decommissioning", "restoration", "dismantling", "impairment",
                "cash-generating", "cash generating unit", "right-of-use", "right of use",
                "useful life", "componentization", "component",
            ],
            "verified": True,
        },
        "inventory": {
            "name": "Inventories",
            "note_title_patterns": ["inventories", "inventory"],
            "ind_as_standards": [2],
            "topic_keywords": [
                "net realisable value", "net realizable value", "cost formula",
                "obsolete", "write-down", "write down",
            ],
            "verified": True,
        },
        "investments": {
            "name": "Investments (Financial Assets)",
            "note_title_patterns": [
                "investments in", "aggregate investments", "investment in subsidiaries",
                "investment in associates", "investment in joint venture",
            ],
            "ind_as_standards": [32, 107, 109],
            "topic_keywords": [
                "financial asset", "fair value", "impairment", "expected credit loss",
                "classification", "amortised cost", "amortized cost",
            ],
            "verified": True,
            "data_quality_note": (
                "The note table found for Investments is a category/entity-wise "
                "BALANCE SNAPSHOT, not a movement/rollforward (unlike PPE or "
                "Provisions) — present it as such; do not describe it as showing "
                "additions/disposals during the year unless the actual table "
                "content includes those columns."
            ),
        },
        "provisions": {
            "name": "Provisions (incl. Site Restoration / Decommissioning)",
            "note_title_patterns": [
                "movement of provision", "provision for decommissioning",
                "provision for site restoration", "decommissioning liability",
            ],
            "ind_as_standards": [37],
            "topic_keywords": [
                "unwinding of discount", "changes in provisions", "disclosure",
                "present obligation", "reliable estimate", "reimbursement",
            ],
            "verified": True,
        },
        "trade_receivables": {
            "name": "Trade Receivables",
            "note_title_patterns": ["trade receivables", "receivables- current", "receivables ageing"],
            "ind_as_standards": [32, 107, 109],
            "topic_keywords": [
                "expected credit loss", "ageing", "aging", "credit-impaired",
                "credit impaired", "significant increase in credit risk",
            ],
            "verified": True,
        },
        "borrowings": {
            "name": "Borrowings",
            "note_title_patterns": ["borrowings", "working capital loan", "foreign currency bonds"],
            "ind_as_standards": [32, 107, 109],
            "topic_keywords": [
                "financial liability", "amortised cost", "effective interest rate",
                "fair value", "liquidity risk",
            ],
            "verified": True,
            "data_quality_note": (
                "The Borrowings note found is thinner than other schedules — it "
                "covers working-capital loans and related current-liability "
                "detail, not necessarily a full long-term-borrowings rollforward "
                "(some companies, e.g. ONGC standalone, have little long-term "
                "debt at all). State plainly if no rollforward/movement table is "
                "present in what was retrieved, rather than implying one exists."
            ),
        },
        "intangible_assets": {
            "name": "Intangible Assets",
            "note_title_patterns": [
                "intangible assets", "intangible", "tangible",  # see data_quality_note
            ],
            "ind_as_standards": [38],
            "topic_keywords": [
                "amortisation period", "amortization period", "useful life",
                "research and development", "internally generated",
            ],
            "verified": True,
            "data_quality_note": (
                "For at least one company checked (ONGC), the Intangible Assets "
                "cost/amortisation rollforward is embedded in the SAME note table "
                "as Tangible Assets (title 'a. Tangible'), not a separately "
                "titled table — a separate 'Intangible Assets' titled table found "
                "elsewhere may instead be an unrelated CWIP/ageing sub-schedule. "
                "Read the returned content carefully to confirm which figures are "
                "actually the Intangible Assets rollforward before commenting on "
                "them, and say so if the retrieved table doesn't clearly separate "
                "tangible from intangible movement."
            ),
        },
    }

    SUPPORTED_SCHEDULES = list(SCHEDULES.keys())


# ===== SECTION 12: AuditRiskTools =====

class AuditRiskTools:
    """
    Audit risk analysis tool executors. Migrated from audit_risk_tools.py.
    Retrieval-GROUNDING fix (not a computation engine) — prevents the model
    from citing standards that were never actually retrieved.
    """

    _AUDIT_REPORT_PATTERNS = [
        "comptroller and auditor general",
        "key audit matter",
        "emphasis of matter",
    ]

    @staticmethod
    def _match_note_tables(doc_id: str, patterns: list[str], conn_reports) -> list[dict]:
        """Find note-level table_chunks rows matching any of `patterns`."""
        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                like_clauses = []
                params: list = [doc_id]
                for p in patterns:
                    like_clauses.append("(table_title ILIKE %s OR table_description ILIKE %s)")
                    params.extend([f"%{p}%", f"%{p}%"])
                sql = (
                    "SELECT table_id, table_title, table_description, table_md, page_ocr_start "
                    "FROM public.table_chunks "
                    "WHERE doc_id = %s "
                    "  AND (toc_section IS NULL OR toc_section NOT ILIKE '%%consolidated%%') "
                    f"  AND ({' OR '.join(like_clauses)}) "
                    "ORDER BY table_id"
                )
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            return []

    @staticmethod
    def get_schedule_note(company: str, financial_year: str, schedule: str, conn_reports) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        schedule = (schedule or "").strip().lower()
        if schedule not in AuditScheduleReference.SCHEDULES:
            return (
                f"Unknown schedule '{schedule}'. Supported schedules: "
                f"{', '.join(AuditScheduleReference.SUPPORTED_SCHEDULES)}."
            )

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            options = "; ".join(
                f"{r['company']} FY{r['fy_start']}-{str(r['fy_end'])[-2:]} ({r['doc_id']})" for r in match[:10]
            )
            return (
                f"Multiple annual reports matched '{company}' / '{financial_year}': {options}. "
                "Please specify the exact company and financial year."
            )

        definition = AuditScheduleReference.SCHEDULES[schedule]
        rows = AuditRiskTools._match_note_tables(match["doc_id"], definition["note_title_patterns"], conn_reports)

        header = (
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})\n"
            f"{UnitResolver.units_line(match['doc_id'], conn_reports)}\n"
            f"Schedule: {definition['name']} (standalone only)\n"
        )
        if definition.get("data_quality_note"):
            header += f"DATA QUALITY NOTE: {definition['data_quality_note']}\n"

        if not rows:
            return header + f"\nNo note table found for '{definition['name']}' in this document."

        parts = [
            SourceRef.table(
                r["table_id"], r.get("page_ocr_start"),
                r.get("table_description") or r.get("table_title"),
            ) + f"\n{r.get('table_md') or ''}"
            for r in rows
        ]
        return header + "\n" + "\n\n".join(parts)

    @staticmethod
    def get_audit_report_highlights(company: str, financial_year: str, conn_reports) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            options = "; ".join(
                f"{r['company']} FY{r['fy_start']}-{str(r['fy_end'])[-2:]} ({r['doc_id']})" for r in match[:10]
            )
            return (
                f"Multiple annual reports matched '{company}' / '{financial_year}': {options}. "
                "Please specify the exact company and financial year."
            )

        rows = AuditRiskTools._match_note_tables(match["doc_id"], AuditRiskTools._AUDIT_REPORT_PATTERNS, conn_reports)

        def _is_consolidated_content(r: dict) -> bool:
            desc = (r.get("table_description") or "").lower()
            title = (r.get("table_title") or "").lower()
            md = (r.get("table_md") or "").lower()
            if "subsidiary" in desc or "holding company" in desc:
                return True
            # The marker appears in the TITLE as often as the body, and in the
            # plural — SAIL's CAG table is titled "... ON THE CONSOLIDATED
            # FINANCIAL STATEMENTS OF ...", which the body-only singular check
            # let straight through into a standalone-only result.
            return any(
                "consolidated financial statement" in field for field in (md, desc, title)
            )

        rows = [r for r in rows if not _is_consolidated_content(r)]

        header = (
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})\n"
            f"Source: Statutory Audit Report — CAG comments / Key Audit Matters / Emphasis of Matter "
            f"(standalone only)\n"
        )

        if not rows:
            return header + "\nNo CAG comments, Key Audit Matters, or Emphasis of Matter content found in this document."

        parts = [
            SourceRef.table(
                r["table_id"], r.get("page_ocr_start"),
                r.get("table_description") or r.get("table_title"),
            ) + f"\n{r.get('table_md') or ''}"
            for r in rows
        ]
        return header + "\n" + "\n\n".join(parts)

    @staticmethod
    def get_audit_requirements(schedule: str, conn_rules) -> str:
        if conn_rules is None:
            return "[tool error] Rules database is not configured."

        schedule = (schedule or "").strip().lower()
        if schedule not in AuditScheduleReference.SCHEDULES:
            return (
                f"Unknown schedule '{schedule}'. Supported schedules: "
                f"{', '.join(AuditScheduleReference.SUPPORTED_SCHEDULES)}."
            )

        definition = AuditScheduleReference.SCHEDULES[schedule]
        standards = definition["ind_as_standards"]
        keywords = definition["topic_keywords"]

        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                keyword_clause = " OR ".join(["section_title ILIKE %s"] * len(keywords) + ["text ILIKE %s"] * len(keywords))
                params = [standards] + [f"%{k}%" for k in keywords] * 2
                cur.execute(
                    "SELECT standard_number, section_title, paragraph_no, text FROM public.ind_as_chunks "
                    "WHERE standard_number = ANY(%s) "
                    f"  AND ({keyword_clause}) "
                    "ORDER BY standard_number, seq LIMIT 30",
                    params,
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        "SELECT standard_number, section_title, paragraph_no, text FROM public.ind_as_chunks "
                        "WHERE standard_number = ANY(%s) ORDER BY standard_number, seq LIMIT 15",
                        (standards,),
                    )
                    rows = cur.fetchall()
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return f"[error fetching Ind AS requirements for schedule '{schedule}']"

        header = (
            f"Schedule: {definition['name']}\n"
            f"Relevant standards: {', '.join(f'Ind AS {n}' for n in standards)}\n"
        )
        if not rows:
            return header + "\nNo relevant content found in the rules database for these standards."

        body = "\n\n".join(
            f"[Ind AS {r['standard_number']} | {r.get('section_title', '')} | para {r.get('paragraph_no', '?')}] {r.get('text', '')}"
            for r in rows
        )
        return header + "\n" + body


# ===== SECTION 13: TrendAnalysisTools =====

class TrendAnalysisTools:
    """
    Multi-year, line-item-level trend analysis tool. Migrated from
    trend_analysis_tools.py.

    DEDUP FIX (the one behaviour-sensitive refactor in this whole file):
    the original trend_analysis_tools.py had its own _get_latest_fy_end and
    _format_ambiguous helpers, duplicating logic that now lives on
    DocumentResolver (DocumentResolver.latest_fy_end /
    DocumentResolver.format_ambiguous — see Section 6). _resolve_reports
    below is otherwise UNCHANGED: it already called
    reports_tools._resolve_document (now DocumentResolver._resolve_document)
    per FY label in the original — that per-label resolution loop was never
    duplicate logic, just a consumer of the resolver, so it is kept as-is.
    Only the two small formatting/lookup helpers were centralised. Return
    shapes are identical to the original: _resolve_reports still returns
    (ascending list of `documents` rows, error_message) with exactly one of
    the two None.
    """

    _STATEMENT_LABELS = ComplianceTools._STATEMENT_LABELS
    _ALL_STATEMENT_TYPES = ["balance_sheet", "profit_loss", "cash_flow", "statement_of_equity"]
    _DEFAULT_ALL_TYPES = ["balance_sheet", "profit_loss", "cash_flow"]

    _SIGNIFICANCE_PCT_THRESHOLD = 10.0
    _SIGNIFICANCE_MIN_ABS = 1.0  # ignore % swings on near-zero bases

    _TREND_DISCLAIMER = (
        "NOTE: All figures, absolute differences, percentage differences, and CAGR values "
        "below were computed by deterministic Python arithmetic directly from the company's "
        "actual filed statements — never estimated by the language model. The 'Significant' "
        "column flags year-over-year changes exceeding "
        f"{_SIGNIFICANCE_PCT_THRESHOLD:.0f}% (on a non-trivial base) — a computed signal for "
        "where written analysis should focus, not a judgment on materiality in the accounting sense."
    )

    # ------------------------------------------------------------------
    # Report resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_report_labels(financial_year: str) -> list[str] | None:
        """
        Returns a list of FY-label strings ("2023-2024", "2024-2025", ...) to
        resolve as separate documents for a genuine multi-year SPAN, or None
        if `financial_year` should be passed through to
        DocumentResolver._resolve_document as-is.
        """
        years_4digit = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", financial_year)]
        if len(years_4digit) < 2:
            return None
        span_start, span_end = min(years_4digit), max(years_4digit)
        if span_end - span_start < 2:
            return None  # e.g. "2022-2023" is one FY label, not a multi-year span
        return [f"{y}-{y + 1}" for y in range(span_start, span_end)]

    @staticmethod
    def _resolve_reports(company: str, financial_year: str, conn_reports) -> tuple[list[dict] | None, str | None]:
        """Returns (ascending list of `documents` rows, error_message)."""
        financial_year = (financial_year or "").strip()

        if not financial_year:
            latest = DocumentResolver.latest_fy_end(company, conn_reports)
            if latest is None:
                return None, f"No annual reports found for company '{company}'."
            labels = [f"{y}-{y + 1}" for y in range(latest - 2, latest)]
        else:
            labels = TrendAnalysisTools._resolve_report_labels(financial_year)

        if labels is None:
            match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
            if match is None:
                return None, f"No annual report found for company '{company}' (financial year '{financial_year}')."
            if isinstance(match, list):
                return None, DocumentResolver.format_ambiguous(company, financial_year, match)
            return [match], None

        docs = []
        for label in labels:
            match = DocumentResolver._resolve_document(company, label, conn_reports)
            if match is None:
                continue  # genuinely missing report for that year — skip, don't fail the whole request
            if isinstance(match, list):
                return None, DocumentResolver.format_ambiguous(company, label, match)
            docs.append(match)

        if not docs:
            return None, f"No annual reports found for company '{company}' covering the requested period."

        docs.sort(key=lambda d: d["fy_end"])
        return docs, None

    # ------------------------------------------------------------------
    # Line-item extraction + multi-year merge
    # ------------------------------------------------------------------

    @staticmethod
    def _synthesize_label(rows: list, i: int) -> tuple[str, str] | None:
        """Synthesize a stable, descriptive label for a blank-label subtotal row."""
        if i + 1 < len(rows) and rows[i + 1].raw_label.strip():
            anchor = rows[i + 1].raw_label.strip()
            return f"subtotal before '{anchor.lower()}'", f"Subtotal (before '{anchor}')"
        return None

    @staticmethod
    def _extract_year_series(docs: list[dict], statement_type: str, conn_reports):
        """
        Returns (series, display_labels, discrepancies):
          series         : {label_key: {year: value}}
          display_labels : {label_key: human-readable label}
          discrepancies  : list[str]
        """
        series: dict[str, dict[int, float]] = {}
        display_labels: dict[str, str] = {}
        discrepancies: list[str] = []

        for doc in docs:
            text = ComplianceTools._find_statement_tables(doc["doc_id"], statement_type, conn_reports)
            if "No standalone" in text:
                continue
            rows = RatioExtractionEngine.parse_table_md(text)
            current_year, prior_year = doc["fy_end"], doc["fy_start"]

            label_occurrence: dict[str, int] = {}

            for i, row in enumerate(rows):
                key, display = row.label, row.raw_label.strip()
                current_val = row.values[0] if len(row.values) > 0 else None
                prior_val = row.values[1] if len(row.values) > 1 else None

                if not key:
                    if current_val is None and prior_val is None:
                        continue
                    synthesized = TrendAnalysisTools._synthesize_label(rows, i)
                    if synthesized is None:
                        continue
                    key, display = synthesized
                if current_val is None and prior_val is None:
                    continue

                occurrence = label_occurrence.get(key, 0)
                label_occurrence[key] = occurrence + 1
                if occurrence > 0:
                    key = f"{key}#{occurrence + 1}"
                    display = f"{display} (occurrence {occurrence + 1})"

                display_labels[key] = display  # later report's phrasing wins
                entry = series.setdefault(key, {})

                if prior_val is not None:
                    if prior_year not in entry:
                        entry[prior_year] = prior_val
                    elif abs(entry[prior_year] - prior_val) > max(0.01, abs(entry[prior_year]) * 0.001):
                        discrepancies.append(
                            f"{display}: FY{prior_year} shown as {entry[prior_year]:,.2f} (as originally "
                            f"filed) vs {prior_val:,.2f} in {doc['doc_id']}'s comparative column — possible "
                            f"restatement; using the as-originally-filed figure."
                        )
                if current_val is not None:
                    entry[current_year] = current_val  # "current" column always wins

        return series, display_labels, discrepancies

    # ------------------------------------------------------------------
    # Diffs, CAGR, significance
    # ------------------------------------------------------------------

    @staticmethod
    def _format_number(v: float | None) -> str:
        return "N/A" if v is None else f"{v:,.2f}"

    @staticmethod
    def _compute_line_item_row(display_label: str, year_values: dict[int, float], years_sorted: list[int]) -> dict:
        cells = [year_values.get(y) for y in years_sorted]

        yoy = []
        any_significant = False
        for i in range(1, len(years_sorted)):
            a, b = cells[i - 1], cells[i]
            if a is None or b is None:
                yoy.append((None, None))
                continue
            abs_diff = b - a
            pct_diff = (abs_diff / a * 100) if a != 0 else None
            if pct_diff is not None and abs(pct_diff) >= TrendAnalysisTools._SIGNIFICANCE_PCT_THRESHOLD and abs(abs_diff) >= TrendAnalysisTools._SIGNIFICANCE_MIN_ABS:
                any_significant = True
            yoy.append((abs_diff, pct_diff))

        # CAGR compounds over the actual span between the first and last
        # KNOWN values, not the full requested window — a line item with a
        # gap (e.g. a year missing due to a reworded caption between annual
        # reports) would otherwise understate the rate of change by dividing
        # by more periods than actually elapsed between those two values.
        cagr = None
        first_idx = next((i for i, c in enumerate(cells) if c is not None), None)
        last_idx = next((i for i in range(len(cells) - 1, -1, -1) if cells[i] is not None), None)
        if first_idx is not None and last_idx is not None and first_idx != last_idx:
            first_val, last_val = cells[first_idx], cells[last_idx]
            n_periods = years_sorted[last_idx] - years_sorted[first_idx]
            if len(years_sorted) >= 3 and first_val > 0 and last_val > 0 and n_periods > 0:
                cagr = ((last_val / first_val) ** (1 / n_periods) - 1) * 100

        return {"label": display_label, "cells": cells, "yoy": yoy, "cagr": cagr, "significant": any_significant}

    @staticmethod
    def _format_statement_trend(statement_label: str, rows: list[dict], years_sorted: list[int], discrepancies: list[str]) -> str:
        if not rows:
            return f"=== {statement_label} — Multi-Year Trend ===\n\nNo standalone {statement_label} data found for the requested reports."

        year_headers = [f"FY{y}" for y in years_sorted]
        yoy_headers = []
        for i in range(1, len(years_sorted)):
            yoy_headers.append(f"Δ FY{years_sorted[i-1]}→{years_sorted[i]} (Abs)")
            yoy_headers.append(f"Δ FY{years_sorted[i-1]}→{years_sorted[i]} (%)")

        header_cells = ["Line Item"] + year_headers + yoy_headers + ["CAGR", "Significant"]
        header = "| " + " | ".join(header_cells) + " |"
        sep = "|" + "---|" * len(header_cells)

        lines = [f"=== {statement_label} — Multi-Year Trend ===", "", header, sep]
        for r in rows:
            cell_strs = [TrendAnalysisTools._format_number(c) for c in r["cells"]]
            yoy_strs = []
            for abs_d, pct_d in r["yoy"]:
                yoy_strs.append(TrendAnalysisTools._format_number(abs_d))
                yoy_strs.append("N/A" if pct_d is None else f"{pct_d:+.2f}%")
            cagr_str = "N/A" if r["cagr"] is None else f"{r['cagr']:+.2f}%"
            sig_str = "Yes" if r["significant"] else "No"
            row_cells = [r["label"]] + cell_strs + yoy_strs + [cagr_str, sig_str]
            lines.append("| " + " | ".join(row_cells) + " |")

        if discrepancies:
            lines.append("")
            lines.append("RESTATEMENT / DISCREPANCY NOTES:")
            for d in discrepancies:
                lines.append(f"- {d}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public tool entry point
    # ------------------------------------------------------------------

    @staticmethod
    def _units_line(docs: list[dict], conn_reports) -> str:
        """
        The presentation scale across the reports in a trend.

        Resolved per report rather than once, because a company that restates
        from ₹ lakh to ₹ crore between years produces a year-on-year series in
        which the change is entirely presentational. That is the single worst
        error this tool could hand an auditor, so a scale change is reported as a
        blocking caution rather than a footnote.
        """
        seen: dict[str, list[str]] = {}
        for doc in docs:
            info = UnitResolver.resolve(doc["doc_id"], conn_reports)
            label = info["label"] or "not declared"
            seen.setdefault(label, []).append(doc["doc_id"])

        if len(seen) == 1:
            label = next(iter(seen))
            if label == "not declared":
                return (
                    "UNITS: none of these reports' tables declare a presentation scale. Amounts "
                    "are reproduced as filed — say the scale is not stated in the source."
                )
            return (
                f"UNITS: every monetary amount below is in {label}, the scale all of these "
                f"reports declare. Quote EVERY figure with '{label}' attached."
            )

        detail = "; ".join(f"{label}: {', '.join(ids)}" for label, ids in seen.items())
        return (
            "UNITS — CAUTION, THE REPORTS DO NOT SHARE ONE SCALE: " + detail + ". A year-on-year "
            "change between reports on different scales is PRESENTATIONAL, not real. Do not "
            "compute or repeat any growth percentage across the boundary until each year's "
            "figure has been restated onto one scale, and say so plainly in the answer."
        )

    @staticmethod
    def get_multi_year_trend(company: str, financial_year: str = "", statement_type: str = "all", conn_reports=None) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."
        if not company or not company.strip():
            return "[tool error] Company name is required."

        statement_type = (statement_type or "all").strip().lower()
        if statement_type == "all":
            types_to_run = list(TrendAnalysisTools._DEFAULT_ALL_TYPES)
        elif statement_type in TrendAnalysisTools._ALL_STATEMENT_TYPES:
            types_to_run = [statement_type]
        else:
            return (
                f"Unknown statement_type '{statement_type}'. Valid values: "
                f"{', '.join(TrendAnalysisTools._ALL_STATEMENT_TYPES)}, or 'all'."
            )

        docs, err = TrendAnalysisTools._resolve_reports(company, financial_year, conn_reports)
        if err:
            return err

        years_sorted = sorted({y for doc in docs for y in (doc["fy_start"], doc["fy_end"])})

        header = (
            f"Company: {docs[0]['company']}\n"
            f"Reports used: " + ", ".join(d["doc_id"] for d in docs) + "\n"
            f"Years covered: " + ", ".join(f"FY{y}" for y in years_sorted) + "\n"
            f"{TrendAnalysisTools._units_line(docs, conn_reports)}\n"
            f"{TrendAnalysisTools._TREND_DISCLAIMER}\n"
        )

        sections = []
        for st in types_to_run:
            series, display_labels, discrepancies = TrendAnalysisTools._extract_year_series(docs, st, conn_reports)
            rows = [
                TrendAnalysisTools._compute_line_item_row(display_labels[key], year_values, years_sorted)
                for key, year_values in series.items()
                if year_values
            ]
            sections.append(TrendAnalysisTools._format_statement_trend(
                TrendAnalysisTools._STATEMENT_LABELS.get(st, st), rows, years_sorted, discrepancies
            ))

        return header + "\n" + "\n\n".join(sections)


# ===== SECTION 14B: UnitResolver =====

class UnitResolver:
    """
    The presentation scale and currency a document's own tables declare.

    Every figure this system computes is a bare parsed number. ONGC's total
    assets parse as 4,516,527.58 — that is ₹ 4.52 LAKH CRORE, because ONGC
    presents in ₹ million; the identical number in a report presented in ₹ crore
    would be ₹ 45.2 lakh crore. An answer that quotes the figure without the
    scale is not merely incomplete, it is wrong by three orders of magnitude. So
    the scale is resolved here, once per document, and printed by every tool that
    prints money.

    Source of truth is the ingestion pipeline's own `unit` and `currency` columns
    on table_chunks, NOT a regex over the markdown — the caption is frequently
    absent from the table body (verified: ONGC, SAIL and NALCO balance sheets
    carry no unit row at all, while NTPC's sits in the column header). Measured
    on the live corpus: 301 of 346 documents carry at least one non-'%' unit, and
    within a document the value is near-unanimous, so a majority vote over the
    financial tables is both available and stable.

    Where nothing is declared the resolver says exactly that. A guessed scale is
    the one outcome worse than an absent one.
    """

    # Cleared per request alongside FinancialFactBase — same reasoning: the rows
    # were read through a request-scoped connection.
    _cache: dict[str, dict] = {}

    _SCALES = ("crore", "lakh", "million", "billion", "thousand")
    _SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}

    @staticmethod
    def reset() -> None:
        UnitResolver._cache.clear()

    @staticmethod
    def _scale_of(raw: str | None) -> str | None:
        """'Rs. crore', '₹ crore', 'in crore', 'INR crore' all mean the same thing."""
        if not raw:
            return None
        text = raw.strip().lower()
        for scale in UnitResolver._SCALES:
            if scale in text:
                return scale
        return None

    @staticmethod
    def resolve(doc_id: str, conn_reports) -> dict:
        """
        {"scale", "currency", "label", "declared", "mixed"} for one document.

        Never raises and never guesses: a document whose tables declare nothing
        comes back declared=False, and callers must say so rather than assume
        rupees in crore because that is the common case.
        """
        if doc_id in UnitResolver._cache:
            return UnitResolver._cache[doc_id]

        result = {
            "scale": None, "currency": None, "label": None,
            "declared": False, "mixed": False,
        }
        if conn_reports is None:
            return result

        try:
            with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # '%' is a unit on ratio/percentage tables, not a monetary scale.
                cur.execute(
                    "SELECT unit, currency, count(*) AS n "
                    "FROM public.table_chunks "
                    "WHERE doc_id = %s AND is_financial "
                    "  AND (unit IS NOT NULL OR currency IS NOT NULL) "
                    "  AND (unit IS NULL OR unit <> '%%') "
                    "GROUP BY unit, currency ORDER BY n DESC",
                    (doc_id,),
                )
                rows = cur.fetchall()
        except Exception:
            try:
                conn_reports.rollback()
            except Exception:
                pass
            rows = []

        scale_votes: dict[str, int] = {}
        currency_votes: dict[str, int] = {}
        for row in rows:
            scale = UnitResolver._scale_of(row.get("unit"))
            if scale:
                scale_votes[scale] = scale_votes.get(scale, 0) + row["n"]
            currency = (row.get("currency") or "").strip().upper()
            if currency:
                currency_votes[currency] = currency_votes.get(currency, 0) + row["n"]

        if scale_votes:
            result["scale"] = max(scale_votes, key=scale_votes.get)
            # A document presenting some statements in lakh and others in crore is
            # a real presentation inconsistency and a reason not to mix figures
            # across tables without checking each one.
            result["mixed"] = len(scale_votes) > 1
        if currency_votes:
            result["currency"] = max(currency_votes, key=currency_votes.get)

        if result["scale"] or result["currency"]:
            symbol = UnitResolver._SYMBOLS.get(result["currency"] or "", "")
            currency_part = symbol or result["currency"] or ""
            result["label"] = " ".join(p for p in (currency_part, result["scale"]) if p)
            result["declared"] = bool(result["label"])

        UnitResolver._cache[doc_id] = result
        return result

    @staticmethod
    def units_line(doc_id: str, conn_reports) -> str:
        """
        The one line every money-printing tool puts under its Document header.

        Phrased as an instruction, not a label, because the failure this exists to
        stop is downstream: the model reproducing '4,516,527.58' into an answer
        with no unit attached.
        """
        info = UnitResolver.resolve(doc_id, conn_reports)

        if not info["declared"]:
            return (
                "UNITS: this document's tables declare no presentation scale (crore / lakh / "
                "million) and no currency. Amounts below are reproduced exactly as filed. Say "
                "that the scale is not stated in the source — do NOT assume rupees in crore."
            )

        if info["scale"] is None:
            return (
                f"UNITS: amounts below are in {info['currency']}, but this document's tables "
                "declare no presentation scale (crore / lakh / million). Quote the currency "
                "with every figure and state that the scale is not declared in the source."
            )

        line = (
            f"UNITS: every monetary amount below is in {info['label']}"
            + (f" ({info['currency']})" if info["currency"] else "")
            + ", the scale this document's own financial tables declare. Quote EVERY figure "
            f"you take from this output with '{info['label']}' attached — a bare number is "
            "wrong by orders of magnitude to the reader."
        )
        if info["mixed"]:
            line += (
                " CAUTION: not every table in this document uses that scale — more than one "
                "presentation scale was found. Check the individual table before combining "
                "figures across statements."
            )
        return line


# ===== SECTION 15: FinancialFactBase =====

class FinancialFactBase:
    """
    Per-request cache of one document's parsed face statements.

    Why this exists: RatioExtractionEngine.extract_all_figures() calls
    ComplianceTools._find_statement_tables() three times (balance sheet, P&L,
    cash flow), and each of those runs up to three SQL queries plus a markdown
    parse. Materiality, tie-outs, going concern and the executive summary all
    need exactly the same rows and figures. Without a cache, a single user
    question that touches two of those tools re-parses the identical tables
    from scratch — wasted latency inside an 8-iteration agent budget.

    Scope: the cache is keyed by doc_id and MUST be cleared per request
    (ToolRegistry.build_tools calls reset()), because a psycopg2 connection is
    request-scoped and stale figures must never leak between users.
    """

    _cache: dict[str, dict] = {}

    @staticmethod
    def reset() -> None:
        """Drop all cached documents. Called once per request from build_tools."""
        FinancialFactBase._cache.clear()

    @staticmethod
    def load(doc_id: str, conn_reports) -> dict:
        """
        {"bs_rows", "pl_rows", "cf_rows", "figures", "sources",
         "inconsistent_totals", "approximate_averages"} for one document.

        Never raises — a statement that cannot be fetched yields an empty row
        list, matching extract_all_figures' "None means not found" convention.
        """
        cached = FinancialFactBase._cache.get(doc_id)
        if cached is not None:
            return cached

        def _rows(statement_type: str) -> list:
            try:
                text = ComplianceTools._find_statement_tables(doc_id, statement_type, conn_reports)
                if "No standalone" in text:
                    return []
                return RatioExtractionEngine.parse_table_md(text)
            except Exception:
                return []

        try:
            figures = RatioExtractionEngine.extract_all_figures(doc_id, conn_reports)
        except Exception:
            figures = {}

        entry = {
            "doc_id": doc_id,
            "bs_rows": _rows("balance_sheet"),
            "pl_rows": _rows("profit_loss"),
            "cf_rows": _rows("cash_flow"),
            "figures": figures,
            "sources": figures.get("_sources", {}),
            "inconsistent_totals": figures.get("_inconsistent_totals", set()),
            "approximate_averages": figures.get("_approximate_averages", set()),
        }
        FinancialFactBase._cache[doc_id] = entry
        return entry

    # ------------------------------------------------------------------
    # Shared formatting helpers — every Phase 2+ tool cites figures the same way
    # ------------------------------------------------------------------

    @staticmethod
    def format_amount(value: float | None) -> str:
        """Thousands-separated amount, or an explicit not-found marker."""
        return "not extracted" if value is None else f"{value:,.2f}"

    @staticmethod
    def format_source(sources: dict, key: str) -> str:
        """
        One-line provenance for a figure key, in the same shape RatioTools
        already emits ("Source — <statement>, row: '<label>'"), so answers
        stay consistent across tools.
        """
        source = sources.get(key)
        if source is None:
            return f"Source — {key}: not extracted from the face statements."
        if getattr(source, "derived_from", None):
            return f"Source — {key}: derived from {', '.join(source.derived_from)}"
        parts = [p for p in (source.statement, f"row: '{source.label}'" if source.label else None) if p]
        line = f"Source — {key}: " + ", ".join(parts)
        if source.note:
            line += f" ({source.note})"
        return line


# ===== SECTION 16: MaterialityReference + MaterialityTools =====

class MaterialityReference:
    """
    Benchmark percentage bands used to size audit materiality.

    IMPORTANT — these are CONVENTIONAL PRACTICE RANGES, not a rule. Neither
    SA 320 nor Schedule III prescribes a percentage; SA 320 requires the auditor
    to select a benchmark and apply professional judgment. The tool therefore
    presents ALL bases as a range and never picks one silently.
    """

    # (figure_key, display name, low %, high %, note)
    BASES = [
        ("revenue_from_operations", "Revenue from operations", 0.5, 1.0,
         "common for profit-oriented entities with stable revenue"),
        ("total_assets", "Total assets", 0.5, 2.0,
         "common for asset-intensive entities"),
        ("profit_before_tax", "Profit before tax", 5.0, 10.0,
         "the usual first choice for a profit-oriented entity, but unstable near break-even"),
        ("total_equity", "Net worth (total equity)", 1.0, 5.0,
         "useful when earnings are volatile"),
    ]

    # Performance materiality is a fraction of overall materiality; the trivial
    # threshold is a fraction of it in turn. Ranges, again — not single values.
    PERFORMANCE_MATERIALITY_BAND = (50.0, 75.0)
    CLEARLY_TRIVIAL_PCT = 5.0

    DISCLAIMER = (
        "These percentage bands are conventional audit practice, NOT a regulatory "
        "rule — SA 320 requires the auditor to choose a benchmark and percentage "
        "using professional judgment about the entity's circumstances. Treat the "
        "figures below as a starting range to be documented and justified, never "
        "as a computed answer."
    )

    # Below this, a PBT-based benchmark stops being meaningful.
    PBT_INSTABILITY_RATIO = 0.01  # PBT < 1% of revenue => near break-even


class MaterialityTools:
    """
    Multi-basis materiality benchmark computation.

    Every figure comes from RatioExtractionEngine via FinancialFactBase; this
    class only multiplies by a percentage and formats. No model arithmetic, and
    no basis is silently dropped — a basis whose figure could not be extracted
    is printed as such so the auditor knows what is missing rather than
    assuming it was considered.
    """

    _MAX_MATERIALITY_NOTE_CHARS = 1200

    @staticmethod
    def compute_materiality(company: str, financial_year: str, conn_reports, conn_rules) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        facts = FinancialFactBase.load(match["doc_id"], conn_reports)
        figures, sources = facts["figures"], facts["sources"]

        lines = [
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, "
            f"company={match['company']}, FY{match['fy_start']}-{str(match['fy_end'])[-2:]})",
            UnitResolver.units_line(match["doc_id"], conn_reports),
            "MATERIALITY BENCHMARKS (standalone financial statements)",
            "",
            MaterialityReference.DISCLAIMER,
            "",
            "| Basis | Amount | Low % | Low amount | High % | High amount |",
            "|---|---|---|---|---|---|",
        ]

        available: list[tuple[str, float, float, float]] = []
        missing: list[str] = []

        for key, name, low_pct, high_pct, _note in MaterialityReference.BASES:
            value = figures.get(key)
            if value is None:
                missing.append(name)
                lines.append(f"| {name} | not extracted | {low_pct}% | — | {high_pct}% | — |")
                continue
            low_amt = abs(value) * low_pct / 100.0
            high_amt = abs(value) * high_pct / 100.0
            available.append((name, value, low_amt, high_amt))
            lines.append(
                f"| {name} | {FinancialFactBase.format_amount(value)} | {low_pct}% | "
                f"{FinancialFactBase.format_amount(low_amt)} | {high_pct}% | "
                f"{FinancialFactBase.format_amount(high_amt)} |"
            )

        lines.append("")

        if not available:
            lines.append(
                "None of the four benchmark figures could be extracted from this document's "
                "face statements, so no materiality range can be computed. Check whether the "
                "Balance Sheet and Statement of Profit and Loss were located for this document."
            )
            return "\n".join(lines)

        # Derived thresholds are expressed against the FULL span of the computed
        # bases, because the auditor has not yet chosen a benchmark.
        overall_low = min(a[2] for a in available)
        overall_high = max(a[3] for a in available)
        pm_low = overall_low * MaterialityReference.PERFORMANCE_MATERIALITY_BAND[0] / 100.0
        pm_high = overall_high * MaterialityReference.PERFORMANCE_MATERIALITY_BAND[1] / 100.0

        lines += [
            "DERIVED THRESHOLDS (across the bases computed above)",
            f"- Overall materiality (OM) range: {FinancialFactBase.format_amount(overall_low)} "
            f"to {FinancialFactBase.format_amount(overall_high)}",
            f"- Performance materiality "
            f"({MaterialityReference.PERFORMANCE_MATERIALITY_BAND[0]:.0f}–"
            f"{MaterialityReference.PERFORMANCE_MATERIALITY_BAND[1]:.0f}% of OM): "
            f"{FinancialFactBase.format_amount(pm_low)} to {FinancialFactBase.format_amount(pm_high)}",
            f"- Clearly trivial threshold ({MaterialityReference.CLEARLY_TRIVIAL_PCT:.0f}% of OM): "
            f"{FinancialFactBase.format_amount(overall_low * MaterialityReference.CLEARLY_TRIVIAL_PCT / 100.0)} "
            f"to {FinancialFactBase.format_amount(overall_high * MaterialityReference.CLEARLY_TRIVIAL_PCT / 100.0)}",
            "",
        ]

        caveats = MaterialityTools._benchmark_caveats(figures, missing)
        if caveats:
            lines.append("CAVEATS")
            lines += [f"- {c}" for c in caveats]
            lines.append("")

        # Only cite bases that actually produced a figure — a Source line for a
        # basis that was never extracted would imply it was considered.
        lines.append("SOURCES")
        for key, _name, _l, _h, _n in MaterialityReference.BASES:
            if figures.get(key) is not None:
                lines.append(FinancialFactBase.format_source(sources, key))
        lines.append("")

        # Schedule III's own materiality wording, quoted verbatim so the ranges
        # above are read alongside the actual regulation rather than instead of it.
        note = ""
        if conn_rules is not None:
            passage = DocumentResolver._find_compliance_passage(match["doc_id"], conn_reports)
            division_no = ComplianceTools._classify_framework_division(passage)
            note = ComplianceTools._get_materiality_note(division_no, conn_rules)
        if note:
            truncated = note[: MaterialityTools._MAX_MATERIALITY_NOTE_CHARS]
            if len(note) > MaterialityTools._MAX_MATERIALITY_NOTE_CHARS:
                truncated += " […truncated]"
            lines += ["[SCHEDULE III GENERAL INSTRUCTIONS ON MATERIALITY — verbatim]", truncated]

        return "\n".join(lines)

    @staticmethod
    def _benchmark_caveats(figures: dict, missing: list[str]) -> list[str]:
        """Conditions that make a specific basis unreliable for THIS entity."""
        caveats: list[str] = []

        pbt = figures.get("profit_before_tax")
        revenue = figures.get("revenue_from_operations")
        if pbt is not None and pbt < 0:
            caveats.append(
                "The entity is LOSS-MAKING (profit before tax is negative). The PBT basis is "
                "computed on the absolute value and is not meaningful here — prefer the revenue "
                "or total-assets basis, which is the standard adjustment for a loss-making entity."
            )
        elif (
            pbt is not None and revenue
            and abs(pbt) < abs(revenue) * MaterialityReference.PBT_INSTABILITY_RATIO
        ):
            caveats.append(
                "Profit before tax is close to break-even (under 1% of revenue), so a PBT-based "
                "benchmark is volatile — a small swing in profit moves materiality sharply. "
                "Prefer the revenue or total-assets basis."
            )

        if figures.get("profit_before_tax") is None and figures.get("profit_before_exceptional_and_tax") is not None:
            caveats.append(
                "Profit before tax was not presented separately; only 'profit before exceptional "
                "items and tax' is on the face of the statement. These are different figures — "
                "the PBT basis is therefore shown as not extracted rather than substituted."
            )

        if missing:
            caveats.append(
                "Not extracted from the face statements: " + ", ".join(missing)
                + ". Those bases were excluded from the derived ranges above."
            )
        return caveats


# ===== SECTION 17: TieOutTools =====

class TieOutCheck:
    """One cross-statement consistency check and its outcome."""

    __slots__ = ("name", "status", "lhs_label", "lhs", "rhs_label", "rhs", "note", "source_keys")

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_AVAILABLE = "NOT AVAILABLE"

    def __init__(self, name, status, lhs_label=None, lhs=None, rhs_label=None, rhs=None,
                 note=None, source_keys=None):
        self.name = name
        self.status = status
        self.lhs_label = lhs_label
        self.lhs = lhs
        self.rhs_label = rhs_label
        self.rhs = rhs
        self.note = note
        self.source_keys = source_keys or []

    @property
    def difference(self) -> float | None:
        if self.lhs is None or self.rhs is None:
            return None
        return self.lhs - self.rhs


class TieOutTools:
    """
    Cross-statement tie-out checks — the arithmetic identities that MUST hold
    between a company's own filed statements.

    Every check is computed in Python from figures already extracted by
    RatioExtractionEngine, and every failure carries the source rows behind both
    sides so the auditor can go straight to the statement lines involved.

    Design note: these checks are deliberately NOT self-fulfilling. Where a
    figure is ambiguous (e.g. an Ind AS 114 entity presents both "Total assets"
    and "Total assets and regulatory account balances"), the check explains the
    reconciling item rather than silently selecting whichever figure balances —
    otherwise the check would always pass and detect nothing.
    """

    # Same rounding tolerance TrendAnalysisTools uses for restatement detection:
    # statements are published rounded, so exact equality is the wrong test.
    @staticmethod
    def _tolerance(value: float) -> float:
        return max(1.0, 0.001 * abs(value))

    _SCOPES = ("all", "balance_sheet", "profit_loss", "cash_flow", "notes")

    _DEFERRED_NOTE = (
        "SCOPE — these checks cover the FACE of the statements, plus the note-to-face "
        "ties listed in the table above (PPE, inventories, trade receivables, trade "
        "payables, borrowings, investments, cash and cash equivalents). Note-to-face "
        "agreement for anything NOT in that table — leases, employee benefits, "
        "related-party balances, segment reconciliations, equity movements, "
        "consolidation ties — is NOT covered: those notes are not numeric "
        "reconciliations to a single face line. Do not describe this as a complete "
        "tie-out of the financial statements. If a check reports NOT AVAILABLE, that "
        "reconciliation was NOT performed — report it as unable to verify, never as "
        "agreed."
    )

    @staticmethod
    def run_tie_out_checks(company: str, financial_year: str, scope: str, conn_reports) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        scope = (scope or "all").strip().lower()
        if scope not in TieOutTools._SCOPES:
            return (
                f"Unknown scope '{scope}'. Supported: {', '.join(TieOutTools._SCOPES)}."
            )

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        facts = FinancialFactBase.load(match["doc_id"], conn_reports)
        figures, sources = facts["figures"], facts["sources"]

        checks: list[TieOutCheck] = []
        if scope in ("all", "balance_sheet"):
            checks.append(TieOutTools._check_balance_sheet_equation(figures))
            checks.append(TieOutTools._check_subtotal_integrity(facts))
        if scope in ("all", "balance_sheet", "notes"):
            for spec in TieOutTools._NOTE_TO_FACE_SPECS:
                checks.append(
                    TieOutTools._check_note_to_face(
                        match["doc_id"], spec, figures, conn_reports
                    )
                )
        if scope in ("all", "profit_loss"):
            checks.append(TieOutTools._check_pl_income_chain(figures))
            checks.append(TieOutTools._check_pl_tax_chain(figures))
        if scope in ("all", "cash_flow"):
            checks.append(TieOutTools._check_cash_reconciliation(figures))

        return TieOutTools._format(match, checks, sources, conn_reports)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_balance_sheet_equation(figures: dict) -> TieOutCheck:
        """Total assets must equal total equity + liabilities."""
        assets = figures.get("total_assets")
        equity_and_liab = figures.get("total_equity_and_liabilities")
        name = "Balance sheet equation (assets = equity + liabilities)"
        keys = ["total_assets", "total_equity_and_liabilities"]

        if assets is None or equity_and_liab is None:
            return TieOutCheck(
                name, TieOutCheck.NOT_AVAILABLE,
                note="one or both totals were not presented on the face of the balance sheet.",
                source_keys=keys,
            )

        if abs(assets - equity_and_liab) <= TieOutTools._tolerance(assets):
            return TieOutCheck(
                name, TieOutCheck.PASS, "Total assets", assets,
                "Total equity and liabilities", equity_and_liab, source_keys=keys,
            )

        # Ind AS 114 entities present regulatory deferral balances below the
        # ordinary asset total; check whether that reconciles the difference.
        with_regulatory = figures.get("total_assets_incl_regulatory")
        if (
            with_regulatory is not None
            and abs(with_regulatory - equity_and_liab) <= TieOutTools._tolerance(with_regulatory)
        ):
            return TieOutCheck(
                name, TieOutCheck.PASS, "Total assets incl. regulatory balances", with_regulatory,
                "Total equity and liabilities", equity_and_liab,
                note=(
                    "'Total assets' alone "
                    f"({FinancialFactBase.format_amount(assets)}) does NOT balance; the statement "
                    "presents regulatory deferral account balances (Ind AS 114) separately and the "
                    "balance is struck on the regulatory-inclusive total. Reconciling item: "
                    f"{FinancialFactBase.format_amount(equity_and_liab - assets)}."
                ),
                source_keys=keys + ["total_assets_incl_regulatory"],
            )

        return TieOutCheck(
            name, TieOutCheck.FAIL, "Total assets", assets,
            "Total equity and liabilities", equity_and_liab,
            note=(
                "The two sides do not agree. Before treating this as a reporting defect, check "
                "whether the balance sheet was split across several tables in the source document "
                "(a partial subtotal can be picked up instead of the final total), or whether "
                "regulatory deferral account balances are presented separately."
            ),
            source_keys=keys,
        )

    @staticmethod
    def _check_subtotal_integrity(facts: dict) -> TieOutCheck:
        """A reported total must be at least as large as any component rolling into it."""
        inconsistent = facts.get("inconsistent_totals") or set()
        name = "Balance sheet subtotal integrity"
        if not inconsistent:
            return TieOutCheck(
                name, TieOutCheck.PASS,
                note="no reported subtotal was smaller than a component rolling into it.",
            )
        return TieOutCheck(
            name, TieOutCheck.FAIL,
            note=(
                "These subtotals were smaller than a single component within them, so they were "
                "discarded as unreliable rather than used: " + ", ".join(sorted(inconsistent))
                + ". This usually means the wrong row was matched in a multi-table balance sheet."
            ),
        )

    @staticmethod
    def _check_pl_income_chain(figures: dict) -> TieOutCheck:
        """Revenue + other income - total expenses should equal profit before tax."""
        revenue = figures.get("revenue_from_operations")
        other_income = figures.get("other_income")
        expenses = figures.get("total_expenses")
        # An entity reporting exceptional items strikes this subtotal BEFORE them.
        target_key = (
            "profit_before_exceptional_and_tax"
            if figures.get("profit_before_exceptional_and_tax") is not None
            else "profit_before_tax"
        )
        target = figures.get(target_key)
        name = "P&L income chain (revenue + other income - expenses = profit before tax)"
        keys = ["revenue_from_operations", "other_income", "total_expenses", target_key]

        if None in (revenue, expenses, target):
            return TieOutCheck(
                name, TieOutCheck.NOT_AVAILABLE,
                note="revenue, total expenses, or the profit subtotal was not extracted.",
                source_keys=keys,
            )

        computed = revenue + (other_income or 0.0) - expenses
        label = "Revenue + other income - total expenses"
        if other_income is None:
            label += " (other income not presented separately; treated as nil)"

        status = (
            TieOutCheck.PASS
            if abs(computed - target) <= TieOutTools._tolerance(target)
            else TieOutCheck.FAIL
        )
        return TieOutCheck(
            name, status, label, computed,
            target_key.replace("_", " ").capitalize(), target,
            note=(
                None if status == TieOutCheck.PASS else
                "A difference here usually means an income or expense line on the face of the "
                "statement was not captured (for example a share of profit of associates, or a "
                "regulatory income line), not that the statement itself is wrong."
            ),
            source_keys=keys,
        )

    @staticmethod
    def _check_pl_tax_chain(figures: dict) -> TieOutCheck:
        """Profit before tax - tax expense should equal profit for the period."""
        pbt = figures.get("profit_before_tax")
        tax = figures.get("tax_expense")
        pat = figures.get("profit_for_period")
        name = "P&L tax chain (profit before tax - tax expense = profit for the period)"
        keys = ["profit_before_tax", "tax_expense", "profit_for_period"]

        if None in (pbt, tax, pat):
            return TieOutCheck(
                name, TieOutCheck.NOT_AVAILABLE,
                note="profit before tax, tax expense, or profit for the period was not extracted.",
                source_keys=keys,
            )

        computed = pbt - tax
        status = (
            TieOutCheck.PASS
            if abs(computed - pat) <= TieOutTools._tolerance(pat)
            else TieOutCheck.FAIL
        )
        return TieOutCheck(
            name, status, "Profit before tax - tax expense", computed,
            "Profit for the period", pat,
            note=(
                None if status == TieOutCheck.PASS else
                "Check whether the entity reports discontinued operations or a share of profit of "
                "associates between these two lines — both sit inside this chain and are excluded "
                "from the tax figure used here."
            ),
            source_keys=keys,
        )

    @staticmethod
    def _check_cash_reconciliation(figures: dict) -> TieOutCheck:
        """Closing cash per the cash flow statement should tie to the balance sheet."""
        cash_flow_end = figures.get("cash_at_end")
        balance_sheet_cash = figures.get("cash_and_bank")
        name = "Cash flow reconciliation (closing cash = balance sheet cash)"
        keys = ["cash_at_end", "cash_and_bank"]

        if cash_flow_end is None or balance_sheet_cash is None:
            return TieOutCheck(
                name, TieOutCheck.NOT_AVAILABLE,
                note=(
                    "the closing-cash line of the cash flow statement, or cash and cash "
                    "equivalents on the balance sheet, was not extracted."
                ),
                source_keys=keys,
            )

        if abs(cash_flow_end - balance_sheet_cash) <= TieOutTools._tolerance(cash_flow_end):
            return TieOutCheck(
                name, TieOutCheck.PASS, "Closing cash per cash flow statement", cash_flow_end,
                "Cash and cash equivalents per balance sheet", balance_sheet_cash, source_keys=keys,
            )

        # A difference here is very often definitional rather than an error.
        return TieOutCheck(
            name, TieOutCheck.NOT_AVAILABLE, "Closing cash per cash flow statement", cash_flow_end,
            "Cash and cash equivalents per balance sheet", balance_sheet_cash,
            note=(
                "These do not agree, but that is NOT necessarily a defect: the balance sheet's "
                "'Cash and cash equivalents' commonly excludes 'Bank balances other than cash and "
                "cash equivalents' (unpaid dividend accounts, deposits with maturity over three "
                "months), while the cash flow statement's closing balance may include or exclude "
                "bank overdrafts. Reported as NOT AVAILABLE rather than FAIL — read the cash and "
                "cash equivalents note before concluding anything. Difference: "
                f"{FinancialFactBase.format_amount(cash_flow_end - balance_sheet_cash)}."
            ),
            source_keys=keys,
        )

    # ------------------------------------------------------------------
    # Note-to-face reconciliations
    # ------------------------------------------------------------------

    # One spec per balance-sheet line that a note is supposed to support.
    #
    # Originally only PPE was reconciled, and every answer had to end with "other
    # note-to-face reconciliations were not part of this check" — which is the
    # honest thing to say about a tool that only does one, but it is the wrong
    # tool. The mechanics that reconcile PPE (find the face figure anywhere in
    # the matched note tables; failing that, find a small set of note totals that
    # SUMS to it) are not PPE-specific, so they are driven from this table
    # instead.
    #
    # `face_key` must name a figure RatioExtractionEngine actually extracts —
    # anything else yields a permanently NOT AVAILABLE check, which is noise.
    # `note_patterns` reuse AuditScheduleReference where a schedule exists so the
    # tie-out and get_schedule_note look at exactly the same tables.
    # `next_step` names the follow-up call that WILL return the note, so an
    # unresolved check is a lead rather than a dead end.
    _NOTE_TO_FACE_SPECS: tuple[dict, ...] = (
        {
            "name": "PPE note-to-face (schedule corroborates balance sheet PPE)",
            "face_key": "property_plant_equipment",
            "face_description": "Property, Plant and Equipment",
            "face_label": "Balance sheet PPE",
            "note_label": "PPE schedule note",
            "note_patterns": AuditScheduleReference.SCHEDULES["ppe"]["note_title_patterns"],
            "max_components": 4,
            "max_candidates": 8,
            "next_step": "get_schedule_note('ppe')",
            "why_not_fail": (
                "a PPE note is a movement schedule (gross block, depreciation, net block) "
                "split across several tables, whose closing net carrying amount is a COLUMN, "
                "not a row"
            ),
        },
        {
            "name": "Inventories note-to-face (note corroborates balance sheet inventories)",
            "face_key": "inventories",
            "face_description": "Inventories",
            "face_label": "Balance sheet inventories",
            "note_label": "Inventories note",
            "note_patterns": AuditScheduleReference.SCHEDULES["inventory"]["note_title_patterns"],
            "max_components": 3,
            "max_candidates": 4,
            "next_step": "get_schedule_note('inventory')",
            "why_not_fail": (
                "the inventories note is presented by class (raw materials, work in progress, "
                "finished goods, stores and spares, goods in transit) and its total may be "
                "struck net of a write-down shown in a separate column"
            ),
        },
        {
            "name": "Trade receivables note-to-face (note corroborates balance sheet receivables)",
            "face_key": "trade_receivables",
            "face_description": "Trade receivables",
            "face_label": "Balance sheet trade receivables",
            "note_label": "Trade receivables note",
            "note_patterns": AuditScheduleReference.SCHEDULES["trade_receivables"]["note_title_patterns"],
            "max_components": 3,
            "max_candidates": 4,
            "next_step": "get_schedule_note('trade_receivables')",
            "why_not_fail": (
                "the trade receivables note is an ageing matrix whose totals are struck GROSS, "
                "with the expected credit loss allowance deducted in a separate row or column, "
                "and the face line is usually current receivables only"
            ),
        },
        {
            "name": "Trade payables note-to-face (note corroborates balance sheet payables)",
            "face_key": "trade_payables",
            "face_description": "Trade payables",
            "face_label": "Balance sheet trade payables",
            "note_label": "Trade payables note",
            "note_patterns": ["trade payable", "payables ageing", "payable ageing"],
            "max_components": 3,
            "max_candidates": 4,
            "next_step": "lookup_report_reference with 'trade payables'",
            "why_not_fail": (
                "the trade payables note is an ageing matrix split between micro and small "
                "enterprises (MSMED) and other creditors, often in separate tables with no "
                "combined total"
            ),
        },
        {
            "name": "Borrowings note-to-face (note corroborates balance sheet borrowings)",
            "face_key": "total_borrowings",
            "face_description": "Total borrowings (non-current + current)",
            "face_label": "Balance sheet borrowings (non-current + current)",
            "note_label": "Borrowings note",
            "note_patterns": AuditScheduleReference.SCHEDULES["borrowings"]["note_title_patterns"],
            "max_components": 3,
            "max_candidates": 4,
            "next_step": "get_schedule_note('borrowings')",
            "why_not_fail": (
                "current maturities of long-term debt are commonly presented under other "
                "financial liabilities rather than borrowings, so the note total and the face "
                "lines legitimately differ, and non-current and current borrowings are usually "
                "two separate notes"
            ),
        },
        {
            "name": "Investments note-to-face (note corroborates non-current investments)",
            "face_key": "non_current_investments",
            "face_description": "Non-current investments",
            "face_label": "Balance sheet non-current investments",
            "note_label": "Investments note",
            "note_patterns": AuditScheduleReference.SCHEDULES["investments"]["note_title_patterns"],
            "max_components": 3,
            "max_candidates": 4,
            "next_step": "get_schedule_note('investments')",
            "why_not_fail": (
                "the investments note is an entity-wise balance snapshot that mixes quoted and "
                "unquoted holdings, aggregate carrying value and aggregate market value, and "
                "the face line may be stated net of an impairment allowance shown separately"
            ),
        },
        {
            "name": "Cash note-to-face (note corroborates balance sheet cash and cash equivalents)",
            "face_key": "cash_and_bank",
            "face_description": "Cash and cash equivalents",
            "face_label": "Balance sheet cash and cash equivalents",
            "note_label": "Cash and cash equivalents note",
            "note_patterns": [
                "cash and cash equivalent", "cash & cash equivalent", "cash and bank balance",
                "bank balances other than",
            ],
            "max_components": 3,
            "max_candidates": 4,
            "next_step": "lookup_report_reference with 'cash and cash equivalents'",
            "why_not_fail": (
                "the cash note and the adjacent 'bank balances other than cash and cash "
                "equivalents' note are frequently tabled together, so a total in the retrieved "
                "tables may span both face lines"
            ),
        },
    )

    @staticmethod
    def _check_note_to_face(
        doc_id: str, spec: dict, figures: dict, conn_reports
    ) -> TieOutCheck:
        """
        Corroborate one balance-sheet face figure against its supporting note.

        These checks exist because "does the note reconcile to the balance sheet"
        is a standard audit question the face-only checks could not answer at all —
        the tool returned figures the model then had to eyeball, producing answers
        that read like a completed reconciliation without one having been done.

        DELIBERATELY ASYMMETRIC — every one of them returns PASS or NOT AVAILABLE,
        never FAIL. A note is a two-dimensional grid, usually split across several
        tables, whose comparable figure is often a COLUMN rather than a row.
        Verified against ONGC / SAIL / NTPC / NALCO, no row-caption heuristic
        identifies that column reliably — an earlier attempt read NALCO's gross
        block and reported a 6,426.85 "discrepancy" that does not exist. So a check
        only makes the claim it can support: if some value in the note equals the
        face figure, the face figure is corroborated (PASS); if none does, we cannot
        tell a real difference from a column we failed to locate, and say so
        (NOT AVAILABLE). Fabricating a discrepancy is the failure these checks were
        built to prevent, so they never report one.
        """
        name = spec["name"]
        keys = [spec["face_key"]]
        face = figures.get(spec["face_key"])

        if face is None:
            return TieOutCheck(
                name, TieOutCheck.NOT_AVAILABLE,
                note=(
                    f"{spec['face_description']} was not extracted from the face of the "
                    "balance sheet, so there is nothing to reconcile the note against."
                ),
                source_keys=keys,
            )

        # A nil face line would be "corroborated" by any empty cell or zero row in
        # the note — a match that means nothing.
        if abs(face) < 1.0:
            return TieOutCheck(
                name, TieOutCheck.NOT_AVAILABLE,
                spec["face_label"], face,
                note=(
                    "The face figure is nil, so matching it against the note would prove "
                    "nothing — any zero row in the note would 'corroborate' it."
                ),
                source_keys=keys,
            )

        tables = AuditRiskTools._match_note_tables(doc_id, spec["note_patterns"], conn_reports)
        hit = TieOutTools._note_corroboration(tables, face)

        if hit is not None:
            note_value, note_label, table_id = hit
            return TieOutCheck(
                name, TieOutCheck.PASS,
                f"{spec['note_label']} — '{note_label}' [table_id: {table_id}]", note_value,
                spec["face_label"], face, source_keys=keys,
            )

        # A single matching figure is the easy case. More often the face line is
        # the SUM of separately-tabled components (ONGC PPE: Tangible + Other PPE +
        # ROU; payables: MSME + others), so try that too — in Python. Leaving the
        # addition to the model would break this module's core invariant that every
        # figure an answer states was computed here, and in practice the model did
        # not attempt it: it listed the components and reported the reconciliation
        # as incomplete.
        combo = TieOutTools._note_component_sum(tables, face, spec["max_components"])
        if combo is not None:
            total, parts = combo
            # Name each component by its table title — the row labels are all
            # literally "Total", which left the answer calling them
            # "Component 1/2/3" instead of Tangible / Other PPE / ROU.
            breakdown = "\n".join(
                f"        + {title or label}: {FinancialFactBase.format_amount(value)} "
                f"[table_id: {table_id}"
                + (f" | page: {page}" if page else "")
                + "]"
                for value, label, table_id, page, title in parts
            )
            return TieOutCheck(
                name, TieOutCheck.PASS,
                f"{spec['note_label']} — sum of {len(parts)} components", total,
                spec["face_label"], face,
                note=(
                    "The face line is the sum of separately-tabled components. Computed "
                    "here, not estimated:\n" + breakdown + "\n        = "
                    + FinancialFactBase.format_amount(total)
                    + ", against the balance sheet's "
                    + FinancialFactBase.format_amount(face) + "."
                ),
                source_keys=keys,
            )

        if not tables:
            detail = (
                f"{spec['note_label']} — no such table was found in this document at all, so "
                "there was nothing to reconcile against."
            )
        else:
            # The note tables are already fetched here. Returning the candidate
            # totals costs nothing and turns a dead end into something the reader
            # can act on — without them the answer says only "could not verify"
            # while the figures sit unused in this function.
            detail = (
                f"{spec['note_label']} WAS found, but no figure in it — and no sum of up to "
                f"{spec['max_components']} of its table totals — matches the face "
                "figure. Candidate totals read from the note — the comparable closing figure "
                "is among them, or is a column this tool cannot read positionally:\n"
                + TieOutTools._note_candidates(tables, spec["max_candidates"])
            )
        # The tail is kept terse deliberately: with seven note-to-face checks in
        # one output, a paragraph of standing advice per unresolved check is what
        # pushes this tool past its context budget. The rule it states in full is
        # in the closing SCOPE paragraph, once.
        return TieOutCheck(
            name, TieOutCheck.NOT_AVAILABLE,
            spec["face_label"], face,
            note=(
                detail + "\n    Not evidence of a misstatement — " + spec["why_not_fail"]
                + ". Verdict: UNABLE TO VERIFY (never 'reconciled', never a difference). "
                f"The data IS available — next step: {spec['next_step']}."
            ),
            source_keys=keys,
        )

    @staticmethod
    def _note_corroboration(
        tables: list[dict], face: float
    ) -> tuple[float, str, str] | None:
        """Find a value anywhere in the note tables that equals the face figure."""
        tolerance = TieOutTools._tolerance(face)
        for table in tables:
            md = table.get("table_md") or ""
            if not md:
                continue
            for row in RatioExtractionEngine.parse_table_md(md):
                for value in row.values:
                    if value is not None and abs(value - face) <= tolerance:
                        return value, row.raw_label.strip(), table.get("table_id") or "?"
        return None

    # Rows worth showing when the reconciliation could not be completed: the
    # closing/total captions of a movement schedule or an ageing matrix.
    _NOTE_CANDIDATE_PATTERNS = [
        "total", "net carrying amount", "carrying amount", "net block",
        "closing", "balance as at", "balance at",
    ]

    @staticmethod
    def _note_component_sum(
        tables: list[dict], face: float, max_components: int
    ) -> tuple[float, list[tuple[float, str, str, object, str]]] | None:
        """
        Find a small set of note-table totals that sums to the face figure.

        One total per table (its largest), so components cannot be double-counted
        from within the same table. Smaller combinations are tried first and the
        first match wins — a 2-component explanation is more likely to be the real
        presentation than a 4-component coincidence.

        `max_components` is per-spec and deliberately small: a face line is built
        from at most a handful of components (PPE: tangible, other PPE,
        right-of-use; payables: MSME, others). Capping the subset size keeps the
        search trivial and, more importantly, keeps a match meaningful — allow
        enough terms and some combination will hit any target by chance.
        """
        import itertools

        totals: list[tuple[float, str, str, object, str]] = []
        for table in tables:
            md = table.get("table_md") or ""
            if not md:
                continue
            best: tuple[float, str, str, object, str] | None = None
            for row in RatioExtractionEngine.parse_table_md(md):
                if not RatioExtractionEngine._row_matches(row, ["total"]):
                    continue
                value = RatioExtractionEngine._first_value(row)
                if value is None or value <= 0:
                    continue
                if best is None or value > best[0]:
                    best = (
                        value, row.raw_label.strip(),
                        table.get("table_id") or "?", table.get("page_ocr_start"),
                        (table.get("table_title") or "").strip(),
                    )
            if best is not None:
                totals.append(best)

        if not totals:
            return None

        tolerance = TieOutTools._tolerance(face)
        limit = min(max_components, len(totals))
        for size in range(2, limit + 1):
            for combo in itertools.combinations(totals, size):
                subtotal = sum(c[0] for c in combo)
                if abs(subtotal - face) <= tolerance:
                    return subtotal, list(combo)
        return None

    @staticmethod
    def _note_candidates(tables: list[dict], max_candidates: int) -> str:
        """
        Total/closing rows from a note, for the unable-to-verify detail.

        Sorted by magnitude, largest first, before truncating. In document order
        the list filled up with prior-year comparatives ("Balance at March 31,
        2023") and ONGC's three component totals — the figures that actually sum
        to the balance-sheet line — fell past the cut.

        `max_candidates` is per-spec: with seven note-to-face checks in one output,
        an unbounded list on each is what would blow the context budget the rest of
        this module is careful to protect.
        """
        seen: set[tuple[str, float]] = set()
        found: list[tuple[float, str, dict]] = []
        for table in tables:
            md = table.get("table_md") or ""
            if not md:
                continue
            for row in RatioExtractionEngine.parse_table_md(md):
                if not RatioExtractionEngine._row_matches(
                    row, TieOutTools._NOTE_CANDIDATE_PATTERNS
                ):
                    continue
                value = RatioExtractionEngine._first_value(row)
                if value is None:
                    continue
                label = row.raw_label.strip()
                if (label, value) in seen:
                    continue
                seen.add((label, value))
                found.append((value, label, table))

        if not found:
            return "        (no total rows could be read)"

        found.sort(key=lambda x: -abs(x[0]))
        truncated = len(found) > max_candidates
        lines = [
            f"        - {label}: {FinancialFactBase.format_amount(value)} "
            f"[table_id: {table.get('table_id') or '?'}"
            + (f" | page: {table['page_ocr_start']}" if table.get("page_ocr_start") else "")
            + f" | {(table.get('table_title') or '').strip()[:60]}]"
            for value, label, table in found[:max_candidates]
        ]
        if truncated:
            lines.append(f"        (showing the {len(lines)} largest of {len(found)})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format(
        match: dict, checks: list[TieOutCheck], sources: dict, conn_reports=None
    ) -> str:
        lines = [
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, "
            f"company={match['company']}, FY{match['fy_start']}-{str(match['fy_end'])[-2:]})",
            UnitResolver.units_line(match["doc_id"], conn_reports),
            "CROSS-STATEMENT TIE-OUT CHECKS (standalone financial statements)",
            "",
            "| # | Check | Result | Left | Right | Difference |",
            "|---|---|---|---|---|---|",
        ]
        for i, check in enumerate(checks, start=1):
            difference = check.difference
            lines.append(
                f"| {i} | {check.name} | **{check.status}** | "
                f"{FinancialFactBase.format_amount(check.lhs) if check.lhs is not None else '—'} | "
                f"{FinancialFactBase.format_amount(check.rhs) if check.rhs is not None else '—'} | "
                f"{FinancialFactBase.format_amount(difference) if difference is not None else '—'} |"
            )
        lines.append("")

        counts = {status: sum(1 for c in checks if c.status == status)
                  for status in (TieOutCheck.PASS, TieOutCheck.FAIL, TieOutCheck.NOT_AVAILABLE)}
        lines.append(
            f"SUMMARY: {counts[TieOutCheck.PASS]} passed, {counts[TieOutCheck.FAIL]} failed, "
            f"{counts[TieOutCheck.NOT_AVAILABLE]} not available."
        )

        # Spelled out separately because the note-to-face family is the part
        # readers ask about by name ("was borrowings reconciled?"), and reading it
        # out of a seven-row table is exactly where an answer starts saying a
        # reconciliation "was not part of this check" when it was.
        note_names = {spec["name"] for spec in TieOutTools._NOTE_TO_FACE_SPECS}
        note_checks = [c for c in checks if c.name in note_names]
        if note_checks:
            def _short(check: TieOutCheck) -> str:
                return check.name.split(" note-to-face")[0]

            corroborated = [_short(c) for c in note_checks if c.status == TieOutCheck.PASS]
            unverified = [_short(c) for c in note_checks if c.status != TieOutCheck.PASS]
            lines.append(
                "NOTE-TO-FACE RECONCILIATIONS ATTEMPTED ("
                f"{len(note_checks)}): corroborated — "
                + (", ".join(corroborated) if corroborated else "none")
                + "; unable to verify — "
                + (", ".join(unverified) if unverified else "none")
                + ". Every one of these WAS attempted: report the unverified ones as UNABLE "
                "TO VERIFY, not as outside the scope of the check."
            )
        lines.append("")

        # Detail is emitted only for checks that need explanation — a passing
        # check needs no commentary, and printing every source line for every
        # check is what would blow the context budget.
        detailed = [c for c in checks if c.status != TieOutCheck.PASS or c.note]
        if detailed:
            lines.append("DETAIL")
            for check in detailed:
                lines.append(f"- [{check.status}] {check.name}")
                # Print whichever side exists, not only matched pairs. A check that
                # has one side and not the other is exactly the case where the
                # reader needs the side we DO have: with the pair-only rule, the
                # PPE unable-to-verify detail omitted the balance-sheet figure and
                # the model correctly reported it as "not provided in the output".
                if check.lhs is not None:
                    lines.append(
                        f"    {check.lhs_label}: {FinancialFactBase.format_amount(check.lhs)}"
                    )
                if check.rhs is not None:
                    lines.append(
                        f"    {check.rhs_label}: {FinancialFactBase.format_amount(check.rhs)}"
                    )
                if check.note:
                    lines.append(f"    {check.note}")
                for key in check.source_keys:
                    lines.append("    " + FinancialFactBase.format_source(sources, key))
            lines.append("")

        lines.append(TieOutTools._DEFERRED_NOTE)
        return "\n".join(lines)


# ===== SECTION 18: ExecutiveSummaryTools =====

class ExecutiveSummaryTools:
    """
    Audit-oriented executive summary of one annual report.

    Deliberately assembled inside a SINGLE tool call rather than left to the
    agent to compose from six separate calls: the summary needs the framework,
    the headline figures, the auditor's own flags, the business narrative and
    the accounting policies, and fetching those one per iteration would consume
    most of the agent's tool-call budget before it could write anything.

    Everything here is either a figure already extracted by
    RatioExtractionEngine or text quoted from the report itself. Nothing is
    summarised by this code — the narrative section quotes the report's own
    pre-computed summary chunks, clearly labelled as the company's own words.
    """

    # Face-statement lines worth putting in a summary, in presentation order.
    _HEADLINE_LINES = [
        ("revenue_from_operations", "Revenue from operations"),
        ("other_income", "Other income"),
        ("total_expenses", "Total expenses"),
        ("profit_before_tax", "Profit before tax"),
        ("tax_expense", "Tax expense"),
        ("profit_for_period", "Profit for the period"),
        ("eps_basic", "Earnings per share (basic)"),
        ("total_assets", "Total assets"),
        ("total_equity", "Total equity"),
        ("cash_from_operating", "Net cash from operating activities"),
    ]

    _LABEL_MAP_BY_KEY = None  # lazily built: figure key -> (patterns, rows_key)

    _MAX_AUDIT_CHARS_PER_TABLE = 800
    _MAX_NARRATIVE_CHARS = 1500
    _MAX_TOTAL_CHARS = 10000

    @staticmethod
    def _key_lookup() -> dict:
        """figure key -> (label patterns, which parsed row list to search)."""
        if ExecutiveSummaryTools._LABEL_MAP_BY_KEY is None:
            mapping = {}
            for key, patterns in RatioExtractionEngine.BALANCE_SHEET_LABELS.items():
                mapping[key] = (patterns, "bs_rows")
            for key, patterns in RatioExtractionEngine.PROFIT_LOSS_LABELS.items():
                mapping[key] = (patterns, "pl_rows")
            for key, patterns in RatioExtractionEngine.CASH_FLOW_LABELS.items():
                mapping[key] = (patterns, "cf_rows")
            ExecutiveSummaryTools._LABEL_MAP_BY_KEY = mapping
        return ExecutiveSummaryTools._LABEL_MAP_BY_KEY

    @staticmethod
    def _prior_year_value(facts: dict, key: str) -> float | None:
        """
        The prior-year comparative for a figure key, read from the same row the
        current-year figure came from. extract_all_figures only keeps the
        current-year column, so this re-locates the row to read column 2.
        """
        entry = ExecutiveSummaryTools._key_lookup().get(key)
        if entry is None:
            return None
        patterns, rows_key = entry
        rows = facts.get(rows_key) or []
        found = RatioExtractionEngine._find_row_with_value(
            rows, patterns, exclude=RatioExtractionEngine.LABEL_EXCLUSIONS.get(key)
        )
        return RatioExtractionEngine._second_value(found[1]) if found else None

    @staticmethod
    def _yoy(current: float | None, prior: float | None) -> str:
        if current is None or prior is None:
            return "—"
        if prior == 0:
            return "n/a (prior year nil)"
        return f"{((current - prior) / abs(prior)) * 100:+.1f}%"

    @staticmethod
    def _narrative(doc_id: str, conn_reports) -> str:
        """
        The report's own pre-computed summary text. Prefers section_summary
        (which carries the performance highlights) over doc_summary (which on
        many documents is just the cover page).
        """
        for chunk_type in ("section_summary", "doc_summary"):
            try:
                with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT content FROM public.text_chunks "
                        "WHERE doc_id = %s AND chunk_type = %s AND content IS NOT NULL "
                        "ORDER BY length(content) DESC LIMIT 1",
                        (doc_id, chunk_type),
                    )
                    row = cur.fetchone()
            except Exception:
                try:
                    conn_reports.rollback()
                except Exception:
                    pass
                continue
            if row and row.get("content"):
                text = re.sub(r"\s+", " ", row["content"]).strip()
                if len(text) > ExecutiveSummaryTools._MAX_NARRATIVE_CHARS:
                    text = text[: ExecutiveSummaryTools._MAX_NARRATIVE_CHARS] + " […truncated]"
                return text
        return ""

    @staticmethod
    def _policy_titles(doc_id: str, conn_reports) -> list[str]:
        """Which of the 11 canonical accounting-policy topics have a note in this report."""
        found = []
        for canonical, phrases in AccountingPolicyReference.TOPIC_KEYWORDS.items():
            anchor = AccountingPolicyTools._find_heading_by_keywords(doc_id, phrases, conn_reports)
            if anchor is not None:
                found.append(AccountingPolicyReference.display_name(canonical))
        return found

    @staticmethod
    def summarize_annual_report(company: str, financial_year: str, conn_reports, conn_rules) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        doc, framework_passage = DocumentResolver.get_framework_and_doc(
            company, financial_year, conn_reports
        )
        if doc is None:
            match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
            if isinstance(match, list):
                return DocumentResolver.format_ambiguous(
                    company, financial_year, match, ask="the exact company and financial year"
                )
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."

        doc_id = doc["doc_id"]
        facts = FinancialFactBase.load(doc_id, conn_reports)
        figures, sources = facts["figures"], facts["sources"]

        lines = [
            f"EXECUTIVE SUMMARY (audit-oriented) — {doc['company']} "
            f"FY{doc['fy_start']}-{str(doc['fy_end'])[-2:]}",
            f"Document: {doc['doc_name']} (doc_id={doc_id})",
            UnitResolver.units_line(doc_id, conn_reports),
            "Scope: standalone financial statements.",
            "",
            "1. REPORTING FRAMEWORK",
        ]
        if framework_passage:
            division = ComplianceTools._classify_framework_division(framework_passage)
            lines.append(f"   Classified as {division} of Schedule III, from the entity's own "
                         f"Statement of Compliance / Basis of Preparation:")
            lines.append(f"   \"{ExecutiveSummaryTools._clean_passage(framework_passage)}\"")
        else:
            lines.append("   Statement of Compliance / Basis of Preparation passage not located "
                         "in this document — framework not confirmed.")
        lines.append("")

        # ---- 2. Headline figures ----
        lines += [
            "2. KEY FINANCIALS (face of the standalone statements, current vs prior-year comparative)",
            "",
            "| Line item | Current year | Prior year | Change |",
            "|---|---|---|---|",
        ]
        any_figure = False
        for key, label in ExecutiveSummaryTools._HEADLINE_LINES:
            current = figures.get(key)
            if current is None:
                continue
            any_figure = True
            prior = ExecutiveSummaryTools._prior_year_value(facts, key)
            lines.append(
                f"| {label} | {FinancialFactBase.format_amount(current)} | "
                f"{FinancialFactBase.format_amount(prior)} | "
                f"{ExecutiveSummaryTools._yoy(current, prior)} |"
            )
        if not any_figure:
            lines.append("| (no face-statement figures could be extracted) | — | — | — |")
        lines.append("")

        # ---- 3. Internal consistency ----
        lines.append("3. INTERNAL CONSISTENCY (cross-statement tie-outs)")
        checks = [
            TieOutTools._check_balance_sheet_equation(figures),
            TieOutTools._check_pl_income_chain(figures),
            TieOutTools._check_pl_tax_chain(figures),
        ]
        for check in checks:
            lines.append(f"   [{check.status}] {check.name}")
            if check.status == TieOutCheck.FAIL and check.note:
                lines.append(f"        {check.note}")
        lines.append("   (Face of the statements only — note-to-face agreement is not checked. "
                     "Run run_tie_out_checks for the full set with figures.)")
        lines.append("")

        # ---- 4. Auditor's report ----
        lines.append("4. AUDITOR'S REPORT — CAG comments / Key Audit Matters / Emphasis of Matter")
        highlights = AuditRiskTools.get_audit_report_highlights(company, financial_year, conn_reports)
        lines.append(ExecutiveSummaryTools._condense_audit_highlights(highlights))
        lines.append("")

        # ---- 5. Accounting policies ----
        lines.append("5. SIGNIFICANT ACCOUNTING POLICIES DISCLOSED (titles only)")
        titles = ExecutiveSummaryTools._policy_titles(doc_id, conn_reports)
        if titles:
            lines.append("   " + "; ".join(titles))
            lines.append("   (Call get_accounting_policy_note for the full text of any one of these.)")
        else:
            lines.append("   No separately-headed policy note was located for the 11 canonical topics; "
                         "this entity may bundle them under one general policies note.")
        lines.append("")

        # ---- 6. Business narrative ----
        narrative = ExecutiveSummaryTools._narrative(doc_id, conn_reports)
        if narrative:
            lines += [
                "6. BUSINESS HIGHLIGHTS — THE COMPANY'S OWN NARRATIVE, QUOTED",
                "   These are management's own presentational highlights, not audited figures and "
                "not independent analysis. Attribute them to the company; do not restate them as fact.",
                f"   \"{narrative}\"",
                "",
            ]

        # ---- 7. Data-quality flags ----
        flags = ExecutiveSummaryTools._data_quality_flags(facts)
        if flags:
            lines.append("7. DATA-QUALITY FLAGS FOR THE AUDITOR")
            lines += [f"   - {f}" for f in flags]
            lines.append("")

        lines.append(
            "This summary is assembled from the entity's own filed statements and report text. "
            "It is not an audit opinion and does not evaluate measurement, classification or "
            "disclosure adequacy."
        )

        output = "\n".join(lines)
        if len(output) > ExecutiveSummaryTools._MAX_TOTAL_CHARS:
            output = (
                output[: ExecutiveSummaryTools._MAX_TOTAL_CHARS]
                + "\n[truncated — ask for a specific section for full detail]"
            )
        return output

    @staticmethod
    def _clean_passage(passage: str) -> str:
        """
        Strip the scaffolding get_framework_and_doc wraps around a passage —
        the [Section: …] breadcrumb, [Page: …], the chunk-id list and the
        SourceRef.instruction() text — leaving only the entity's own words.
        That scaffolding is guidance for the agent, not content, and quoting it
        back inside a summary is noise.
        """
        text = re.sub(r"\[Section:.*?\]", " ", passage, flags=re.DOTALL)
        text = re.sub(r"\[Page:.*?\]", " ", text)
        text = re.sub(r"\[Chunk IDs in this passage:.*?\]", " ", text, flags=re.DOTALL)
        # Matches SourceRef.instruction(), which spans several sentences and ends
        # on "...mean nothing to a reader." Anchored on that closing token so a
        # non-greedy match cannot stop early and leave half the instruction behind.
        # "CITE e(ach|very)" keeps this working against the older wording too.
        text = re.sub(r"CITE e(?:ach|very) sentence.*?reader\.", " ", text, flags=re.DOTALL)
        text = re.sub(r"CITE e(?:ach|very) sentence[^\n]*", " ", text)
        text = re.sub(r"\[chunk_id:[^\]]*\]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:600] + (" […truncated]" if len(text) > 600 else "")

    @staticmethod
    def _condense_audit_highlights(raw: str) -> str:
        """
        Trim get_audit_report_highlights' output, which returns whole note tables
        and would otherwise dominate the summary.
        """
        if not raw or "No CAG comments" in raw:
            return ("   No CAG comments, Key Audit Matters or Emphasis of Matter content was "
                    "located in this document.")
        if raw.startswith("[tool error]") or raw.startswith("No annual report"):
            return f"   {raw}"

        out, count = [], 0
        for block in raw.split("\n\n"):
            block = block.strip()
            if not block.startswith("[table_id:"):
                continue
            count += 1
            if count > 3:
                break
            condensed = re.sub(r"\s+", " ", block)
            if len(condensed) > ExecutiveSummaryTools._MAX_AUDIT_CHARS_PER_TABLE:
                condensed = condensed[: ExecutiveSummaryTools._MAX_AUDIT_CHARS_PER_TABLE] + " […truncated]"
            out.append(f"   {condensed}")
        if not out:
            return "   No CAG/KAM/EOM tables were located in this document."
        out.append("   (Showing at most 3 items, each truncated. Call get_audit_report_highlights "
                   "for the complete text.)")
        return "\n".join(out)

    @staticmethod
    def _data_quality_flags(facts: dict) -> list[str]:
        flags = []
        inconsistent = facts.get("inconsistent_totals") or set()
        if inconsistent:
            flags.append(
                "These balance-sheet subtotals were discarded as unreliable (smaller than a "
                "component within them): " + ", ".join(sorted(inconsistent))
            )
        approximate = facts.get("approximate_averages") or set()
        if approximate:
            flags.append(
                "No prior-year comparative was available for these, so any average uses the "
                "closing balance alone: " + ", ".join(sorted(approximate))
            )
        missing = [
            label for key, label in ExecutiveSummaryTools._HEADLINE_LINES
            if facts.get("figures", {}).get(key) is None
        ]
        if missing:
            flags.append(
                "Not extracted from the face of the statements: " + ", ".join(missing)
                + ". Their absence here does not mean the entity did not report them."
            )
        return flags


# ===== SECTION 19: CAROClauseReference + AuditorReportTools =====

class CAROClauseReference:
    """
    The 21 clauses of paragraph 3 of the Companies (Auditor's Report) Order, 2020.

    Titles and requirement summaries are hand-authored from the Order itself
    (public, stable regulation text). The rules-DB copy in `cag_caro_chunks` is
    only 14 chunks split mid-sentence, so it cannot be segmented into clauses
    programmatically — it is quoted as supporting text instead.

    `patterns` are phrases distinctive enough to locate that clause's response
    inside a company's own auditor's report. MEASURED COVERAGE across the 346
    ingested reports is very uneven — statutory dues 91%, CSR 93%, PPE
    verification 62%, but several clauses under 10% — so `coverage` records what
    was actually observed and the tool reports "not located" rather than
    "not reported by the auditor".
    """

    CLAUSES: dict[str, dict] = {
        "i": {"title": "Property, Plant and Equipment and Intangible Assets",
              "requires": "Records of PPE/intangibles, physical verification and its discrepancies, title deeds of immovable property, revaluation, and benami proceedings.",
              "patterns": ["physical verification of property, plant", "physically verified by the management", "title deeds of immovable", "benami"], "coverage": "good"},
        "ii": {"title": "Inventory and working capital limits",
               "requires": "Physical verification of inventory and discrepancies of 10% or more; whether quarterly returns filed with banks against working capital limits over Rs 5 crore agree with the books.",
               "patterns": ["physical verification of inventory", "working capital limits", "quarterly returns or statements"], "coverage": "partial"},
        "iii": {"title": "Investments, guarantees, security, loans and advances",
                "requires": "Loans/advances/guarantees granted, whether terms are prejudicial, schedule of repayment, overdue amounts, renewal to settle overdues, and loans repayable on demand.",
                "patterns": ["provided loans or provided advances", "granted loans or provided advances", "in the nature of loans"], "coverage": "poor"},
        "iv": {"title": "Compliance with sections 185 and 186",
               "requires": "Compliance in respect of loans, investments, guarantees and security given to directors and related parties.",
               "patterns": ["section 185", "section 186"], "coverage": "partial"},
        "v": {"title": "Deposits",
              "requires": "Compliance with sections 73-76 and the deposit rules; whether directions of the RBI or an order of a tribunal/court has been complied with.",
              "patterns": ["deposits or amounts which are deemed to be deposits", "sections 73 to 76"], "coverage": "partial"},
        "vi": {"title": "Cost records",
               "requires": "Whether cost records prescribed under section 148(1) have been made and maintained.",
               "patterns": ["cost records", "section 148"], "coverage": "partial"},
        "vii": {"title": "Statutory dues",
                "requires": "Regularity in depositing undisputed statutory dues; arrears over six months; dues not deposited on account of a dispute with forum and amount.",
                "patterns": ["undisputed statutory dues", "provident fund, employees' state insurance", "statutory dues"], "coverage": "good"},
        "viii": {"title": "Unrecorded income surrendered in tax assessments",
                 "requires": "Transactions not recorded in the books that were surrendered or disclosed as income in income-tax assessments.",
                 "patterns": ["surrendered or disclosed as income", "not recorded in the books of account"], "coverage": "partial"},
        "ix": {"title": "Default in repayment of borrowings",
               "requires": "Default to lenders; declared a wilful defaulter; term loans applied for the stated purpose; short-term funds used for long-term purposes; funds taken to meet subsidiary obligations; loans raised on pledge of subsidiary securities.",
               "patterns": ["default in repayment of loans", "wilful defaulter", "term loans were applied"], "coverage": "poor"},
        "x": {"title": "Money raised by public offer and private placement",
              "requires": "Whether IPO/FPO money and preferential allotment/private placement proceeds were applied for the purposes for which they were raised.",
              "patterns": ["initial public offer", "preferential allotment or private placement"], "coverage": "poor"},
        "xi": {"title": "Fraud",
               "requires": "Fraud by or on the company; filing of Form ADT-4 under section 143(12); whistle-blower complaints considered.",
               "patterns": ["fraud by the company or any fraud on the company", "ADT-4", "whistle-blower"], "coverage": "partial"},
        "xii": {"title": "Nidhi company",
                "requires": "Net owned funds to deposits ratio, and maintenance of 10% unencumbered term deposits — applicable only to a Nidhi company.",
                "patterns": ["nidhi company", "net owned funds"], "coverage": "poor"},
        "xiii": {"title": "Related party transactions",
                 "requires": "Compliance with sections 177 and 188 and whether the details have been disclosed as required by the applicable accounting standards.",
                 "patterns": ["section 177 and 188", "sections 177 and 188", "related party transactions"], "coverage": "partial"},
        "xiv": {"title": "Internal audit system",
                "requires": "Whether the company has an internal audit system commensurate with its size, and whether the internal auditor's reports were considered.",
                "patterns": ["internal audit system", "internal auditors"], "coverage": "partial"},
        "xv": {"title": "Non-cash transactions with directors",
               "requires": "Non-cash transactions with directors or persons connected with them, and compliance with section 192.",
               "patterns": ["non-cash transactions", "section 192"], "coverage": "partial"},
        "xvi": {"title": "Registration under section 45-IA of the RBI Act",
                "requires": "Whether registration is required and obtained; NBFC/HFC activity without a Certificate of Registration; core investment company criteria; number of CICs in the group.",
                "patterns": ["section 45-ia", "certificate of registration", "core investment company"], "coverage": "poor"},
        "xvii": {"title": "Cash losses",
                 "requires": "Whether the company incurred cash losses in the financial year and in the immediately preceding financial year, and the amounts.",
                 "patterns": ["incurred cash losses", "cash losses"], "coverage": "partial"},
        "xviii": {"title": "Resignation of statutory auditors",
                  "requires": "Whether there has been any resignation of the statutory auditors during the year and whether the incoming auditor considered the outgoing auditor's issues.",
                  "patterns": ["resignation of the statutory auditors", "outgoing auditors"], "coverage": "poor"},
        "xix": {"title": "Material uncertainty on meeting liabilities",
                "requires": "On the basis of financial ratios, ageing and expected realisation dates, whether a material uncertainty exists about meeting liabilities falling due within one year.",
                "patterns": ["material uncertainty exists", "ageing and expected dates of realisation"], "coverage": "partial"},
        "xx": {"title": "Transfer of unspent CSR amount",
               "requires": "Transfer of unspent CSR amounts to a Fund specified in Schedule VII, or to a special account, under section 135(5)/(6).",
               "patterns": ["unspent amount", "section 135", "corporate social responsibility"], "coverage": "good"},
        "xxi": {"title": "Qualifications or adverse remarks in group companies' CARO reports",
                "requires": "For consolidated financial statements — whether any CARO report of a company included in the consolidation contains a qualification or adverse remark.",
                "patterns": ["clause (xxi) of paragraph 3", "qualifications or adverse remarks"], "coverage": "partial"},
    }

    ORDER = list(CLAUSES.keys())

    # Rule 11(g) of the Companies (Audit and Auditors) Rules, 2014 — the
    # audit-trail reporting requirement — applies for financial years beginning
    # on or after 1 April 2022, i.e. FY2022-23 onward. Measured presence in this
    # corpus tracks that exactly: 8-20% up to FY2022, then 74% / 92% / 97%.
    RULE_11G_FIRST_FY_END = 2023
    RULE_11G_PATTERNS = [
        "audit trail", "accounting software", "edit log", "feature was not enabled",
        "feature is enabled", "audit trail feature",
    ]


class AuditorReportTools:
    """
    Auditor's-report tooling: CARO 2020 clause lookup and the Rule 11(g)
    audit-trail check.

    Scope limitation, stated up front because it drives the whole design:
    CARO annexure text is only PARTIALLY ingested in this corpus. Measured
    per-clause presence ranges from ~4% to ~93% of documents. A 21-clause
    "does the auditor report on each clause" matrix would therefore be mostly
    false negatives, so this tool does not produce one. It reports what it can
    locate for the clauses asked about, and says plainly that a miss means the
    text was not found in the ingested document — NOT that the auditor failed
    to report it.
    """

    _MAX_CLAUSES_PER_CALL = 5
    _EXTRACT_CHARS = 320

    @staticmethod
    def _parse_clause_arg(clauses: str) -> list[str]:
        """'i, vii, xx' / 'all' / '' -> ordered list of canonical clause keys."""
        raw = (clauses or "").strip().lower()
        if not raw or raw == "all":
            return list(CAROClauseReference.ORDER)
        wanted = []
        for token in re.split(r"[,\s]+", raw):
            token = token.strip("() .")
            if token in CAROClauseReference.CLAUSES and token not in wanted:
                wanted.append(token)
        return wanted

    # Phrases that identify text as coming from the AUDITOR rather than from
    # management. Matched against the passage body, not against section metadata:
    # in this corpus toc_section/section_breadcrumb for auditor content is just
    # the company name or "CONSOLIDATED FINANCIAL STATEMENTS", so scoping by
    # section never fires. Without some such scoping the clause phrases match all
    # over the report — the statutory-dues clause lands in the sustainability
    # section, and "audit trail" in an unrelated funding paragraph.
    _AUDITOR_VOICE_PATTERNS = [
        "in our opinion", "we report that", "we have audited", "referred to in paragraph",
        "annexure", "companies (auditor's report) order", "caro",
    ]

    @staticmethod
    def _window_around_match(body: str, patterns: list[str]) -> str:
        """
        The passage AROUND the first matching phrase, not the head of the chunk —
        the matched sentence is often hundreds of characters in, so showing the
        chunk's opening would display text unrelated to the clause.
        """
        text = re.sub(r"\s+", " ", body).strip()
        lowered = text.lower()
        position = min(
            (lowered.find(p.lower()) for p in patterns if lowered.find(p.lower()) != -1),
            default=-1,
        )
        if position == -1:
            snippet, prefix, suffix = text[: AuditorReportTools._EXTRACT_CHARS], "", ""
        else:
            start = max(0, position - 80)
            end = min(len(text), position + AuditorReportTools._EXTRACT_CHARS - 80)
            snippet = text[start:end]
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
        return f"{prefix}{snippet}{suffix}"

    @staticmethod
    def _find_clause_evidence(
        doc_id: str, patterns: list[str], conn_reports
    ) -> tuple[str, str, bool] | None:
        """
        (source_label, extract, in_auditor_section) for the first standalone
        passage matching any pattern.

        Searched in two passes: first restricted to the auditor's report /
        annexure sections, then — only if nothing is found there — across the
        whole document, with the flag set False so the caller can say the hit
        came from elsewhere and may not be the auditor's clause response.
        """
        for restrict_to_auditor_section in (True, False):
            for table in ("text_chunks", "table_chunks"):
                column = "content" if table == "text_chunks" else "table_md"
                id_column = "chunk_id" if table == "text_chunks" else "table_id"
                scope_column = "section_breadcrumb::text" if table == "text_chunks" else "toc_section"

                clause_sql = " OR ".join([f"{column} ILIKE %s"] * len(patterns))
                params: list = [doc_id] + [f"%{p}%" for p in patterns]
                section_sql = ""
                if restrict_to_auditor_section:
                    section_sql = " AND (" + " OR ".join(
                        [f"{column} ILIKE %s"] * len(AuditorReportTools._AUDITOR_VOICE_PATTERNS)
                    ) + ")"
                    params += [f"%{p}%" for p in AuditorReportTools._AUDITOR_VOICE_PATTERNS]

                try:
                    with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(
                            f"SELECT {id_column} AS ref, {column} AS body FROM public.{table} "
                            f"WHERE doc_id = %s "
                            f"  AND ({scope_column} IS NULL OR {scope_column} NOT ILIKE '%%consolidated%%') "
                            f"  AND ({clause_sql}){section_sql} "
                            f"ORDER BY {id_column} LIMIT 1",
                            params,
                        )
                        row = cur.fetchone()
                except Exception:
                    try:
                        conn_reports.rollback()
                    except Exception:
                        pass
                    continue

                if row and row.get("body"):
                    return (
                        f"{table}:{row['ref']}",
                        AuditorReportTools._window_around_match(row["body"], patterns),
                        restrict_to_auditor_section,
                    )
        return None

    @staticmethod
    def check_caro_clauses(company: str, financial_year: str, clauses: str, conn_reports) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        wanted = AuditorReportTools._parse_clause_arg(clauses)
        if not wanted:
            return (
                f"No valid CARO clause recognised in '{clauses}'. Use roman numerals i-xxi "
                "(e.g. 'vii' or 'i, vii, xx'), or 'all'."
            )

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        truncated_request = len(wanted) > AuditorReportTools._MAX_CLAUSES_PER_CALL
        wanted = wanted[: AuditorReportTools._MAX_CLAUSES_PER_CALL]

        lines = [
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})",
            "CARO 2020 — CLAUSE REQUIREMENTS AND WHAT WAS LOCATED IN THIS REPORT (standalone only)",
            "",
            "IMPORTANT — 'Not located' means the clause's text was NOT FOUND in the ingested "
            "document. CARO annexure coverage in this corpus is partial and uneven, so a miss is "
            "NOT evidence that the auditor failed to report on the clause. Never present it as a "
            "compliance failure.",
            "",
        ]

        for key in wanted:
            clause = CAROClauseReference.CLAUSES[key]
            lines.append(f"### Clause ({key}) — {clause['title']}")
            lines.append(f"Requires: {clause['requires']}")
            evidence = AuditorReportTools._find_clause_evidence(
                match["doc_id"], clause["patterns"], conn_reports
            )
            if evidence:
                ref, extract, in_auditor_section = evidence
                if in_auditor_section:
                    lines.append(f"Located in the auditor's report / annexure [{ref}]:")
                else:
                    lines.append(
                        f"Located ELSEWHERE in the report, NOT in the auditor's report section "
                        f"[{ref}] — this may be management's own disclosure rather than the "
                        f"auditor's clause response, so read it before relying on it:"
                    )
                lines.append(f"   \"{extract}\"")
            else:
                lines.append(
                    f"Not located in the ingested text of this report "
                    f"(expected coverage for this clause across the corpus: {clause['coverage']})."
                )
            lines.append("")

        if truncated_request:
            lines.append(
                f"Only the first {AuditorReportTools._MAX_CLAUSES_PER_CALL} clauses are shown per "
                "call to stay within the context budget — ask for specific clause numbers to see "
                "the rest."
            )
        lines.append(
            "For the full text of any clause's response, call lookup_report_reference with the "
            "clause wording or the annexure note number."
        )
        return "\n".join(lines)

    @staticmethod
    def check_rule_11g(company: str, financial_year: str, conn_reports) -> str:
        """Rule 11(g) audit-trail reporting — applicable FY2022-23 onward only."""
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        fy_end = match.get("fy_end")
        header = (
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})\n"
            "RULE 11(g) — AUDIT TRAIL (accounting software) REPORTING\n"
        )

        # Reporting only became mandatory for FY2022-23 onward. Saying "not
        # found" for an earlier year would imply a failure that could not exist.
        if fy_end is not None and fy_end < CAROClauseReference.RULE_11G_FIRST_FY_END:
            return (
                header
                + f"\nNOT APPLICABLE for this financial year. Rule 11(g) of the Companies (Audit "
                f"and Auditors) Rules, 2014 applies to financial years beginning on or after "
                f"1 April 2022 (FY2022-23 onward). This report is FY{match['fy_start']}-"
                f"{str(fy_end)[-2:]}, so the auditor was not required to report on the audit "
                f"trail. Absence of an audit-trail paragraph here is expected, not a deficiency."
            )

        evidence = AuditorReportTools._find_clause_evidence(
            match["doc_id"], CAROClauseReference.RULE_11G_PATTERNS, conn_reports
        )
        if evidence is None:
            return (
                header
                + "\nNo audit-trail / accounting-software passage was located in the ingested text "
                "of this report. Rule 11(g) WAS applicable for this year, so this is a genuine "
                "'not found in the available text' result — but note that annexure coverage in "
                "this corpus is partial, so confirm against the original report before concluding "
                "the auditor omitted it."
            )

        ref, extract, in_auditor_section = evidence
        provenance = (
            f"Located in the auditor's report [{ref}]"
            if in_auditor_section
            else (
                f"Located ELSEWHERE in the report, NOT in the auditor's report section [{ref}] — "
                "this may be management's own statement rather than the auditor's Rule 11(g) "
                "opinion, so verify before relying on it"
            )
        )
        return (
            header
            + f"\n{provenance}:\n   \"{extract}\"\n\n"
            "Read the wording carefully: auditors commonly report that the audit trail feature was "
            "enabled, was NOT enabled for parts of the year, or was not enabled at the database "
            "level. Quote what this report actually says rather than summarising it as compliant."
        )


# ===== SECTION 20: GoingConcernTools =====

class GoingConcernTools:
    """
    Going-concern INDICATOR SCREEN and subsequent-events search.

    This is deliberately not a going-concern conclusion and the tool says so in
    its own output. SA 570 requires management's assessment and cash-flow
    forecasts covering at least twelve months from the reporting date; neither
    is in the ingested data. What this can do is compute, deterministically, the
    financial indicators SA 570 lists as conditions that may cast significant
    doubt, and report which are triggered.

    Everything is derived from figures already extracted by
    RatioExtractionEngine and ratios already computed by RatioTools — no new
    parsing, and no arithmetic left to the model.
    """

    _MODES = ("indicators", "subsequent_events")

    _SUBSEQUENT_EVENT_PATTERNS = [
        "events after the reporting period", "events after the balance sheet date",
        "subsequent to the balance sheet date", "subsequent events", "non-adjusting event",
        "adjusting event",
    ]

    _GOING_CONCERN_PATTERNS = [
        "going concern", "material uncertainty related to going concern",
        "material uncertainty exists",
    ]

    _STANDING_CAVEAT = (
        "THIS IS AN INDICATOR SCREEN, NOT A GOING-CONCERN CONCLUSION. SA 570 requires "
        "management's own assessment and cash-flow forecasts covering at least twelve months "
        "from the reporting date, together with mitigating factors such as undrawn facilities, "
        "shareholder or government support and refinancing plans. NONE of that is in the "
        "ingested data. Triggered indicators mean 'examine this', never 'the entity is not a "
        "going concern'. For a state-owned entity in particular, continued government support "
        "is frequently the decisive factor and is not visible here."
    )

    # (key, label, predicate(figures, ratios) -> (triggered|None, value_text, threshold_text))
    @staticmethod
    def _indicators(figures: dict, ratios: dict) -> list[tuple[str, str, str, bool | None]]:
        """[(label, value_text, threshold_text, triggered_or_None)] — None = not assessable."""
        out: list[tuple[str, str, str, bool | None]] = []

        def ratio_value(ratio_id: str) -> float | None:
            entry = ratios.get(ratio_id) or {}
            return entry.get("value") if entry.get("error") is None else None

        # Net current assets
        current_assets = figures.get("current_assets_total")
        current_liabilities = figures.get("current_liabilities_total")
        if current_assets is not None and current_liabilities is not None:
            net_current = current_assets - current_liabilities
            out.append((
                "Net current assets (working capital)",
                FinancialFactBase.format_amount(net_current),
                "negative is an indicator",
                net_current < 0,
            ))
        else:
            out.append(("Net current assets (working capital)", "not extracted", "negative is an indicator", None))

        for ratio_id, label, threshold, test in (
            ("current_ratio", "Current ratio", "< 1.0", lambda v: v < 1.0),
            ("interest_coverage_ratio", "Interest coverage ratio", "< 1.5", lambda v: v < 1.5),
        ):
            value = ratio_value(ratio_id)
            if value is None:
                out.append((label, "not computable", threshold, None))
            else:
                out.append((label, f"{value:.4f}", threshold, test(value)))

        # Accumulated losses / eroded net worth
        other_equity = figures.get("other_equity")
        if other_equity is not None:
            out.append((
                "Other equity (reserves and surplus)",
                FinancialFactBase.format_amount(other_equity),
                "negative indicates accumulated losses",
                other_equity < 0,
            ))
        else:
            out.append(("Other equity (reserves and surplus)", "not extracted",
                        "negative indicates accumulated losses", None))

        total_equity = figures.get("total_equity")
        share_capital = figures.get("equity_share_capital")
        if total_equity is not None and share_capital is not None:
            out.append((
                "Net worth vs paid-up share capital",
                f"{FinancialFactBase.format_amount(total_equity)} vs "
                f"{FinancialFactBase.format_amount(share_capital)}",
                "net worth below share capital indicates erosion",
                total_equity < share_capital,
            ))
        else:
            out.append(("Net worth vs paid-up share capital", "not extracted",
                        "net worth below share capital indicates erosion", None))

        # Operating cash flow
        operating_cash = figures.get("cash_from_operating")
        if operating_cash is not None:
            out.append((
                "Net cash from operating activities",
                FinancialFactBase.format_amount(operating_cash),
                "negative is an indicator",
                operating_cash < 0,
            ))
        else:
            out.append(("Net cash from operating activities", "not extracted",
                        "negative is an indicator", None))

        # Loss in current year (and prior year where the comparative is available)
        profit = figures.get("profit_for_period")
        if profit is not None:
            out.append((
                "Profit for the period",
                FinancialFactBase.format_amount(profit),
                "loss is an indicator",
                profit < 0,
            ))
        else:
            out.append(("Profit for the period", "not extracted", "loss is an indicator", None))

        return out

    @staticmethod
    def _auditor_flagged(doc_id: str, patterns: list[str], conn_reports) -> tuple[str, str] | None:
        """First standalone passage mentioning any pattern, with a window around the match."""
        for table, column, id_column, scope in (
            ("text_chunks", "content", "chunk_id", "section_breadcrumb::text"),
            ("table_chunks", "table_md", "table_id", "toc_section"),
        ):
            clause_sql = " OR ".join([f"{column} ILIKE %s"] * len(patterns))
            try:
                with conn_reports.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {id_column} AS ref, {column} AS body FROM public.{table} "
                        f"WHERE doc_id = %s "
                        f"  AND ({scope} IS NULL OR {scope} NOT ILIKE '%%consolidated%%') "
                        f"  AND ({clause_sql}) ORDER BY {id_column} LIMIT 1",
                        [doc_id] + [f"%{p}%" for p in patterns],
                    )
                    row = cur.fetchone()
            except Exception:
                try:
                    conn_reports.rollback()
                except Exception:
                    pass
                continue
            if row and row.get("body"):
                return (
                    f"{table}:{row['ref']}",
                    AuditorReportTools._window_around_match(row["body"], patterns),
                )
        return None

    @staticmethod
    def assess_going_concern(company: str, financial_year: str, mode: str, conn_reports, conn_rules) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        mode = (mode or "indicators").strip().lower()
        if mode not in GoingConcernTools._MODES:
            return f"Unknown mode '{mode}'. Supported: {', '.join(GoingConcernTools._MODES)}."

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        doc_id = match["doc_id"]
        header = (
            f"Document: {match['doc_name']} (doc_id={doc_id}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})\n"
            f"{UnitResolver.units_line(doc_id, conn_reports)}\n"
        )

        if mode == "subsequent_events":
            return header + GoingConcernTools._subsequent_events(doc_id, conn_reports)

        facts = FinancialFactBase.load(doc_id, conn_reports)
        figures, sources = facts["figures"], facts["sources"]

        ratios = {}
        for ratio_id in ("current_ratio", "interest_coverage_ratio"):
            try:
                ratios[ratio_id] = RatioTools._compute_ratio(ratio_id, figures)
            except Exception:
                ratios[ratio_id] = {"error": "not computable"}

        indicators = GoingConcernTools._indicators(figures, ratios)
        triggered = [i for i in indicators if i[3] is True]
        not_assessable = [i for i in indicators if i[3] is None]

        lines = [
            header.rstrip("\n"),
            "GOING-CONCERN INDICATOR SCREEN (standalone financial statements)",
            "",
            "| Indicator | Value | Threshold | Triggered? |",
            "|---|---|---|---|",
        ]
        for label, value_text, threshold_text, is_triggered in indicators:
            flag = "—" if is_triggered is None else ("**YES**" if is_triggered else "no")
            lines.append(f"| {label} | {value_text} | {threshold_text} | {flag} |")

        lines += [
            "",
            f"SUMMARY: {len(triggered)} indicator(s) triggered, "
            f"{len(indicators) - len(triggered) - len(not_assessable)} not triggered, "
            f"{len(not_assessable)} not assessable from the extracted figures.",
            "",
        ]

        flagged = GoingConcernTools._auditor_flagged(
            doc_id, GoingConcernTools._GOING_CONCERN_PATTERNS, conn_reports
        )
        lines.append("AUDITOR / MANAGEMENT REFERENCES TO GOING CONCERN IN THIS REPORT")
        if flagged:
            ref, extract = flagged
            lines.append(f"   Found [{ref}]:")
            lines.append(f"   \"{extract}\"")
            lines.append("   Read this before drawing any inference from the table above — a routine "
                         "'prepared on a going concern basis' statement is NOT a material uncertainty, "
                         "and a genuine 'Material Uncertainty Related to Going Concern' section is a "
                         "far stronger signal than any computed indicator.")
        else:
            lines.append("   No going-concern passage was located in the ingested text of this report.")
        lines.append("")

        if triggered:
            lines.append("SOURCES FOR THE FIGURES USED IN THIS SCREEN")
            for key in ("current_assets_total", "current_liabilities_total", "other_equity",
                        "total_equity", "equity_share_capital", "cash_from_operating",
                        "profit_for_period"):
                if figures.get(key) is not None:
                    lines.append("   " + FinancialFactBase.format_source(sources, key))
            lines.append("")

        requirement = GoingConcernTools._sa570_requirement(conn_rules)
        if requirement:
            lines += ["[SA 570 / Ind AS 1 REQUIREMENT — retrieved verbatim]", requirement, ""]

        lines.append(GoingConcernTools._STANDING_CAVEAT)
        return "\n".join(lines)

    @staticmethod
    def _sa570_requirement(conn_rules) -> str:
        """The going-concern requirement text from the rules DB, if present."""
        if conn_rules is None:
            return ""
        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT chunk FROM public.sa_700_chunks "
                    "WHERE chunk ILIKE %s ORDER BY chunk_id LIMIT 1",
                    ("%going concern%",),
                )
                row = cur.fetchone()
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return ""
        if not row or not row.get("chunk"):
            return ""
        text = re.sub(r"\s+", " ", row["chunk"]).strip()
        return text[:900] + (" […truncated]" if len(text) > 900 else "")

    @staticmethod
    def _subsequent_events(doc_id: str, conn_reports) -> str:
        found = GoingConcernTools._auditor_flagged(
            doc_id, GoingConcernTools._SUBSEQUENT_EVENT_PATTERNS, conn_reports
        )
        lines = ["SUBSEQUENT EVENTS / EVENTS AFTER THE REPORTING PERIOD", ""]
        if found:
            ref, extract = found
            lines.append(f"Located [{ref}]:")
            lines.append(f"   \"{extract}\"")
            lines.append("")
            lines.append(
                "This is a KEYWORD SEARCH, not a complete subsequent-events review. It returns the "
                "first matching passage only — there may be others. Distinguish adjusting from "
                "non-adjusting events (Ind AS 10) using the text itself, and call "
                "lookup_report_reference with the note number for the full disclosure."
            )
        else:
            lines.append(
                "No events-after-the-reporting-period passage was located in the ingested text of "
                "this report. That is a search miss, not evidence that no subsequent events "
                "occurred or that the entity failed to disclose them."
            )
        return "\n".join(lines)


# ===== SECTION 21: AccountAreaReference + AccountAreaTools =====

class AccountAreaReference:
    """
    Account areas outside the 7 schedules AuditScheduleReference covers.

    `coverage` is MEASURED, not guessed: each area's note_title_patterns were run
    against table_chunks across all 346 ingested reports and the percentage of
    documents with at least one hit recorded. Areas that scored below ~40% are
    NOT included here at all — shipping them would mean a tool that confidently
    reports "no note found" on data that was simply never ingested. Excluded on
    that basis: government grants (35%), exceptional items (22%), suspense
    balances (13%).

    Modelled deliberately on AuditScheduleReference so AuditRiskTools'
    _match_note_tables can be reused verbatim.
    """

    AREAS: dict[str, dict] = {
        # --- Batch A: measured >= 78% document coverage ---
        "employee_benefits": {
            "name": "Employee Benefits", "coverage_pct": 96,
            "note_title_patterns": ["employee benefit", "gratuity", "defined benefit", "provident fund"],
            "ind_as_standards": [19],
            "topic_keywords": ["defined benefit obligation", "actuarial", "discount rate", "plan assets", "gratuity"],
            "checklist": [
                "Is the defined benefit obligation supported by an actuarial valuation, and is the actuary's report dated close to the reporting date?",
                "Are the key actuarial assumptions (discount rate, salary escalation, attrition, mortality) disclosed and consistent with the prior year?",
                "Is the split between current and non-current provision presented?",
                "Are remeasurement gains and losses routed through OCI, not profit or loss (Ind AS 19)?",
                "Is plan-asset fair value disclosed separately from the obligation rather than netted without disclosure?",
            ],
        },
        "provisions_contingencies": {
            "name": "Provisions and Contingent Liabilities", "coverage_pct": 94,
            "note_title_patterns": ["provision", "contingent liabilit", "contingent asset", "commitment"],
            "ind_as_standards": [37],
            "topic_keywords": ["present obligation", "probable", "reliable estimate", "onerous", "reimbursement", "discounting"],
            "checklist": [
                "Does each provision meet all three Ind AS 37 recognition criteria — present obligation from a past event, probable outflow, reliable estimate?",
                "Is the movement (opening, additional provision, amounts used, reversed, unwinding of discount, closing) disclosed?",
                "Are contingent liabilities disclosed rather than recognised, and is the distinction from provisions applied consistently?",
                "Are long-term provisions discounted, and is the unwinding shown in finance costs?",
                "Is any expected reimbursement recognised as a separate asset and not netted against the provision?",
            ],
        },
        "csr": {
            "name": "Corporate Social Responsibility", "coverage_pct": 94,
            "note_title_patterns": ["corporate social responsibility", "csr"],
            "ind_as_standards": [37],
            "topic_keywords": ["corporate social responsibility", "section 135", "unspent"],
            "checklist": [
                "Is the amount required to be spent under section 135(5) disclosed alongside the amount actually spent?",
                "Where there is a shortfall, has the unspent amount been transferred to a Schedule VII fund or an Unspent CSR Account within the statutory timeline?",
                "Is any excess spend carried forward, and is the set-off consistent with the CSR Rules?",
                "Are ongoing-project and other-than-ongoing-project amounts distinguished?",
                "Does the CARO clause (xx) response agree with this note? (check_caro_clauses, clause xx)",
            ],
        },
        "leases": {
            "name": "Leases", "coverage_pct": 92,
            "note_title_patterns": ["lease", "right-of-use", "right of use"],
            "ind_as_standards": [116],
            "topic_keywords": ["right-of-use", "lease liability", "incremental borrowing rate", "short-term lease", "low value", "lease term"],
            "checklist": [
                "Are right-of-use assets presented separately, or within the PPE note with separate disclosure?",
                "Is the maturity analysis of lease liabilities disclosed on an undiscounted basis?",
                "Is the discount rate basis (incremental borrowing rate) disclosed?",
                "Are short-term and low-value exemptions applied and disclosed as such?",
                "Is the lease-term judgement, including extension and termination options, explained?",
            ],
        },
        "borrowings_finance": {
            "name": "Borrowings and Finance Costs", "coverage_pct": 86,
            "note_title_patterns": ["borrowing", "loans and advances from", "finance cost", "debentures"],
            "ind_as_standards": [23, 107, 109],
            "topic_keywords": ["effective interest", "security", "covenant", "default", "repayment", "capitalisation rate", "borrowing cost"],
            "checklist": [
                "Are the terms of repayment, rate of interest and nature of security disclosed for each borrowing?",
                "Has any covenant breach or default in repayment of principal or interest been disclosed?",
                "Are borrowing costs eligible for capitalisation identified and the capitalisation rate disclosed (Ind AS 23)?",
                "Is the current/non-current split correct, including the current maturities of long-term debt?",
                "Does this note agree to the face of the balance sheet? (run_tie_out_checks attempts this tie; it corroborates or reports unable to verify, never a difference)",
            ],
        },
        "fair_value": {
            "name": "Fair Value Measurement and Financial Instruments", "coverage_pct": 85,
            "note_title_patterns": ["fair value", "financial instrument", "financial asset", "financial liabilit"],
            "ind_as_standards": [107, 109, 113],
            "topic_keywords": ["fair value hierarchy", "level 3", "unobservable", "valuation technique", "expected credit loss"],
            "checklist": [
                "Is the fair value hierarchy (Level 1/2/3) disclosed by class of instrument?",
                "For Level 3, are the valuation technique, significant unobservable inputs and sensitivity disclosed?",
                "Are transfers between levels disclosed with the reason and the policy for determining transfer dates?",
                "Is the expected credit loss model and the basis of the loss allowance explained (Ind AS 109)?",
                "Are classification and measurement categories consistent with the stated business model?",
            ],
        },
        "taxation": {
            "name": "Taxation and Deferred Tax", "coverage_pct": 78,
            "note_title_patterns": ["deferred tax", "income tax", "tax expense", "tax reconciliation"],
            "ind_as_standards": [12],
            "topic_keywords": ["deferred tax asset", "temporary difference", "unused tax losses", "effective tax rate", "mat credit", "probable future taxable profit"],
            "checklist": [
                "Is a reconciliation between the accounting profit multiplied by the statutory rate and the actual tax expense disclosed (Ind AS 12)?",
                "Are deferred tax assets on unused tax losses and credits supported by convincing evidence of future taxable profit?",
                "Is the movement in deferred tax split between profit or loss and OCI?",
                "Are DTA and DTL offset only where a legally enforceable right of set-off exists with the same tax authority?",
                "Does the tax expense in this note agree to the face of the P&L? (run_tie_out_checks tests PBT - tax = PAT)",
            ],
        },
        "financial_risk": {
            "name": "Financial Risk Management", "coverage_pct": 78,
            "note_title_patterns": ["financial risk", "credit risk", "liquidity risk", "market risk", "risk management"],
            "ind_as_standards": [107],
            "topic_keywords": ["credit risk", "liquidity risk", "market risk", "sensitivity analysis", "concentration", "maturity profile"],
            "checklist": [
                "Are credit, liquidity and market risk each addressed with the entity's objectives, policies and processes?",
                "Is a maturity analysis of financial liabilities disclosed on an undiscounted contractual basis?",
                "Is a sensitivity analysis given for each material market risk (interest rate, currency, commodity, price)?",
                "Are concentrations of credit risk — single customer, government receivables, sector — disclosed?",
                "Is the ageing of trade receivables and the expected credit loss provision matrix disclosed?",
            ],
        },
        "other_income": {
            "name": "Other Income", "coverage_pct": 84,
            "note_title_patterns": ["other income"],
            "ind_as_standards": [115, 109],
            "topic_keywords": ["interest income", "dividend income", "gain on", "write back", "other non-operating"],
            "checklist": [
                "Are the components of other income disaggregated rather than shown as a single line?",
                "Are non-recurring items (write-backs of provisions, gains on disposal, insurance claims) separately identifiable?",
                "Is interest income recognised using the effective interest method?",
                "Is anything classified as other income that is in substance revenue from operations (Ind AS 115)?",
                "Does the total agree to the face of the P&L?",
            ],
        },
        # --- Batch B: measured 40-69% coverage; ship WITH an explicit caveat ---
        "related_party": {
            "name": "Related Party Transactions", "coverage_pct": 67,
            "note_title_patterns": ["related party"],
            "ind_as_standards": [24],
            "topic_keywords": ["related party", "key management personnel", "holding company", "subsidiar", "associate", "joint venture"],
            "checklist": [
                "Is the list of related parties by category disclosed (parent, subsidiaries, associates, joint ventures, KMP, entities under common control)?",
                "Are transactions and outstanding balances disclosed BY CATEGORY, with amounts, not merely described?",
                "Are terms and conditions — including whether balances are secured and the nature of consideration — disclosed?",
                "Is KMP compensation disclosed by component (short-term, post-employment, termination, share-based)?",
                "Does the CARO clause (xiii) response on sections 177 and 188 agree with this note?",
                "Government-related entity exemption (Ind AS 24): for a PSU, has the reduced disclosure been applied, and is the nature and amount of individually significant transactions still given?",
            ],
        },
        "segment": {
            "name": "Segment Reporting", "coverage_pct": 67,
            "note_title_patterns": ["segment"],
            "ind_as_standards": [108],
            "topic_keywords": ["operating segment", "chief operating decision maker", "reportable segment", "geographical", "entity-wide"],
            "checklist": [
                "Are reportable segments identified on the basis actually used by the chief operating decision maker, not a statutory default?",
                "Are the aggregation criteria disclosed where operating segments have been combined?",
                "Is a reconciliation of segment revenue, result and assets to the corresponding entity totals presented?",
                "Are entity-wide disclosures given — products and services, geographical areas, major customers over 10% of revenue?",
                "Is the measurement basis of segment information explained where it differs from the financial statements?",
            ],
        },
        "managerial_remuneration": {
            "name": "Managerial Remuneration", "coverage_pct": 44,
            "note_title_patterns": ["managerial remuneration", "remuneration to director", "key management personnel", "directors' remuneration"],
            "ind_as_standards": [24],
            "topic_keywords": ["managerial remuneration", "section 197", "sitting fees", "commission"],
            "checklist": [
                "Is remuneration disclosed by individual director or by category as required by Schedule V and section 197?",
                "Where remuneration exceeds the section 197 limits, is the required approval disclosed?",
                "Are sitting fees to non-executive and independent directors disclosed separately?",
                "Does the amount agree with the KMP compensation figure in the related-party note?",
            ],
        },
        "capital_management": {
            "name": "Capital Management", "coverage_pct": 42,
            "note_title_patterns": ["capital management"],
            "ind_as_standards": [1],
            "topic_keywords": ["capital management", "gearing", "debt equity", "dividend policy"],
            "checklist": [
                "Are the entity's objectives, policies and processes for managing capital disclosed (Ind AS 1)?",
                "Is what the entity manages as capital defined, and any externally imposed capital requirement disclosed?",
                "Is the gearing or debt-to-equity measure used by management given, with its computation?",
                "Is compliance with any externally imposed capital requirement stated?",
            ],
        },
    }

    SUPPORTED = list(AREAS.keys())
    # Deliberately not built — measured coverage too low to report on honestly.
    EXCLUDED = {
        "government_grants": 35, "exceptional_items": 22, "suspense_balances": 13,
    }

    @staticmethod
    def resolve(raw: str) -> str | None:
        """Map free text ('RPT', 'deferred tax', 'employee benefits') to an area key."""
        if not raw:
            return None
        text = raw.strip().lower().replace("-", " ").replace("_", " ")
        aliases = {
            "rpt": "related_party", "related parties": "related_party",
            "dta": "taxation", "deferred tax": "taxation", "tax": "taxation",
            "esop": "employee_benefits", "gratuity": "employee_benefits",
            "contingent liabilities": "provisions_contingencies",
            "provisions": "provisions_contingencies",
            "financial instruments": "fair_value",
            "risk": "financial_risk", "segment reporting": "segment",
        }
        if text in aliases:
            return aliases[text]
        for key, definition in AccountAreaReference.AREAS.items():
            if text == key.replace("_", " ") or text in definition["name"].lower():
                return key
        for key, definition in AccountAreaReference.AREAS.items():
            if any(p in text for p in definition["note_title_patterns"]):
                return key
        return None


class AccountAreaTools:
    """
    Per-area note review: the company's own filed note + the relevant Ind AS
    paragraphs + a static audit checklist.

    Reuses AuditRiskTools._match_note_tables verbatim for the company side, and
    the same rules-DB query shape as get_audit_requirements for the standards
    side (generalised here to take standards+keywords rather than a schedule key).

    ONE area per call and a hard character cap — the same discipline the
    audit-risk playbook enforces after a broad query once fetched all seven
    schedules in a single turn and exceeded the model's context window.
    """

    # Budgets, in characters (~4 chars per token). The note and the standards
    # text are each capped, and the assembled output is capped again — without
    # the outer cap a document with several matching note tables plus eight long
    # Ind AS paragraphs runs past 13,000 characters, which is the same
    # context-pressure mistake the audit-risk playbook exists to prevent.
    _MAX_NOTE_CHARS = 2200
    _MAX_REQUIREMENT_CHARS = 1800
    _MAX_PARAGRAPH_CHARS = 500
    _MAX_TOTAL_CHARS = 5200

    @staticmethod
    def _ind_as_requirements(standards: list[int], keywords: list[str], conn_rules) -> str:
        if conn_rules is None:
            return ""
        try:
            with conn_rules.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                keyword_clause = " OR ".join(
                    ["section_title ILIKE %s"] * len(keywords) + ["text ILIKE %s"] * len(keywords)
                )
                params = [standards] + [f"%{k}%" for k in keywords] * 2
                cur.execute(
                    "SELECT standard_number, section_title, paragraph_no, text "
                    "FROM public.ind_as_chunks WHERE standard_number = ANY(%s) "
                    f"  AND ({keyword_clause}) ORDER BY standard_number, seq LIMIT 8",
                    params,
                )
                rows = cur.fetchall()
                if not rows:
                    cur.execute(
                        "SELECT standard_number, section_title, paragraph_no, text "
                        "FROM public.ind_as_chunks WHERE standard_number = ANY(%s) "
                        "ORDER BY standard_number, seq LIMIT 5",
                        (standards,),
                    )
                    rows = cur.fetchall()
        except Exception:
            try:
                conn_rules.rollback()
            except Exception:
                pass
            return ""
        parts, budget = [], AccountAreaTools._MAX_REQUIREMENT_CHARS
        for row in rows:
            if budget <= 0:
                break
            text = re.sub(r"\s+", " ", row.get("text") or "").strip()
            if len(text) > AccountAreaTools._MAX_PARAGRAPH_CHARS:
                text = text[: AccountAreaTools._MAX_PARAGRAPH_CHARS] + " […truncated]"
            entry = (
                f"[Ind AS {row['standard_number']} | {row.get('section_title', '')} | "
                f"para {row.get('paragraph_no', '?')}] {text}"
            )
            parts.append(entry)
            budget -= len(entry)
        return "\n\n".join(parts)

    @staticmethod
    def review_account_area(company: str, financial_year: str, area: str, conn_reports, conn_rules) -> str:
        if conn_reports is None:
            return "[tool error] Reports database is not configured."

        key = AccountAreaReference.resolve(area)
        if key is None:
            excluded = ", ".join(
                f"{k.replace('_', ' ')} ({v}%)" for k, v in AccountAreaReference.EXCLUDED.items()
            )
            return (
                f"Unknown or unsupported account area '{area}'.\n"
                f"Supported: {', '.join(AccountAreaReference.SUPPORTED)}.\n"
                f"Deliberately NOT supported because the underlying notes are present in too few "
                f"ingested reports to review reliably: {excluded}. For those, use "
                f"search_company_disclosures or lookup_report_reference instead — and say that "
                f"coverage is limited rather than reporting an absence as a finding."
            )

        definition = AccountAreaReference.AREAS[key]

        match = DocumentResolver._resolve_document(company, financial_year, conn_reports)
        if match is None:
            return f"No annual report found for company '{company}' (financial year '{financial_year}')."
        if isinstance(match, list):
            return DocumentResolver.format_ambiguous(
                company, financial_year, match, ask="the exact company and financial year"
            )

        lines = [
            f"Document: {match['doc_name']} (doc_id={match['doc_id']}, company={match['company']}, "
            f"FY{match['fy_start']}-{str(match['fy_end'])[-2:]})",
            UnitResolver.units_line(match["doc_id"], conn_reports),
            f"ACCOUNT AREA REVIEW — {definition['name']} (standalone only)",
            f"Relevant standards: {', '.join(f'Ind AS {n}' for n in definition['ind_as_standards'])}",
        ]
        if definition["coverage_pct"] < 70:
            lines.append(
                f"COVERAGE CAVEAT: a note for this area was found in only "
                f"{definition['coverage_pct']}% of the ingested reports. If nothing is found below, "
                f"treat that as a gap in the ingested data, NOT as the company failing to disclose."
            )
        lines.append("")

        rows = AuditRiskTools._match_note_tables(
            match["doc_id"], definition["note_title_patterns"], conn_reports
        )
        lines.append("1. THE COMPANY'S OWN FILED NOTE")
        if rows:
            budget = AccountAreaTools._MAX_NOTE_CHARS
            for row in rows:
                if budget <= 0:
                    lines.append("   […further matching notes omitted to stay within the context budget]")
                    break
                title = row.get("table_description") or row.get("table_title") or ""
                body = (row.get("table_md") or "")[:budget]
                budget -= len(body)
                lines.append("   " + SourceRef.table(row["table_id"], row.get("page_ocr_start"), title))
                lines.append(body)
                lines.append("")
        else:
            lines.append(
                f"   No note matching {definition['note_title_patterns']} was located in this "
                f"document's ingested tables. This is a retrieval miss, not evidence of "
                f"non-disclosure — confirm against the original report before saying anything "
                f"about the company's compliance."
            )
            lines.append("")

        requirements = AccountAreaTools._ind_as_requirements(
            definition["ind_as_standards"], definition["topic_keywords"], conn_rules
        )
        lines.append("2. RETRIEVED IND AS REQUIREMENTS")
        if requirements:
            lines.append(requirements)
        else:
            lines.append("   No relevant paragraphs were retrieved from the rules database.")
        lines.append("")

        lines.append("3. AUDIT CHECKLIST FOR THIS AREA")
        for i, item in enumerate(definition["checklist"], start=1):
            lines.append(f"   {i}. {item}")
        lines.append("")

        lines.append(
            "CITATION RULE — every Ind AS paragraph you cite for this area MUST appear in section 2 "
            "above. Do not supply a standard or paragraph number from memory. Any observation not "
            "grounded in the retrieved text must be labelled '(general audit judgment, not from a "
            "retrieved Ind AS paragraph)'. The checklist in section 3 is a prompt for enquiry, not "
            "a set of findings — do not report a checklist item as a deficiency unless the note in "
            "section 1 actually shows it."
        )

        output = "\n".join(lines)
        if len(output) > AccountAreaTools._MAX_TOTAL_CHARS:
            output = (
                output[: AccountAreaTools._MAX_TOTAL_CHARS]
                + "\n[truncated to stay within the context budget — call lookup_report_reference "
                  "with the note number for the full note text]"
            )
        return output


# ===== SECTION 14: ToolRegistry =====

class ToolRegistry:
    """
    Builds yukta.Tool objects wrapping the tool-executor classes above so
    they can be registered on a yukta ToolProcessor / Agent. Migrated from
    yukta_tools.py's build_yukta_tools() function — binds the per-request
    context (shared DB connection + current query's retrieved_chunks) via
    closures, since yukta tool functions are called as
    `tool.function(**llm_supplied_args)` with no way to inject extra
    context per call.
    """

    @staticmethod
    def _format_chunks(chunks: list[dict]) -> str:
        """
        Format retrieved chunks into numbered [Chunk N] entries with source
        metadata — what the fs-agent reads and cites. Canonical copy: this
        used to live in agent.py's Orchestrator (built from Stage B's
        pre-computed chunk list before the Evidence Agent was constructed);
        now that retrieval itself is a tool call (_search_knowledge_base
        below) rather than a Python pipeline stage, formatting has to happen
        at the point the chunks are produced — i.e. here.
        """
        lines = []
        for i, chunk in enumerate(chunks, start=1):
            label = chunk.get("table_label", chunk.get("table_name", "Unknown"))
            content = chunk.get("content", "")

            if chunk.get("table_name") in Config.HTML_CONTENT_TABLES:
                content = Reranker._strip_html(content)

            # Lead the citation with the fields a reader can actually navigate by.
            # These used to appear only as incidental k=v pairs buried in the
            # metadata string, so the model cited "[Chunk 3]" and nothing else.
            # The rules DB has no page numbers — standard + section + paragraph
            # IS the citable reference here, and paragraph_no is a real column.
            cite_parts = []
            if chunk.get("standard_number") not in (None, ""):
                cite_parts.append(f"Ind AS {chunk['standard_number']}")
            elif chunk.get("doc_name"):
                cite_parts.append(str(chunk["doc_name"]))
            else:
                cite_parts.append(str(label))
            for key, prefix in (("section_title", ""), ("paragraph_no", "para ")):
                value = chunk.get(key)
                if value not in (None, ""):
                    cite_parts.append(f"{prefix}{value}")
            citation = ", ".join(cite_parts)

            skip_keys = {
                "table_name", "table_label", "id", "content",
                "similarity", "_matched_hint", "rerank_score",
                # already promoted into the citation above
                "standard_number", "section_title", "paragraph_no", "doc_name",
            }
            meta_parts = [
                f"{k}={v}" for k, v in chunk.items()
                if k not in skip_keys and v is not None and v != ""
            ]
            meta_str = ("; " + ", ".join(meta_parts)) if meta_parts else ""
            lines.append(f"[Chunk {i}] (Source: {citation}{meta_str})\n{content}")

        return "\n\n".join(lines)

    # A single question needs one rules-DB search. Left to its own judgement the
    # model re-searches with reworded queries — measured at 4 and then 6 calls on
    # "What does Ind AS 115 say about revenue recognition?", turning a 2-round /
    # 31k-token answer into 7 rounds / 109k. Reworded queries return near-identical
    # chunks, so the extra rounds buy nothing. Prompt-level wording did not hold
    # (tightening it made the model search MORE), so the budget is enforced here.
    _SEARCH_CALL_BUDGET = 1

    @staticmethod
    def _search_budget_exceeded(chunks_out: list) -> str:
        """Returned instead of a fresh search once the per-question budget is spent."""
        if chunks_out:
            return (
                "SEARCH BUDGET REACHED — no further rules-DB search will run for this "
                "question. Reworded queries return near-identical chunks. The chunks you "
                "already retrieved are below; ANSWER FROM THEM NOW. If they only partly "
                "cover the question, say so explicitly and cite what they do state.\n\n"
                + ToolRegistry._format_chunks(chunks_out)
            )
        return (
            "SEARCH BUDGET REACHED and no chunks were retrieved. Do not search again. "
            "State plainly that the knowledge base returned nothing for this question."
        )

    @staticmethod
    def _search_knowledge_base(query: str, target_tables, conn, chunks_out: list) -> str:
        """
        Executor for the `search_knowledge_base` tool — embed -> hybrid
        search across Config.TABLE_CONFIG -> rerank -> top TOP_K_OVERALL.
        Mutates `chunks_out` IN PLACE (chunks_out[:] = ...) rather than
        reassigning it, so the `validate_answer` tool's closure — which
        captures the same list object by reference — sees this call's
        results too, even though validate_answer was registered before this
        tool ever ran.
        """
        if not query or not query.strip():
            return "Please provide a non-empty query to search the knowledge base."

        metadata_hints: dict = {}
        if isinstance(target_tables, list):
            for tt in target_tables:
                if isinstance(tt, dict):
                    tbl = tt.get("table_name")
                    filters = tt.get("filters", [])
                    if tbl and isinstance(filters, list):
                        metadata_hints[tbl] = filters

        try:
            query_vector = Embedder.embed_text(query)
        except Exception as e:
            return f"[tool error] Embedding request failed: {e}"

        try:
            all_results, _ = Database.search_all_tables_hybrid(
                conn, query_vector, metadata_hints=metadata_hints if metadata_hints else None
            )
        except Exception as e:
            return f"[tool error] Database search failed: {e}"

        pre_rerank = all_results[:Config.TOP_K_PRE_RERANK]
        try:
            reranked = Reranker.rerank_chunks(query, pre_rerank) if pre_rerank else []
        except Exception as e:
            return f"[tool error] Reranker failed: {e}"

        top_results = reranked[:Config.TOP_K_OVERALL]
        chunks_out[:] = top_results

        if not top_results:
            return "No relevant chunks found in the rules database for this query."

        return ToolRegistry._format_chunks(top_results)

    def build_tools(
        self, conn, initial_chunks: list[dict], conn_reports=None,
        allowed: set[str] | None = None,
    ) -> list:
        from yukta import Tool, ToolParameter, ToolType

        # build_tools runs once per request; drop any statement rows cached for a
        # previous request so figures can never leak across users or outlive the
        # connection they were read through.
        FinancialFactBase.reset()
        UnitResolver.reset()

        core = CoreTools()
        # Per-request counter for the rules-DB search budget (see
        # _SEARCH_CALL_BUDGET). Scoped to this build_tools call, so it resets
        # with every new question and never leaks between requests.
        search_calls = {"n": 0}

        tools = [
            Tool(
                name="search_knowledge_base",
                description=(
                    "Semantic search over the rules DB (Ind AS, SA 700, CARO/CAG, EAC, Schedule III). The "
                    "evidence source for standards questions. Returns numbered [Chunk N] results with "
                    "source metadata — cite them by number. Call this ONCE for the whole"
                    " question; re-searching with reworded queries returns near-identical"
                    " chunks and wastes a full round."
                ),
                parameters=[
                    ToolParameter(
                        name="query", type="string",
                        description=(
                            "Dense-search-optimized query text: expand abbreviations both ways "
                            "(e.g. 'Ind AS' -> 'Indian Accounting Standards'), add domain "
                            "synonyms (e.g. 'inventory' -> 'stock, goods'), keep specific "
                            "standard/section numbers."
                        ),
                        required=True,
                    ),
                    ToolParameter(
                        name="target_tables", type="array",
                        description=(
                            "Optional routing hints, [] to search all tables. Shape: "
                            "[{table_name, filters:[{column, operator, value}]}], operator '=' or "
                            "'ILIKE'. Tables: public.ind_as_chunks / ind_as_appendix (filter "
                            "standard_number or doc_name), sa_700_chunks, cag_caro_chunks / "
                            "cag_caro_tables / cag_directions_chunks (filter doc_name), "
                            "eac_general / eac_query / eac_table (filter query_no), "
                            "schedule_iii_chunks / schedule_iii_table_chunks."
                        ),
                        required=False,
                    ),
                ],
                tool_type=ToolType.CUSTOM,
                function=lambda query="", target_tables=None: (
                    ToolRegistry._search_budget_exceeded(initial_chunks)
                    if search_calls["n"] >= ToolRegistry._SEARCH_CALL_BUDGET
                    else (
                        search_calls.__setitem__("n", search_calls["n"] + 1)
                        or ToolRegistry._search_knowledge_base(
                            query, target_tables, conn, initial_chunks
                        )
                    )
                ),
            ),
            Tool(
                name="get_eac_opinion_by_topic",
                description=(
                    "Search ICAI Expert Advisory Committee opinions by topic. Searches the structured "
                    "subject column — more precise than semantic search for topic-based EAC lookups."
                ),
                parameters=[
                    ToolParameter(
                        name="topic",
                        type="string",
                        description=(
                            "The topic or subject to search for in EAC opinions. "
                            "e.g. 'deferred tax', 'foreign exchange loss', 'impairment of goodwill'"
                        ),
                        required=True,
                    ),
                ],
                tool_type=ToolType.CUSTOM,
                function=lambda topic="": core.get_eac_opinion_by_topic(topic, conn),
            ),
            Tool(
                name="compare_standards",
                description=(
                    "Fetch chunks from two standards side by side. Use when the query explicitly asks to "
                    "compare or differentiate two standards."
                ),
                parameters=[
                    ToolParameter(
                        name="standard_1", type="string",
                        description="First standard. e.g. 'Ind AS 2', 'SA 700', 'CARO 2020'",
                        required=True,
                    ),
                    ToolParameter(
                        name="standard_2", type="string",
                        description="Second standard. e.g. 'IAS 2', 'SA 705', 'CARO 2016'",
                        required=True,
                    ),
                ],
                tool_type=ToolType.CUSTOM,
                function=lambda standard_1="", standard_2="": core.compare_standards(
                    standard_1, standard_2, conn
                ),
            ),
            Tool(
                name="check_amendment_status",
                description=(
                    "Check whether a standard, notification or clause is currently in force or has been "
                    "amended/superseded. Reads effective dates, is_amended, status and version."
                ),
                parameters=[
                    ToolParameter(
                        name="document", type="string",
                        description="Document name or standard to check. e.g. 'CARO 2020', 'SA 700', 'Ind AS 115'",
                        required=True,
                    ),
                    ToolParameter(
                        name="clause", type="string",
                        description="Specific clause or section (optional). e.g. '3(ix)', 'paragraph 45'",
                        required=False,
                    ),
                ],
                tool_type=ToolType.CUSTOM,
                function=lambda document="", clause=None: core.check_amendment_status(
                    document, clause, conn
                ),
            ),
            Tool(
                name="validate_answer",
                description=(
                    "Re-read specific retrieved chunks to verify your claims before finalizing. Pass the "
                    "chunk indices you plan to cite and the claims you are making. Works only on "
                    "search_knowledge_base chunks, not reports-DB tool output."
                ),
                parameters=[
                    ToolParameter(
                        name="chunk_indices", type="array",
                        description=(
                            "1-indexed chunk numbers (matching [Chunk N] in the prompt). "
                            "e.g. [1, 3, 5] to re-read chunks 1, 3, and 5."
                        ),
                        required=True,
                    ),
                    ToolParameter(
                        name="claims", type="array",
                        description="The specific claims you want to verify against those chunks.",
                        required=True,
                    ),
                ],
                tool_type=ToolType.CUSTOM,
                function=lambda chunk_indices=None, claims=None: core.validate_answer(
                    chunk_indices or [], claims or [], initial_chunks
                ),
            ),
            Tool(
                name="cross_reference_lookup",
                description=(
                    "Follow a cross-reference found inside a chunk (e.g. 'see paragraph 45 of Ind AS 36') "
                    "and fetch the referenced content."
                ),
                parameters=[
                    ToolParameter(
                        name="standard", type="string",
                        description="The referenced standard to look up. e.g. 'Ind AS 36', 'SA 700', 'Schedule III'",
                        required=True,
                    ),
                    ToolParameter(
                        name="paragraph", type="string",
                        description="Paragraph or section reference (optional). e.g. '45', 'para 12', 'Part I'",
                        required=False,
                    ),
                    ToolParameter(
                        name="topic", type="string",
                        description="The topic being referenced. e.g. 'impairment of assets', 'auditor report format'",
                        required=True,
                    ),
                ],
                tool_type=ToolType.CUSTOM,
                function=lambda standard="", paragraph=None, topic="": core.cross_reference_lookup(
                    standard, paragraph, topic, conn
                ),
            ),
        ]

        if conn_reports is not None:
            compliance = ComplianceTools()
            ratio_tools = RatioTools()
            audit_risk = AuditRiskTools()
            trend = TrendAnalysisTools()
            disclosure_search = DisclosureSearchTools()
            accounting_policy = AccountingPolicyTools()
            report_reference = ReportReferenceTools()

            tools.append(
                Tool(
                    name="search_company_disclosures",
                    description=(
                        "Semantic search over a named company's own annual-report narrative (MD&A, "
                        "contingencies, other prose). For the 11 canonical accounting-policy topics use "
                        "get_accounting_policy_note instead — it is more precise. Searches the reports DB, not "
                        "the standards KB."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2023'",
                            required=True,
                        ),
                        ToolParameter(
                            name="topic", type="string",
                            description=(
                                "The policy/disclosure topic to search for. "
                                "e.g. 'related party transactions', 'contingent liabilities', "
                                "'management discussion and analysis'"
                            ),
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", topic="": (
                        disclosure_search.search_company_disclosures(
                            company, financial_year, topic, conn_reports
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="get_accounting_policy_note",
                    description=(
                        "A company's own filed accounting-policy note for one of 11 canonical topics. "
                        "Deterministic heading match, more precise than search_company_disclosures for these. "
                        "If the topic is not one of the 11 the tool says so."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2023'",
                            required=True,
                        ),
                        ToolParameter(
                            name="topic", type="string",
                            description=(
                                "One of: revenue recognition, depreciation, inventory "
                                "valuation, employee benefits, foreign currency, taxation, "
                                "financial instruments, impairment, leases, borrowing costs, "
                                "provisions."
                            ),
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", topic="": (
                        accounting_policy.get_accounting_policy_note(
                            company, financial_year, topic, conn_reports
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="compute_materiality",
                    description=(
                        "Audit materiality benchmarks against revenue, total assets, profit before tax and net "
                        "worth, with performance-materiality and clearly-trivial ranges. Deliberately does not "
                        "pick one benchmark — SA 320 makes that the auditor's judgment. Call ONCE."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC', 'RVNL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24', '2024'",
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="": (
                        MaterialityTools.compute_materiality(
                            company, financial_year, conn_reports, conn
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="review_account_area",
                    description=(
                        "Review ONE account area outside the 7 schedules: the company's filed note, the "
                        "retrieved Ind AS paragraphs, and an audit checklist. ONE area per call — never loop "
                        "over areas. Government grants, exceptional items and suspense balances are unsupported "
                        "because too few reports contain them."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC'", required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24'", required=True,
                        ),
                        ToolParameter(
                            name="area", type="string",
                            description=(
                                "One account area, e.g. 'related_party', 'taxation', "
                                "'employee_benefits', 'leases', 'segment'. Common aliases such as "
                                "'RPT' or 'deferred tax' are accepted."
                            ),
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", area="": (
                        AccountAreaTools.review_account_area(
                            company, financial_year, area, conn_reports, conn
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="assess_going_concern",
                    description=(
                        "Going-concern INDICATOR SCREEN (working capital, current ratio, interest cover, "
                        "accumulated losses, operating cash flow, losses) or a subsequent-events search. A "
                        "screen, NEVER a conclusion — management's assessment and forecasts are not in the "
                        "data. Call once per mode."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC'", required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24'", required=True,
                        ),
                        ToolParameter(
                            name="mode", type="string",
                            description="'indicators' (default) or 'subsequent_events'.",
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", mode="indicators": (
                        GoingConcernTools.assess_going_concern(
                            company, financial_year, mode, conn_reports, conn
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="check_caro_clauses",
                    description=(
                        "CARO 2020 clause requirements plus whatever this company's report was found to say on "
                        "them. Max 5 clauses per call. 'Not located' means absent from the ingested text — "
                        "NEVER that the auditor failed to report."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC'", required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24'", required=True,
                        ),
                        ToolParameter(
                            name="clauses", type="string",
                            description=(
                                "Roman-numeral clause numbers, comma separated (e.g. 'vii' or "
                                "'i, vii, xx'), or 'all' for the first five. Default 'all'."
                            ),
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", clauses="all": (
                        AuditorReportTools.check_caro_clauses(
                            company, financial_year, clauses, conn_reports
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="check_rule_11g",
                    description=(
                        "Rule 11(g) audit-trail / accounting-software reporting for a company and year. Returns "
                        "NOT APPLICABLE before FY2022-23, when the rule did not yet apply."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC'", required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24'", required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="": (
                        AuditorReportTools.check_rule_11g(company, financial_year, conn_reports)
                    ),
                )
            )
            tools.append(
                Tool(
                    name="summarize_annual_report",
                    description=(
                        "Audit-oriented executive summary in ONE call: framework, key financials with YoY, "
                        "tie-out status, auditor CAG/KAM/EOM, policies disclosed, the company's own highlights, "
                        "and data-quality flags. Do not call the underlying tools separately."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC', 'IOCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24', '2024'",
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="": (
                        ExecutiveSummaryTools.summarize_annual_report(
                            company, financial_year, conn_reports, conn
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="run_tie_out_checks",
                    description=(
                        "Cross-statement arithmetic identities: assets = equity + liabilities, subtotal "
                        "integrity, revenue + other income - expenses = PBT, PBT - tax = PAT, and closing cash "
                        "vs balance-sheet cash. ALSO reconciles seven notes to the face of the balance sheet "
                        "(PPE, inventories, trade receivables, trade payables, borrowings, investments, cash) "
                        "— each PASS or NOT AVAILABLE, never FAIL. Returns PASS/FAIL/NOT AVAILABLE with both "
                        "sides. Call ONCE."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'SAIL', 'NTPC', 'IOCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2023-24', '2023-24', '2024'",
                            required=True,
                        ),
                        ToolParameter(
                            name="scope", type="string",
                            description=(
                                "Which checks to run: 'all' (default), 'balance_sheet', "
                                "'profit_loss', 'cash_flow', or 'notes' (the note-to-face "
                                "reconciliations only)."
                            ),
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", scope="all": (
                        TieOutTools.run_tie_out_checks(company, financial_year, scope, conn_reports)
                    ),
                )
            )
            tools.append(
                Tool(
                    name="lookup_report_reference",
                    description=(
                        "Follow a note number ('Note 45') or exact phrase to the real content in a company's "
                        "annual report. Literal matching over BOTH data tables and narrative text, not "
                        "semantic. Use for topics outside the 7 schedules. Call once per topic; if it returns "
                        "nothing, fall back once to search_company_disclosures, then answer."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2023'",
                            required=True,
                        ),
                        ToolParameter(
                            name="reference", type="string",
                            description=(
                                "A note number (e.g. 'Note 45', '45.2.1') or an exact phrase "
                                "(e.g. 'related party transactions', 'contingent liabilities')."
                            ),
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", reference="": (
                        report_reference.lookup_report_reference(
                            company, financial_year, reference, conn_reports
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="get_reporting_framework",
                    description=(
                        "Which financial reporting framework a company used, taken from its own Statement of "
                        "Compliance / Basis of Preparation. Returns citable chunk_id and page."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2023'",
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="": DocumentResolver._get_reporting_framework(
                        company, financial_year, conn_reports
                    ),
                )
            )
            tools.append(
                Tool(
                    name="check_statement_compliance",
                    description=(
                        "Compare a company's actual statements against the Schedule III / Ind AS 7 line items "
                        "required for its framework. LINE-ITEM COMPLETENESS ONLY — does not verify "
                        "classification, measurement basis or disclosure adequacy. Determines the framework "
                        "internally."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2023'",
                            required=True,
                        ),
                        ToolParameter(
                            name="statement_type", type="string",
                            description=(
                                "Which statement to check: 'balance_sheet', 'profit_loss', "
                                "'cash_flow', 'statement_of_equity', or 'all' (default) to check "
                                "every statement in one call."
                            ),
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", statement_type="all": (
                        compliance._check_statement_compliance(
                            company, financial_year, statement_type, conn_reports, conn
                        )
                    ),
                )
            )
            tools.append(
                Tool(
                    name="compute_ratio_analysis",
                    description=(
                        "33 financial ratios computed by deterministic Python from a company's filed "
                        "statements. Use to CALCULATE a ratio value for a named company and year. For a formula "
                        "with no company/year use get_ratio_formula. Call ONCE per request."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2023'",
                            required=True,
                        ),
                        ToolParameter(
                            name="category", type="string",
                            description=(
                                "Which ratios to compute: 'liquidity', 'leverage', 'coverage', "
                                "'activity', 'profitability', 'all' (default, all 33), or a specific "
                                "ratio name."
                            ),
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", category="all": (
                        ratio_tools.compute_ratio_analysis(company, financial_year, category, conn_reports)
                    ),
                )
            )
            tools.append(
                Tool(
                    name="get_ratio_formula",
                    description=(
                        "Formula and interpretation for a ratio — no company, no database, and no 'ideal' or "
                        "benchmark level (industry-dependent; not something this tool states). Use for "
                        "'what is the formula for X', 'how is X calculated', 'what does X mean'. Accepts a "
                        "ratio name, a category, or 'all'."
                    ),
                    parameters=[
                        ToolParameter(
                            name="ratio_name", type="string",
                            description=(
                                "A specific ratio name (e.g. 'current ratio', 'debt-to-equity'), a "
                                "category ('liquidity', 'leverage', 'coverage', 'activity', "
                                "'profitability', 'market'), or 'all' (default)."
                            ),
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda ratio_name="all": ratio_tools.get_ratio_formula(ratio_name),
                )
            )
            tools.append(
                Tool(
                    name="get_audit_report_highlights",
                    description=(
                        "The auditor's own CAG comments, Key Audit Matters and Emphasis of Matter from a "
                        "company's statutory audit report, standalone only. Highest-signal, lowest-volume "
                        "source — call FIRST for a broad risk review."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2024-25'",
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="": (
                        audit_risk.get_audit_report_highlights(company, financial_year, conn_reports)
                    ),
                )
            )
            tools.append(
                Tool(
                    name="get_schedule_note",
                    description=(
                        "A company's actual filed note/schedule table for one of the 7 supported schedules, "
                        "standalone only. Use TOGETHER with get_audit_requirements."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description="Financial year. e.g. 'FY2022-23', '2022-23', '2024-25'",
                            required=True,
                        ),
                        ToolParameter(
                            name="schedule", type="string",
                            description=(
                                "Which schedule to fetch: " + ", ".join(AuditScheduleReference.SUPPORTED_SCHEDULES)
                            ),
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", schedule="": (
                        audit_risk.get_schedule_note(company, financial_year, schedule, conn_reports)
                    ),
                )
            )
            tools.append(
                Tool(
                    name="get_audit_requirements",
                    description=(
                        "The relevant Ind AS paragraph text for one of the 7 supported schedules, from the "
                        "rules DB. Use TOGETHER with get_schedule_note. Every Ind AS paragraph you cite must "
                        "appear in this output."
                    ),
                    parameters=[
                        ToolParameter(
                            name="schedule", type="string",
                            description=(
                                "Which schedule's audit requirements to fetch: "
                                + ", ".join(AuditScheduleReference.SUPPORTED_SCHEDULES)
                            ),
                            required=True,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda schedule="": audit_risk.get_audit_requirements(schedule, conn),
                )
            )
            tools.append(
                Tool(
                    name="get_multi_year_trend",
                    description=(
                        "Multi-year line-item trend: year-over-year absolute and %, CAGR, and a Significant "
                        "flag, computed deterministically. financial_year accepts a range, a single FY, or "
                        "blank for a 3-year default. Call once per statement_type."
                    ),
                    parameters=[
                        ToolParameter(
                            name="company", type="string",
                            description="Company name. e.g. 'Coal India', 'ONGC', 'BPCL'",
                            required=True,
                        ),
                        ToolParameter(
                            name="financial_year", type="string",
                            description=(
                                "A range ('2023-2025'), a single FY ('FY2024-25'), or omit/blank "
                                "for the default: a minimum 3-year window ending at the latest "
                                "available report for this company."
                            ),
                            required=False,
                        ),
                        ToolParameter(
                            name="statement_type", type="string",
                            description=(
                                "Which statement to analyze: 'balance_sheet', 'profit_loss', "
                                "'cash_flow', 'statement_of_equity', or 'all' (default — bundles "
                                "the first three)."
                            ),
                            required=False,
                        ),
                    ],
                    tool_type=ToolType.CUSTOM,
                    function=lambda company="", financial_year="", statement_type="all": (
                        trend.get_multi_year_trend(company, financial_year, statement_type, conn_reports)
                    ),
                )
            )

        # Every tool schema is re-serialised into EVERY LLM call and every
        # tool-calling iteration — all 24 came to ~4,879 tokens, 57% of a call,
        # even though a materiality question only ever uses one of them. When the
        # caller knows which tools this query can need (Orchestrator._select_tools,
        # driven by the same playbook router that picks the prompt), send only
        # those. `allowed=None` keeps the full set, which is what the kill switch
        # and every other caller relies on.
        if allowed is not None:
            tools = [t for t in tools if t.name in allowed]

        return tools
