"""Settings for the Trial Balance mode. Import `settings` everywhere; never call
os.getenv directly outside this module.

Every name below is prefixed TB_ (or reuses a name the gateway's .env.example
already reserves for this mode, e.g. TB_GENERATION_BASE_URL) to avoid colliding
with another mode's identically-shaped env vars living in the same process
environment -- Financial Statement's own config already owns the bare
DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/LLM_BASE_URL names for its own,
different database and LLM endpoint.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# The gateway's own app/main.py already loads the repo's .env before importing any
# mode; this is a harmless, idempotent safety net for standalone use (tests, a
# script run directly against this package), matching the pattern every other
# mode's own config module already follows (e.g. financial_statement/pipeline/
# tools_fs.py's bare load_dotenv()).
load_dotenv()

_PIPELINE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    # Reuses the gateway's already-reserved names (kept separate from SAR's
    # GENERATION_BASE_URL/GENERATION_MODEL, which point at a different
    # endpoint/model) rather than inventing new ones.
    LLM_BASE_URL: str = os.getenv("TB_GENERATION_BASE_URL", "http://10.10.180.48:30004")
    LLM_MODEL: str = os.getenv("TB_GENERATION_MODEL", "gemma-4-26b-a4b-it")
    LLM_API_KEY: str = os.getenv("TB_LLM_API_KEY", "not-needed")
    HTTP_TIMEOUT: float = float(os.getenv("TB_HTTP_TIMEOUT", "120"))

    # Backend container's root filesystem is read-only; only the artha-data named
    # volume (mounted at /var/lib/artha, already used by yukta's own session
    # storage) and /tmp are writable. Audit-run artifacts should survive a restart
    # the same way yukta's chat sessions already do, so the persistent volume is
    # the default, not /tmp.
    OUTPUT_DIR: Path = Path(os.getenv("TB_OUTPUT_DIR", "/var/lib/artha/trial-balance"))

    TAXONOMY_AS_PATH: Path = Path(
        os.getenv("TB_TAXONOMY_AS_PATH", str(_PIPELINE_DIR / "knowledge" / "taxonomy" / "schedule_iii_AS_taxonomy.json"))
    )
    TAXONOMY_IND_AS_PATH: Path = Path(
        os.getenv(
            "TB_TAXONOMY_IND_AS_PATH", str(_PIPELINE_DIR / "knowledge" / "taxonomy" / "schedule_iii_IND_AS_taxonomy.json")
        )
    )

    # Versioned audit-knowledge packs (keyword tables, evidence catalogues, weights,
    # benchmark percentages) read by the knowledge-pack loader in pipeline/tools.py.
    KNOWLEDGE_DIR: Path = Path(os.getenv("TB_KNOWLEDGE_DIR", str(_PIPELINE_DIR / "knowledge")))

    # TB-v2 keeps its own separate Postgres instance rather than the gateway's
    # shared finance_llm DB -- same "second DB reachable via host.docker.internal"
    # pattern the old Trial Balance mode's PDF-evidence sub-feature already used.
    DB_HOST: str = os.getenv("TB_DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("TB_DB_PORT", "5433"))
    DB_NAME: str = os.getenv("TB_DB_NAME", "trial_balance_db")
    DB_USER: str = os.getenv("TB_DB_USER", "")
    DB_PASSWORD: str = os.getenv("TB_DB_PASSWORD", "")
    DB_POOL_MIN: int = int(os.getenv("TB_DB_POOL_MIN", "1"))
    DB_POOL_MAX: int = int(os.getenv("TB_DB_POOL_MAX", "5"))

    # Query-time (candidate retrieval) and compile-time (taxonomy node) embedding
    # endpoint for the native classification engine (pipeline/tools/taxonomy_repository.py).
    # NOT the same knob as the gateway's shared EMBEDDING_BASE_URL (used by Financial
    # Statement/SAR/FDR): those modes read a bare base URL and each mode appends its
    # own path convention (e.g. Financial Statement appends "/v1/embeddings" at
    # startup). This client instead POSTs directly to EMBEDDING_BASE_URL as the full
    # request URL (default ends in "/embeddings", not "/v1/embeddings") -- the two
    # conventions are incompatible under one shared value, so this mode keeps its own.
    EMBEDDING_BASE_URL: str = os.getenv("TB_EMBEDDING_BASE_URL", "http://localhost:11652/embeddings")
    EMBEDDING_MODEL: str = os.getenv("TB_EMBEDDING_MODEL", "BAAI/bge-m3")
    EMBEDDING_API_KEY: str = os.getenv("TB_EMBEDDING_API_KEY", "EMPTY")
    EMBEDDING_DIM: int = int(os.getenv("TB_EMBEDDING_DIM", "1024"))

    # Hot-state / cache / coordination plane (pipeline/valkey_client.py). Enabled by
    # default (unlike MinIO below) -- Valkey replaces two in-process, per-worker
    # dicts (the old _PREVIEW_STORE, _FAILED_CALLS) that don't survive a restart or a
    # multi-worker deployment; every helper still degrades gracefully to a no-op if
    # the service isn't reachable, so a dev machine without Valkey running does not
    # fail requests, only loses caching/coordination.
    VALKEY_HOST: str = os.getenv("TB_VALKEY_HOST", "localhost")
    VALKEY_PORT: int = int(os.getenv("TB_VALKEY_PORT", "6379"))
    VALKEY_DB: int = int(os.getenv("TB_VALKEY_DB", "0"))
    VALKEY_ENABLED: bool = os.getenv("TB_VALKEY_ENABLED", "true").lower() == "true"
    VALKEY_PASSWORD: str = os.getenv("TB_VALKEY_PASSWORD", "")

    # Durable artifact plane (pipeline/object_store.py). Disabled by default --
    # unlike Valkey, this is a genuinely new capability, so a clean checkout keeps
    # working as pure local-filesystem-only until an operator explicitly opts in.
    MINIO_ENDPOINT: str = os.getenv("TB_MINIO_ENDPOINT", "http://localhost:9000")
    MINIO_ACCESS_KEY: str = os.getenv("TB_MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY: str = os.getenv("TB_MINIO_SECRET_KEY", "")
    MINIO_BUCKET: str = os.getenv("TB_MINIO_BUCKET", "tb-artifacts")
    MINIO_ENABLED: bool = os.getenv("TB_MINIO_ENABLED", "false").lower() == "true"

    SYSTEM_PROMPT_MAX_TOKENS: int = int(os.getenv("TB_SYSTEM_PROMPT_MAX_TOKENS", "2048"))

    # Tool domains the LLM agent may CHOOSE BETWEEN. Everything else still runs --
    # router.py invokes it deterministically through call_tool(), which reads
    # get_tool_registry() and is unaffected by this setting.
    #
    # The analytics tools are not agent-selected: /audit sequences them itself
    # precisely because agent tool-calling proved unreliable for pipeline-critical
    # steps. Exposing all 62 to the agent therefore cost ~2,400 tokens of schema on
    # every request to advertise capabilities the agent never picks, and put the
    # assembled prompt permanently over budget.
    #
    # Set to "*" to expose every domain (the pre-Phase-5 behaviour).
    # The upload path (router.py's /upload-mapped) now deterministically calls
    # ingest_tb_to_live (domain db_bridge) directly -- no agent tool-chaining is
    # needed for ingestion any more. canonical stays required for the agent's own
    # post-ingestion analytics flow (validate_layer1_tb, build_data_sufficiency_grade,
    # build_normalisation_note). The old "grouping" domain (extract_grouping_mapping)
    # was retired upstream along with the manual-column-mapping upload flow -- there
    # is nothing left to expose under it.
    AGENT_TOOL_DOMAINS: str = os.getenv(
        "TB_AGENT_TOOL_DOMAINS", "chat,db_bridge,input,canonical"
    )

    # Shared platform-wide tracing config (one Phoenix project for every mode) --
    # same three names Financial Statement's tracing_setup.py already reads.
    ENABLE_TRACING: bool = os.getenv("ENABLE_TRACING", "false").lower() == "true"
    PHOENIX_ENDPOINT: str = os.getenv("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces")
    PHOENIX_PROJECT_NAME: str = os.getenv("PHOENIX_PROJECT_NAME", "artha-ai")


settings = Settings()
