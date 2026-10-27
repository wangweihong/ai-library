import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import LibraryError

PROMPT_COLUMNS = (
    "id", "shasum", "prompt", "language", "media_type", "prompt_type", "nsfw",
    "content_format", "dynamic", "suggested_width", "suggested_height", "created_at",
)
SCHEMA_VERSION = "2"


def configured_path() -> Path:
    return Path(os.environ.get("AI_LIBRARY_DB_PATH", "data/prompts.sqlite3")).expanduser().resolve()


def create_prompts(conn, name="prompts"):
    if name not in ("prompts", "prompts_migrated"):
        raise ValueError("Invalid table name")
    conn.execute(f"""CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, shasum TEXT NOT NULL,
        prompt TEXT NOT NULL, language TEXT NOT NULL, media_type TEXT NOT NULL,
        prompt_type TEXT NOT NULL, nsfw INTEGER NOT NULL, content_format TEXT NOT NULL,
        dynamic INTEGER NOT NULL, suggested_width INTEGER NOT NULL DEFAULT 1024,
        suggested_height INTEGER NOT NULL DEFAULT 768, created_at TEXT NOT NULL)""")


def unique_shasum_indexes(conn):
    result = []
    for row in conn.execute("PRAGMA index_list(prompts)"):
        quoted = row[1].replace('"', '""')
        if row[2] and [c[2] for c in conn.execute(f'PRAGMA index_info("{quoted}")')] == ["shasum"]:
            result.append((row[1], row[3]))
    return result


def check_schema(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(prompts)")}
    if not set(PROMPT_COLUMNS) <= columns:
        raise LibraryError("数据库未初始化或需要迁移；请先运行 ai-library migrate --db <数据库路径>", 503)
    if unique_shasum_indexes(conn):
        raise LibraryError("旧库的 shasum 唯一约束需要迁移；请运行 ai-library migrate --db <数据库路径>", 503)
    try:
        meta = dict(conn.execute("SELECT key, value FROM library_meta"))
        if meta.get("schema_version") != SCHEMA_VERSION or not {"instance", "revision"} <= meta.keys():
            raise sqlite3.DatabaseError("Incompatible metadata")
        if not {"id", "name"} <= {r[1] for r in conn.execute("PRAGMA table_info(tags)")}:
            raise sqlite3.DatabaseError("Invalid tags table")
        if not {"prompt_id", "tag_id"} <= {r[1] for r in conn.execute("PRAGMA table_info(prompt_tags)")}:
            raise sqlite3.DatabaseError("Invalid prompt_tags table")
        if not {"resources", "resource_versions", "resource_tag_names", "resource_tags", "file_blobs", "resource_files"} <= {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            raise sqlite3.DatabaseError("Resource tables are missing")
    except sqlite3.DatabaseError as exc:
        raise LibraryError("数据库版本不兼容；请运行 ai-library migrate --db <数据库路径>", 503) from exc


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self, write=False):
        if not self.path.is_file():
            raise LibraryError("数据库不存在；请使用 ai-library serve 或 make run 启动并初始化新库", 503)
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            check_schema(conn)
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def revision(conn):
        meta = dict(conn.execute("SELECT key, value FROM library_meta WHERE key IN ('instance','revision')"))
        return f"{meta['instance']}:{meta['revision']}"

    @staticmethod
    def bump(conn):
        conn.execute("UPDATE library_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def initialize_meta(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executemany("INSERT OR IGNORE INTO library_meta VALUES (?, ?)",
                     [("schema_version", SCHEMA_VERSION), ("instance", str(uuid.uuid4())), ("revision", "0")])
    conn.execute("UPDATE library_meta SET value=? WHERE key='schema_version'", (SCHEMA_VERSION,))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_shasum ON prompts(shasum)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_tags_tag ON prompt_tags(tag_id, prompt_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_filters ON prompts(language, media_type, prompt_type, id)")
