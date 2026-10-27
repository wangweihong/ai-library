"""Create a new library on first serve; migrate existing libraries explicitly."""
import argparse
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .database import PROMPT_COLUMNS, check_schema, configured_path, create_prompts, initialize_meta, unique_shasum_indexes
from .errors import LibraryError
from .prompts.models import shasum


def backup_database(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = path.with_name(f"{path.name}.backup-{stamp}")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
        if source.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity check failed; restore from backup before migrating")
        with sqlite3.connect(destination) as target:
            source.backup(target)
    return destination


def migrate(path: Path):
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_database(path) if path.exists() else None
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "library_meta" in tables:
            meta = dict(conn.execute("SELECT key,value FROM library_meta"))
            if meta.get("schema_version") not in (None, "1"):
                raise ValueError("Unsupported database version; refusing to downgrade")
        if "prompts" not in tables:
            if {"tags", "prompt_tags"} & tables:
                raise ValueError("Partial legacy schema; refusing to create over existing tag data")
            create_prompts(conn)
        else:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(prompts)")}
            if "shasum" not in columns:
                conn.execute("ALTER TABLE prompts ADD COLUMN shasum TEXT")
                columns.add("shasum")
            if not set(PROMPT_COLUMNS) <= columns:
                raise ValueError("Legacy prompt columns are missing; migration rolled back")
            indexes = unique_shasum_indexes(conn)
            if any(origin != "c" for _, origin in indexes):
                if columns != set(PROMPT_COLUMNS):
                    raise ValueError("Extra prompt columns require a custom migration; refusing data loss")
                # Retain application indexes/triggers except the obsolete unique shasum index.
                excluded = {name for name, _ in indexes}
                ancillary = [r[1] for r in conn.execute(
                    "SELECT name,sql FROM sqlite_master WHERE tbl_name='prompts' AND type IN ('index','trigger') AND sql IS NOT NULL"
                ) if r[0] not in excluded]
                sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='prompts'").fetchone()
                create_prompts(conn, "prompts_migrated")
                names = ",".join(PROMPT_COLUMNS)
                conn.create_function("library_shasum", 1, shasum)
                source_names = ",".join("library_shasum(prompt)" if name == "shasum" else name for name in PROMPT_COLUMNS)
                conn.execute(f"INSERT INTO prompts_migrated ({names}) SELECT {source_names} FROM prompts")
                conn.execute("DROP TABLE prompts")
                conn.execute("ALTER TABLE prompts_migrated RENAME TO prompts")
                if sequence:
                    conn.execute("DELETE FROM sqlite_sequence WHERE name='prompts'")
                    maximum_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM prompts").fetchone()[0]
                    conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('prompts',?)", (max(sequence[0], maximum_id),))
                for sql in ancillary:
                    conn.execute(sql)
            else:
                for name, _ in indexes:
                    name = name.replace('"', '""')
                    conn.execute(f'DROP INDEX "{name}"')
            # Keyset batches keep a 100k+ record migration bounded in memory.
            cursor_id = -1
            while rows := conn.execute("SELECT id,prompt,shasum FROM prompts WHERE id>? ORDER BY id LIMIT 1000", (cursor_id,)).fetchall():
                conn.executemany("UPDATE prompts SET shasum=? WHERE id=?",
                                 [(shasum(text), i) for i, text, current in rows if current != shasum(text)])
                cursor_id = rows[-1][0]
        conn.execute("CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)")
        conn.execute("""CREATE TABLE IF NOT EXISTS prompt_tags (
            prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY(prompt_id,tag_id))""")
        initialize_meta(conn)
        # Offline imports and restored backups may reuse a previous numeric revision.
        # A migration marks a fresh database generation, including when row IDs repeat.
        conn.execute("UPDATE library_meta SET value=? WHERE key='instance'", (str(uuid.uuid4()),))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(f"Foreign key check failed: {violations[:5]}")
        conn.commit()
        conn.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return backup


def prepare_database(path: Path):
    """Only a missing file is initialized; existing files are checked read-only."""
    path = path.expanduser().resolve()
    if not path.exists():
        migrate(path)
        return
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        check_schema(conn)


def main():
    parser = argparse.ArgumentParser(description="AI Library maintenance (stop writers before migration)")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("migrate", "backup"):
        p = commands.add_parser(command)
        p.add_argument("--db", type=Path, default=configured_path())
    serve = commands.add_parser("serve", help="Start with defaults; initialize a missing database once")
    serve.add_argument("--db", type=Path, default=configured_path())
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            prepare_database(args.db)
            import uvicorn
            from .main import create_app
            uvicorn.run(create_app(args.db), host=args.host, port=args.port)
        elif args.command == "migrate":
            backup = migrate(args.db)
            print(f"Migration complete: {args.db}; previous database backup: {backup or 'new database'}")
        else:
            print(backup_database(args.db.expanduser().resolve()))
    except (ValueError, OSError, sqlite3.DatabaseError, LibraryError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
