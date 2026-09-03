"""
list_tables.py — diagnostic: which rules-DB tables are discoverable by the
retrieval layer.

Database.discover_table_config() only registers a table if it has an `embedding`
column (plus a recognisable id and content column). This script shows which
public tables qualify and which are invisible to search, which is the first
thing to check when a table's content never turns up in results.

Credentials come from .env via Config — never hard-code them here.

Run:  python list_tables.py
"""

from tools_fs import Config, Database


def main() -> int:
    try:
        conn = Database.get_connection()
    except Exception as exc:
        print(f"Could not connect to the rules database: {exc}")
        print("Check DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD in your .env.")
        return 1

    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'embedding' "
            "ORDER BY table_name"
        )
        with_embedding = {row[0] for row in cur.fetchall()}

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        all_tables = [row[0] for row in cur.fetchall()]

    print(f"Database: {Config.DB_NAME} @ {Config.DB_HOST}:{Config.DB_PORT}\n")

    print("=== Discoverable — has an `embedding` column ===")
    for table in sorted(with_embedding):
        print(f"  [OK]  {table}")

    print("\n=== NOT discoverable — no `embedding` column ===")
    for table in all_tables:
        if table not in with_embedding:
            print(f"  [--]  {table}")

    print(f"\n{len(with_embedding)} of {len(all_tables)} public tables are searchable.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
