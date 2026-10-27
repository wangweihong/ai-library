import base64
import hashlib
import io
import json
import math
import mimetypes
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath

import yaml

from ..errors import LibraryError
from .models import MAX_FILE_BYTES, MAX_FILES, MAX_PACKAGE_BYTES, VARIABLE, FileInput, ResourceInput, safe_path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def skill_metadata(content):
    try:
        text = content.decode("utf-8-sig")
        if len(text) > 2_000_000:
            raise ValueError("Skill 入口超过 200 万字符")
        match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        if not match:
            raise ValueError("SKILL.md 必须包含 YAML frontmatter")
        metadata = yaml.safe_load(match.group(1))
        if not isinstance(metadata, dict):
            raise ValueError("Skill 元数据必须为对象")
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("Skill name 必须为小写字母、数字及单连字符")
        if not isinstance(description, str) or not description.strip() or len(description) > 1024:
            raise ValueError("Skill description 不能为空且不得超过 1024 字符")
        return metadata, text
    except (UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise LibraryError(f"Skill 入口无效：{exc}", 422) from exc


def blob(conn, data):
    if len(data) > MAX_FILE_BYTES:
        raise LibraryError("单个文件超过 20 MiB", 413)
    digest = hashlib.sha256(data).hexdigest()
    conn.execute("INSERT OR IGNORE INTO file_blobs(hash,content,size) VALUES (?,?,?)", (digest, data, len(data)))
    return digest


def blob_bytes(conn, digest):
    row = conn.execute("SELECT content FROM file_blobs WHERE hash=?", (digest,)).fetchone()
    if row is None:
        raise LibraryError("资源文件缺失", 500)
    return row[0]


def check_version(row, version):
    if row["version"] != version:
        raise LibraryError("资源已被修改，请保留草稿并重新加载", 409)


class ResourceService:
    def __init__(self, db, kind):
        if kind not in ("templates", "skills"):
            raise ValueError("Unknown resource kind")
        self.db, self.kind = db, kind

    def _row(self, conn, resource_id, writable=False):
        row = conn.execute("SELECT * FROM resources WHERE id=? AND kind=?", (resource_id, self.kind)).fetchone()
        if row is None:
            raise LibraryError("资源不存在", 404)
        if writable and row["builtin"]:
            raise LibraryError("内置预设只读，请先复制为自定义资源", 403)
        return row

    @staticmethod
    def _public(row, number=None, data=None, version=None):
        value = json.loads(row["data"]) if data is None else data
        return {**value, "id": row["id"], "kind": row["kind"], "builtin": bool(row["builtin"]),
                "active": bool(row["active"]), "preset_id": row["preset_id"],
                "number": number or row["number"], "version": version or row["version"],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def _record(self, conn, resource_id, number=None):
        row = self._row(conn, resource_id)
        if number is None:
            return self._public(row)
        old = conn.execute("SELECT * FROM resource_versions WHERE resource_id=? AND number=?", (resource_id, number)).fetchone()
        if old is None:
            raise LibraryError("版本不存在", 404)
        result = self._public(row, number, json.loads(old["data"]), old["version"])
        result["updated_at"] = old["created_at"]
        return result

    def get(self, resource_id, number=None):
        with self.db.connect() as conn:
            return self._record(conn, resource_id, number)

    def _prepare(self, conn, value, previous=None, source=None):
        data = value.model_dump(exclude={"files", "version"})
        data["source"] = source if source is not None else (previous or {}).get("source", {})
        if not isinstance(data["source"], dict):
            raise LibraryError("来源信息必须为对象", 422)
        for key in ("author", "type", "url", "commit", "reference_commit"):
            if key in data["source"] and not isinstance(data["source"][key], str):
                raise LibraryError("来源字段必须为文本", 422)
        urls = data["source"].get("reference_urls", [])
        if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
            raise LibraryError("参考来源必须为文本列表", 422)
        if previous and previous.get("derived_from"):
            data["derived_from"] = previous["derived_from"]
        filemap = {f["path"]: f["hash"] for f in (previous or {}).get("files", [])}
        touched = set()
        for item in value.files:
            if item.path in touched:
                raise LibraryError("同次保存包含重复文件路径", 422)
            touched.add(item.path)
            if item.remove:
                filemap.pop(item.path, None)
            else:
                filemap[item.path] = blob(conn, item.content())
        if self.kind == "skills":
            if "SKILL.md" in touched and "SKILL.md" in filemap and value.body and (previous is None or value.body != previous.get("body")):
                try:
                    file_body = blob_bytes(conn, filemap["SKILL.md"]).decode("utf-8-sig")
                except UnicodeError as exc:
                    raise LibraryError("SKILL.md 必须为 UTF-8", 422) from exc
                if file_body != value.body.lstrip("\ufeff"):
                    raise LibraryError("SKILL.md 的正文与文件编辑不一致，请合并后保存", 422)
            if value.body and "SKILL.md" not in touched and (previous is None or value.body != previous.get("body")):
                filemap["SKILL.md"] = blob(conn, value.body.encode())
            if "SKILL.md" not in filemap:
                raise LibraryError("Skill 包缺少 SKILL.md", 422)
            meta, text = skill_metadata(blob_bytes(conn, filemap["SKILL.md"]))
            extra = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
            data.update(body=text, skill_name=meta["name"], compatibility=str(meta.get("compatibility", extra.get("compatibility", ""))))
            if not data["description"]:
                data["description"] = meta["description"]
        else:
            if not value.body.strip():
                raise LibraryError("模板正文不能为空", 422)
            names = [v.name for v in value.variables]
            if len(names) != len(set(names)):
                raise LibraryError("模板变量名称重复", 422)
            missing = set(VARIABLE.findall(value.body)) - set(names)
            if missing:
                raise LibraryError("存在未声明的模板变量：" + ", ".join(sorted(missing)), 422)
            for path in value.context_paths:
                if path not in filemap:
                    raise LibraryError(f"参考规范文件缺失：{path}", 422)
                try:
                    blob_bytes(conn, filemap[path]).decode("utf-8")
                except UnicodeError as exc:
                    raise LibraryError("模板参考规范必须为 UTF-8 文本", 422) from exc
        if len(filemap) > MAX_FILES:
            raise LibraryError("文件数量超过 1000", 413)
        files = []
        for path, digest in sorted(filemap.items()):
            size = conn.execute("SELECT size FROM file_blobs WHERE hash=?", (digest,)).fetchone()[0]
            files.append({"path": path, "hash": digest, "size": size,
                          "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream"})
        if sum(f["size"] for f in files) > MAX_PACKAGE_BYTES:
            raise LibraryError("文件包超过 100 MiB", 413)
        data["files"] = files
        return data

    def _commit(self, conn, data, row=None, preset_id=None, force=False):
        encoded = canonical(data)
        if row is not None and encoded == row["data"] and not force:
            return self._public(row)
        now = datetime.now(timezone.utc).isoformat()
        number = row["number"] + 1 if row is not None else 1
        version = str(uuid.uuid4())
        search = "\n".join([data["title"], data["description"], data["body"], *data["tags"], *data["target_models"]])
        if row is None:
            rid = conn.execute("""INSERT INTO resources(kind,data,search_text,builtin,preset_id,number,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (self.kind, encoded, search, int(preset_id is not None), preset_id, number, version, now, now)).lastrowid
        else:
            rid = row["id"]
            conn.execute("UPDATE resources SET data=?,search_text=?,number=?,version=?,updated_at=?,active=1 WHERE id=?",
                         (encoded, search, number, version, now, rid))
        conn.execute("INSERT INTO resource_versions VALUES(?,?,?,?,?)", (rid, number, encoded, version, now))
        conn.executemany("INSERT INTO resource_files VALUES(?,?,?,?)", [(rid, number, f["path"], f["hash"]) for f in data["files"]])
        conn.execute("DELETE FROM resource_tags WHERE resource_id=?", (rid,))
        for tag in data["tags"]:
            conn.execute("INSERT OR IGNORE INTO resource_tag_names(name) VALUES(?)", (tag,))
            conn.execute("INSERT INTO resource_tags SELECT ?,id FROM resource_tag_names WHERE name=?", (rid, tag))
        return self._public(self._row(conn, rid))

    def create(self, value, source=None):
        with self.db.connect(write=True) as conn:
            return self._commit(conn, self._prepare(conn, value, source=source))

    def update(self, resource_id, value):
        with self.db.connect(write=True) as conn:
            row = self._row(conn, resource_id, writable=True)
            check_version(row, value.version)
            return self._commit(conn, self._prepare(conn, value, json.loads(row["data"])), row)

    def delete(self, resource_id, version):
        with self.db.connect(write=True) as conn:
            row = self._row(conn, resource_id, writable=True)
            check_version(row, version)
            conn.execute("DELETE FROM resources WHERE id=?", (resource_id,))
            conn.execute("DELETE FROM file_blobs WHERE hash NOT IN (SELECT hash FROM resource_files)")
            return {"deleted": resource_id}

    def copy(self, resource_id, version, number=None):
        with self.db.connect(write=True) as conn:
            row = self._row(conn, resource_id)
            check_version(row, version)
            data = json.loads(row["data"])
            if number is not None:
                old = conn.execute("SELECT data FROM resource_versions WHERE resource_id=? AND number=?", (resource_id, number)).fetchone()
                if old is None:
                    raise LibraryError("版本不存在", 404)
                data = json.loads(old[0])
            data["title"] += " · 副本"
            data["derived_from"] = {"id": resource_id, "preset_id": row["preset_id"], "number": number or row["number"]}
            return self._commit(conn, data)

    def versions(self, resource_id):
        with self.db.connect() as conn:
            self._row(conn, resource_id)
            return {"items": [dict(r) for r in conn.execute(
                "SELECT number,version,created_at FROM resource_versions WHERE resource_id=? ORDER BY number DESC", (resource_id,))]}

    def restore(self, resource_id, value):
        with self.db.connect(write=True) as conn:
            row = self._row(conn, resource_id, writable=True)
            check_version(row, value.version)
            old = conn.execute("SELECT data FROM resource_versions WHERE resource_id=? AND number=?", (resource_id, value.number)).fetchone()
            if old is None:
                raise LibraryError("版本不存在", 404)
            return self._commit(conn, json.loads(old[0]), row, force=True)

    def list(self, q="", tags=(), target_model="", origin="all", template_kind="", untagged=False, page=1, page_size=50):
        where, args = ["r.kind=?", "r.active=1"], [self.kind]
        if q.strip():
            where.append("instr(lower(r.search_text),lower(?))>0")
            args.append(q.strip())
        if origin != "all":
            where.append("r.builtin=?"); args.append(int(origin == "builtin"))
        if template_kind:
            where.append("json_extract(r.data,'$.template_kind')=?"); args.append(template_kind)
        if target_model:
            where.append("EXISTS(SELECT 1 FROM json_each(r.data,'$.target_models') m WHERE m.value=?)"); args.append(target_model)
        for tag in tags:
            where.append("EXISTS(SELECT 1 FROM resource_tags rt JOIN resource_tag_names t ON t.id=rt.tag_id WHERE rt.resource_id=r.id AND t.name=?)")
            args.append(tag.strip().lower())
        if untagged:
            where.append("NOT EXISTS(SELECT 1 FROM resource_tags rt WHERE rt.resource_id=r.id)")
        sql = " AND ".join(where)
        with self.db.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM resources r WHERE {sql}", args).fetchone()[0]
            rows = conn.execute(f"""SELECT r.id,r.kind,r.builtin,r.preset_id,r.active,r.number,r.version,r.created_at,r.updated_at,
                json_remove(r.data,'$.body','$.variables','$.files') AS data,
                substr(json_extract(r.data,'$.body'),1,240) AS excerpt,
                json_array_length(r.data,'$.files') AS file_count
                FROM resources r WHERE {sql} ORDER BY r.builtin ASC,
                CASE WHEN r.builtin=1 THEN r.id ELSE 0 END ASC,r.updated_at DESC,r.id DESC LIMIT ? OFFSET ?""", (*args, page_size, (page-1)*page_size)).fetchall()
            items = []
            for row in rows:
                data = self._public(row)
                data["excerpt"] = row["excerpt"]
                data["file_count"] = row["file_count"]
                items.append(data)
            models = [r[0] for r in conn.execute("SELECT DISTINCT m.value FROM resources r,json_each(r.data,'$.target_models') m WHERE r.kind=? AND r.active=1 ORDER BY m.value", (self.kind,))]
            return {"items": items, "total": total, "page": page, "page_size": page_size, "target_models": models}

    def tags(self, q=""):
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT t.id,t.name,COUNT(*) AS count FROM resource_tags rt
                JOIN resources r ON r.id=rt.resource_id JOIN resource_tag_names t ON t.id=rt.tag_id
                WHERE r.kind=? AND r.active=1 AND instr(lower(t.name),lower(?))>0
                GROUP BY t.id ORDER BY count DESC,t.name""", (self.kind, q)).fetchall()
            untagged = conn.execute("SELECT COUNT(*) FROM resources r WHERE kind=? AND active=1 AND NOT EXISTS(SELECT 1 FROM resource_tags rt WHERE rt.resource_id=r.id)", (self.kind,)).fetchone()[0]
            return {"items": [dict(r) for r in rows], "total": len(rows), "untagged": untagged}

    def file(self, resource_id, path, number=None):
        path = safe_path(path)
        with self.db.connect() as conn:
            row = self._row(conn, resource_id)
            found = conn.execute("SELECT hash FROM resource_files WHERE resource_id=? AND number=? AND path=?", (resource_id, number or row["number"], path)).fetchone()
            if found is None:
                raise LibraryError("文件不存在", 404)
            return blob_bytes(conn, found[0])

    def render(self, resource_id, value):
        with self.db.connect() as conn:
            data = self._record(conn, resource_id)
            if value.version:
                check_version(data, value.version)
            definitions = {v["name"]: v for v in data["variables"]}
            if set(value.values) - definitions.keys():
                raise LibraryError("包含未声明的参数", 422)
            resolved = {}
            for name, variable in definitions.items():
                raw = value.values.get(name, variable["default"])
                if raw is None or not str(raw).strip():
                    if variable["required"]:
                        raise LibraryError(f"请填写：{variable['label']}", 422)
                    resolved[name] = ""; continue
                if variable["type"] == "number":
                    try:
                        numeric = float(raw)
                        if not math.isfinite(numeric) or (variable["min"] is not None and numeric < variable["min"]) or (variable["max"] is not None and numeric > variable["max"]):
                            raise ValueError
                    except (ValueError, TypeError) as exc:
                        raise LibraryError(f"数字超出范围：{variable['label']}", 422) from exc
                if variable["type"] == "select" and str(raw) not in variable["options"]:
                    raise LibraryError(f"选项无效：{variable['label']}", 422)
                resolved[name] = str(raw)
            text = VARIABLE.sub(lambda m: resolved[m[1]], data["body"])
            # Context files are immutable blobs from this exact template revision.
            contexts = []
            filemap = {f["path"]: f["hash"] for f in data["files"]}
            for path in data["context_paths"]:
                contexts.append(f"\n\n---\n参考规范：{path}\n\n" + blob_bytes(conn, filemap[path]).decode("utf-8"))
            return {"text": text + "".join(contexts), "version": data["version"], "context_paths": data["context_paths"]}

    def context(self, resource_id, value):
        with self.db.connect() as conn:
            data = self._record(conn, resource_id)
            if value.version:
                check_version(data, value.version)
            known = {f["path"]: f for f in data["files"]}
            paths = value.paths if value.paths is not None else ["SKILL.md", *[p for p in known if p.startswith(("references/", "assets/")) and PurePosixPath(p).suffix in (".md", ".txt", ".json", ".yaml", ".yml", ".csv")]]
            if len(paths) > MAX_FILES or len(set(paths)) != len(paths):
                raise LibraryError("文件选择无效")
            sections = []
            for path in paths:
                if path not in known:
                    raise LibraryError("文件不存在", 404)
                try:
                    text = blob_bytes(conn, known[path]["hash"]).decode("utf-8-sig")
                except UnicodeError as exc:
                    raise LibraryError(f"二进制附件请使用 ZIP 导出：{path}") from exc
                sections.append(f"# 文件：{path}\n\n{text}")
            return {"text": "\n\n---\n\n".join(sections), "paths": paths, "version": data["version"]}

    def export(self, resource_id, number=None):
        output = io.BytesIO()
        with self.db.connect() as conn:
            data = self._record(conn, resource_id, number)
            root = data.get("skill_name") or f"template-{resource_id}"
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("ai-library.json", canonical({"format": "ai-library/1", "kind": self.kind, "data": data}))
                if self.kind == "templates":
                    archive.writestr(f"{root}/template.md", data["body"])
                for file in data["files"]:
                    archive.writestr(f"{root}/{file['path']}", blob_bytes(conn, file["hash"]))
        return output.getvalue(), root + ".zip"

    def import_files(self, files):
        if not files or len(files) > MAX_FILES + 1:
            raise LibraryError("文件数量无效", 413)
        for path, content in files.items():
            safe_path(path)
            if len(content) > MAX_FILE_BYTES:
                raise LibraryError("单个文件超过 20 MiB", 413)
        if sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
            raise LibraryError("文件包超过 100 MiB", 413)
        manifest = {}
        if "ai-library.json" in files:
            try:
                manifest = json.loads(files.pop("ai-library.json"))
                if manifest.get("format") != "ai-library/1" or manifest.get("kind") != self.kind:
                    raise ValueError
            except (ValueError, AttributeError) as exc:
                raise LibraryError("资源清单格式或类型不匹配") from exc
        if self.kind == "skills":
            entries = [p for p in files if p == "SKILL.md" or p.endswith("/SKILL.md")]
            if len(entries) != 1:
                raise LibraryError("请导入只包含一个 SKILL.md 入口的包")
            prefix = entries[0][:-len("SKILL.md")]
        else:
            candidates = [p for p in files if p.endswith("template.md")] or [p for p in files if p.endswith(".md")]
            if len(candidates) != 1:
                raise LibraryError("模板包需要唯一 template.md 正文")
            entry = candidates[0]; prefix = entry[:entry.rfind("/")+1] if "/" in entry else ""
        if any(not p.startswith(prefix) for p in files):
            raise LibraryError("所有附件必须位于同一个资源目录")
        stripped = {safe_path(p[len(prefix):]): value for p, value in files.items()}
        raw = manifest.get("data", {})
        if not isinstance(raw, dict):
            raise LibraryError("资源元数据无效")
        values = {k: raw[k] for k in ResourceInput.model_fields if k in raw and k != "files"}
        if self.kind == "skills":
            meta, body = skill_metadata(stripped["SKILL.md"])
            values.setdefault("title", meta["name"])
            values.setdefault("description", meta["description"])
            values["body"] = body
        else:
            key = safe_path(entry[len(prefix):])
            try:
                values["body"] = stripped.pop(key).decode("utf-8-sig")
            except UnicodeError as exc:
                raise LibraryError("模板正文必须为 UTF-8") from exc
            values.setdefault("title", PurePosixPath(entry).stem)
            if "variables" not in values:
                values["variables"] = [{"name": name, "label": name} for name in dict.fromkeys(VARIABLE.findall(values["body"]))]
        values["files"] = [{"path": path, "base64": base64.b64encode(content).decode()} for path, content in stripped.items()]
        return self.create(ResourceInput.model_validate(values), source=raw.get("source", {}))

    def import_zip(self, content):
        if len(content) > MAX_PACKAGE_BYTES:
            raise LibraryError("压缩包超过 100 MiB", 413)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_FILES + 100 or sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
                    raise LibraryError("解压文件数量或总大小超限", 413)
                files = {}
                for info in infos:
                    safe_path(info.filename.rstrip("/"))
                    mode = info.external_attr >> 16
                    if mode & 0o170000 == 0o120000 or info.flag_bits & 1:
                        raise LibraryError("不支持符号链接或加密文件")
                    if info.is_dir():
                        continue
                    if info.filename in files:
                        raise LibraryError("压缩包包含重复路径")
                    if info.file_size > MAX_FILE_BYTES:
                        raise LibraryError("单个文件超过 20 MiB", 413)
                    files[info.filename] = archive.read(info)
                return self.import_files(files)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise LibraryError("ZIP 文件无效或使用了不支持的压缩方式") from exc
