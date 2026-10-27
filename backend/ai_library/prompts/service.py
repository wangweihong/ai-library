import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

from ..database import Database
from ..errors import LibraryError
from .models import BatchInput, Filters, PromptInput, PromptPatch, WriterInput, shasum


def filter_sql(filters: Filters):
    clauses, params = [], []
    if filters.id is not None:
        clauses.append("p.id=?")
        params.append(filters.id)
    if filters.q.strip():
        q = filters.q.strip()
        # Numeric input is an exact ID search; text is a literal substring.
        if q.isascii() and q.isdigit() and 0 < int(q) <= 2**63 - 1:
            clauses.append("p.id=?")
            params.append(int(q))
        else:
            clauses.append("p.prompt LIKE ? ESCAPE '\\'")
            params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    for field in ("language", "media_type", "prompt_type", "content_format"):
        value = getattr(filters, field)
        if value != "全部":
            clauses.append(f"p.{field}=?")
            params.append(value)
    if filters.safety != "全部":
        clauses.append("p.nsfw=?")
        params.append(int(filters.safety == "nsfw"))
    if filters.dynamic != "全部":
        clauses.append("p.dynamic=?")
        params.append(int(filters.dynamic == "dynamic"))
    if filters.match_size:
        clauses.extend(["p.suggested_width=?", "p.suggested_height=?"])
        params.extend([filters.width, filters.height])
    for tag in filters.tags:
        clauses.append("EXISTS (SELECT 1 FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.prompt_id=p.id AND t.name=?)")
        params.append(tag)
    if filters.untagged:
        clauses.append("NOT EXISTS (SELECT 1 FROM prompt_tags pt WHERE pt.prompt_id=p.id)")
    return " AND ".join(clauses) or "1=1", params


TAGS_SQL = """(SELECT json_group_array(name) FROM (
    SELECT t.name FROM tags t JOIN prompt_tags pt ON pt.tag_id=t.id
    WHERE pt.prompt_id=p.id ORDER BY t.name))"""


def row_version(row, tags):
    return hashlib.sha256(json.dumps([dict(row), tags], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def record(conn, prompt_id):
    row = conn.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    if row is None:
        raise LibraryError(f"prompt id does not exist: {prompt_id}", 404)
    tags = [r[0] for r in conn.execute(
        "SELECT t.name FROM tags t JOIN prompt_tags pt ON t.id=pt.tag_id WHERE pt.prompt_id=? ORDER BY t.name", (prompt_id,))]
    data = dict(row)
    data.update(nsfw=bool(row["nsfw"]), dynamic=bool(row["dynamic"]),
                width=row["suggested_width"], height=row["suggested_height"], tags=tags,
                version=row_version(row, tags))
    return data


def metadata(data):
    return {key: value for key, value in data.items() if key not in ("prompt", "version")}


def assert_version(data, version):
    if not hmac.compare_digest(data["version"], version):
        raise LibraryError("记录已被其他客户端修改，请保留草稿并重新加载", 409)


def replace_tags(conn, prompt_id, tags):
    conn.execute("DELETE FROM prompt_tags WHERE prompt_id=?", (prompt_id,))
    for tag in tags:
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
        conn.execute("INSERT OR IGNORE INTO prompt_tags SELECT ?,id FROM tags WHERE name=?", (prompt_id, tag))


def write_record(conn, value: PromptInput, prompt_id=None, writer=False):
    timestamp = datetime.now(timezone.utc).isoformat()
    values = (shasum(value.prompt), value.prompt, value.language, value.media_type, value.prompt_type,
              int(value.nsfw), value.content_format, int(value.dynamic), value.width, value.height)
    if prompt_id is not None:
        current = record(conn, prompt_id)
        # Preserve writer's historical timestamp behavior; editor retains creation time.
        conn.execute("""UPDATE prompts SET shasum=?,prompt=?,language=?,media_type=?,prompt_type=?,
            nsfw=?,content_format=?,dynamic=?,suggested_width=?,suggested_height=?,created_at=? WHERE id=?""",
                     (*values, timestamp if writer else current["created_at"], prompt_id))
    else:
        prompt_id = conn.execute("""INSERT INTO prompts (shasum,prompt,language,media_type,prompt_type,
            nsfw,content_format,dynamic,suggested_width,suggested_height,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (*values, timestamp)).lastrowid
    replace_tags(conn, prompt_id, value.tags)
    Database.bump(conn)
    return record(conn, prompt_id)


class PromptService:
    def __init__(self, db):
        self.db = db
        self._secret = secrets.token_bytes(32)

    def list(self, filters, page=1, page_size=50, order="desc"):
        where, params = filter_sql(filters)
        direction = "ASC" if order == "asc" else "DESC"
        with self.db.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM prompts p WHERE {where}", params).fetchone()[0]
            rows = conn.execute(f"""SELECT p.id,substr(p.prompt,1,240) AS excerpt,p.language,p.media_type,
                p.prompt_type,p.nsfw,p.content_format,p.dynamic,p.suggested_width AS width,
                p.suggested_height AS height,p.created_at,{TAGS_SQL} AS tags
                FROM prompts p WHERE {where} ORDER BY p.id {direction} LIMIT ? OFFSET ?""",
                [*params, page_size, (page-1)*page_size]).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["tags"] = json.loads(item["tags"])
                items.append(item)
            return {"items": items, "total": total, "page": page, "page_size": page_size,
                    "revision": self.db.revision(conn)}

    def get(self, prompt_id):
        with self.db.connect() as conn:
            return record(conn, prompt_id)

    def create(self, value):
        with self.db.connect(write=True) as conn:
            return write_record(conn, value)

    def patch(self, prompt_id, patch: PromptPatch):
        with self.db.connect(write=True) as conn:
            current = record(conn, prompt_id)
            assert_version(current, patch.version)
            values = {key: current[key] for key in PromptInput.model_fields}
            changed = patch.model_dump(exclude_unset=True, exclude={"version"})
            if any(v is None for v in changed.values()):
                raise LibraryError("编辑字段不能为 null", 422)
            values.update(changed)
            return write_record(conn, PromptInput.model_validate(values), prompt_id)

    def delete(self, prompt_id, version):
        with self.db.connect(write=True) as conn:
            assert_version(record(conn, prompt_id), version)
            conn.execute("DELETE FROM prompt_tags WHERE prompt_id=?", (prompt_id,))
            conn.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))
            self.db.bump(conn)
            return {"affected": 1}

    def write(self, value: WriterInput):
        with self.db.connect(write=True) as conn:
            prompt_id = value.id or None
            if prompt_id is not None and prompt_id > 2**63 - 1:
                raise LibraryError(f"prompt id does not exist: {prompt_id}", 404)
            if prompt_id is None:
                row = conn.execute("SELECT id FROM prompts WHERE shasum=? ORDER BY id LIMIT 1", (shasum(value.prompt),)).fetchone()
                prompt_id = row[0] if row else None
            return write_record(conn, value, prompt_id, writer=True)

    def random(self, value):
        where, params = filter_sql(value)
        with self.db.connect() as conn:
            count = conn.execute(f"SELECT COUNT(*) FROM prompts p WHERE {where}", params).fetchone()[0]
            if count == 0:
                raise LibraryError("No prompts match the selected filters.", 404)
            row = conn.execute(f"SELECT p.id FROM prompts p WHERE {where} ORDER BY p.id LIMIT 1 OFFSET ?",
                               [*params, value.seed % count]).fetchone()
            return record(conn, row[0])

    def revision(self):
        with self.db.connect() as conn:
            return {"revision": self.db.revision(conn)}

    def tags(self, q="", page=1, page_size=100):
        escaped = q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.db.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tags WHERE name LIKE ? ESCAPE '\\'", (f"%{escaped}%",)).fetchone()[0]
            rows = conn.execute("""SELECT t.id,t.name,(SELECT COUNT(*) FROM prompt_tags pt WHERE pt.tag_id=t.id) AS count
                FROM tags t WHERE t.name LIKE ? ESCAPE '\\' ORDER BY count DESC,t.name LIMIT ? OFFSET ?""",
                (f"%{escaped}%", page_size, (page-1)*page_size)).fetchall()
            untagged = conn.execute("SELECT COUNT(*) FROM prompts p WHERE NOT EXISTS (SELECT 1 FROM prompt_tags pt WHERE pt.prompt_id=p.id)").fetchone()[0]
            return {"items": [dict(r) for r in rows], "total": total, "untagged": untagged}

    @staticmethod
    def _targets(conn, selection):
        conn.execute("CREATE TEMP TABLE selected (id INTEGER PRIMARY KEY)")
        if selection.mode == "filter":
            where, params = filter_sql(selection.filters)
            conn.execute(f"INSERT INTO selected SELECT p.id FROM prompts p WHERE {where}", params)
        else:
            if not selection.ids:
                raise LibraryError("请先选择提示词")
            conn.executemany("INSERT INTO selected VALUES (?)", [(i,) for i in selection.ids])
            if conn.execute("SELECT COUNT(*) FROM selected s LEFT JOIN prompts p ON p.id=s.id WHERE p.id IS NULL").fetchone()[0]:
                raise LibraryError("选中记录已不存在，请刷新列表", 409)
        count = conn.execute("SELECT COUNT(*) FROM selected").fetchone()[0]
        if not count:
            raise LibraryError("当前筛选没有匹配记录")
        digest = hashlib.sha256()
        # Include record contents and tags: edits after preview invalidate deletion too.
        for row in conn.execute(f"SELECT p.*,{TAGS_SQL} AS _tags FROM prompts p JOIN selected s ON s.id=p.id ORDER BY p.id"):
            digest.update(json.dumps(list(row), ensure_ascii=False).encode())
            digest.update(b"\n")
        return count, digest.hexdigest()

    def _signature(self, value, fingerprint, expires):
        payload = value.model_dump(exclude={"token"})
        raw = json.dumps([payload, fingerprint, expires], ensure_ascii=False, sort_keys=True).encode()
        return hmac.new(self._secret, raw, hashlib.sha256).hexdigest()

    def preview(self, value: BatchInput):
        if value.action in ("append", "remove") and not value.tags:
            raise LibraryError("追加或移除操作需要至少一个标签")
        with self.db.connect() as conn:
            count, fingerprint = self._targets(conn, value.selection)
        expires = int(time.time()) + 600
        return {"count": count, "action": value.action, "tags": value.tags,
                "token": f"{expires}.{self._signature(value, fingerprint, expires)}", "expires_at": expires}

    def apply(self, value):
        try:
            raw_expiry, signature = value.token.split(".", 1)
            expires = int(raw_expiry)
        except (ValueError, TypeError):
            raise LibraryError("预览凭据无效，请重新预览", 409)
        if expires < time.time():
            raise LibraryError("预览已过期，请重新预览", 409)
        with self.db.connect(write=True) as conn:
            count, fingerprint = self._targets(conn, value.selection)
            if not hmac.compare_digest(signature, self._signature(value, fingerprint, expires)):
                raise LibraryError("操作范围或记录已变化，请重新预览", 409)
            if value.action == "delete":
                conn.execute("DELETE FROM prompt_tags WHERE prompt_id IN (SELECT id FROM selected)")
                conn.execute("DELETE FROM prompts WHERE id IN (SELECT id FROM selected)")
            else:
                if value.action == "replace":
                    conn.execute("DELETE FROM prompt_tags WHERE prompt_id IN (SELECT id FROM selected)")
                for tag in value.tags:
                    if value.action == "remove":
                        conn.execute("DELETE FROM prompt_tags WHERE prompt_id IN (SELECT id FROM selected) AND tag_id=(SELECT id FROM tags WHERE name=?)", (tag,))
                    else:
                        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
                        conn.execute("INSERT OR IGNORE INTO prompt_tags SELECT s.id,t.id FROM selected s CROSS JOIN tags t WHERE t.name=?", (tag,))
            self.db.bump(conn)
            return {"affected": count, "revision": self.db.revision(conn)}
