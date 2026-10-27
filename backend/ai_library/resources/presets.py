import base64
import json
from pathlib import Path

from ..errors import LibraryError
from .models import MAX_PACKAGE_BYTES, ResourceInput, safe_path
from .service import ResourceService


def presets_directory():
    local = Path.cwd() / "presets"
    return local if local.is_dir() else Path(__file__).resolve().parents[3] / "presets"


def load_presets(directory):
    directory = Path(directory).resolve()
    catalog = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))
    seen, result = set(), []
    if catalog.get("version") != 1:
        raise LibraryError("预设目录清单版本不兼容")
    for item in catalog["resources"]:
        if item["id"] in seen:
            raise LibraryError("预设 ID 重复")
        seen.add(item["id"])
        package = directory / safe_path(item["path"])
        if not package.is_dir() or package.is_symlink() or not package.resolve().is_relative_to(directory):
            raise LibraryError("预设目录不存在或路径无效")
        files = {}
        for path in sorted(package.rglob("*")):
            if path.is_symlink():
                raise LibraryError("预设包不能包含符号链接")
            if path.is_file():
                files[safe_path(path.relative_to(package).as_posix())] = path.read_bytes()
        if sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
            raise LibraryError("预设包超过大小限制")
        if item["kind"] == "templates":
            values = json.loads(files.pop("manifest.json"))
            values["body"] = files.pop("template.md").decode("utf-8")
        elif item["kind"] == "skills":
            values = {"title": item["title"], "description": item.get("description", ""),
                      "tags": item.get("tags", []), "target_models": item.get("target_models", []),
                      "language": item.get("language", "mixed")}
        else:
            raise LibraryError("未知预设类型")
        values["files"] = [{"path": path, "base64": base64.b64encode(content).decode()} for path, content in files.items()]
        result.append((item, ResourceInput.model_validate(values)))
    return result


def sync_presets(db, directory=None):
    entries = load_presets(directory or presets_directory())
    # Entire catalog update is atomic; a malformed upstream package changes nothing.
    with db.connect(write=True) as conn:
        for item, value in entries:
            service = ResourceService(db, item["kind"])
            row = conn.execute("SELECT * FROM resources WHERE preset_id=?", (item["id"],)).fetchone()
            if row is not None and (row["kind"] != item["kind"] or not row["builtin"]):
                raise LibraryError("预设 ID 与已有资源冲突")
            data = service._prepare(conn, value, source=item.get("source", {}))
            service._commit(conn, data, row, preset_id=item["id"])
            conn.execute("UPDATE resources SET active=1 WHERE preset_id=?", (item["id"],))
        active = {item["id"] for item, _ in entries}
        for row in conn.execute("SELECT id,preset_id FROM resources WHERE builtin=1").fetchall():
            if row["preset_id"] not in active:
                conn.execute("UPDATE resources SET active=0 WHERE id=?", (row["id"],))
    return {"synced": len(entries)}
