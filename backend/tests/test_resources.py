import base64
import hashlib
import io
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_library.cli import backup_database, migrate
from ai_library.database import Database
from ai_library.main import create_app
from ai_library.resources.models import ResourceInput
from ai_library.resources.presets import load_presets, presets_directory, sync_presets
from ai_library.resources.service import ResourceService


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "library.sqlite3"
    migrate(path)
    sync_presets(Database(path))
    return path


@pytest.fixture
def client(database):
    return TestClient(create_app(database))


def find(client, kind, title):
    return next(row for row in client.get(f"/api/v1/{kind}").json()["items"] if row["title"] == title)


def detail(client, kind, rid):
    response = client.get(f"/api/v1/{kind}/{rid}")
    assert response.status_code == 200, response.text
    return response.json()


def payload(record, **changes):
    result = {key: record[key] for key in ResourceInput.model_fields if key != "files"}
    return {**result, "files": [], "version": record["version"], **changes}


def copy(client, kind, rid):
    row = detail(client, kind, rid)
    response = client.post(f"/api/v1/{kind}/{rid}/copy", json={"version": row["version"]})
    assert response.status_code == 201, response.text
    return response.json()


def skill_body(name="custom-skill", text="First version"):
    return f"---\nname: {name}\ndescription: A portable test skill.\nmetadata:\n  unknown-field: keep-me\n---\n\n# Skill\n\n{text}\n"


def make_skill(client, **changes):
    response = client.post("/api/v1/skills", json={"title": "My skill", "body": skill_body(), **changes})
    assert response.status_code == 201, response.text
    return response.json()


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def test_catalog_counts_and_original_sources(client):
    assert client.get("/api/v1/templates").json()["total"] == 6
    skills = client.get("/api/v1/skills").json()
    assert skills["total"] == 10
    upstream = [row for row in skills["items"] if row["source"]["author"] == "MiniMax-AI"]
    assert len(upstream) == 9
    assert all(r["source"]["commit"] == "d21241f0a4b3acbb34c97dae47fa417b7065e438" for r in upstream)
    assert all("MiniMax Hub" in r["compatibility"] for r in upstream if r["title"] != "H3 官方提示词编写")


def test_vendored_bytes_and_packages_are_complete(client):
    root = presets_directory()
    manifest = json.loads((root / "minimax-source.json").read_text())
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    for row in client.get("/api/v1/skills").json()["items"]:
        if row["source"]["author"] != "MiniMax-AI":
            continue
        slug = row["preset_id"].split(".")[-1]
        record = detail(client, "skills", row["id"])
        actual = set()
        for file in record["files"]:
            key = f"skills/{slug}/{file['path']}"
            assert expected[key] == file["hash"]
            actual.add(key)
        assert actual == {p for p in expected if p.startswith(f"skills/{slug}/")}


def test_tags_scope_and_combined_filters(client):
    rows = client.get("/api/v1/skills", params=[("tags", "导演"), ("tags", "视频")]).json()
    assert rows["total"] == 2
    assert client.get("/api/v1/skills", params={"target_model": "MiniMax H3", "origin": "builtin", "q": "Ref2VA"}).json()["total"] >= 1
    assert client.get("/api/v1/templates", params={"template_kind": "prompt"}).json()["total"] == 2
    assert client.get("/api/v1/templates", params={"template_kind": "assistant"}).json()["total"] == 4
    assert client.get("/api/v1/tags").json()["items"] == []
    make_skill(client, tags=[])
    assert client.get("/api/v1/skills?untagged=true&origin=custom").json()["total"] == 1
    tags = client.get("/api/v1/skills/tags").json()
    assert next(t for t in tags["items"] if t["name"] == "导演")["count"] == 2
    assert tags["untagged"] == 1


def test_builtin_readonly_and_copy(client):
    row = find(client, "templates", "Krea 2 写实助手")
    record = detail(client, "templates", row["id"])
    assert client.patch(f"/api/v1/templates/{row['id']}", json=payload(record, title="change")).status_code == 403
    assert client.delete(f"/api/v1/templates/{row['id']}", params={"version": row["version"]}).status_code == 403
    copied = copy(client, "templates", row["id"])
    assert not copied["builtin"] and copied["derived_from"]["number"] == 1
    changed = client.patch(f"/api/v1/templates/{copied['id']}", json=payload(copied, title="My assistant", tags=["Mine"]))
    assert changed.status_code == 200
    assert changed.json()["tags"] == ["mine"]
    assert detail(client, "templates", row["id"])["title"] == "Krea 2 写实助手"


def test_literal_render_validation_and_no_history(client):
    response = client.post("/api/v1/templates", json={"title": "Variable test", "body": "{{subject}} + {{scale}} + {{style}}", "variables": [
        {"name": "subject", "label": "主体"}, {"name": "scale", "label": "数量", "type": "number", "min": 1, "max": 3, "default": 2},
        {"name": "style", "label": "风格", "type": "select", "options": ["photo", "film"], "default": "photo"}]})
    assert response.status_code == 201, response.text
    row = response.json(); url = f"/api/v1/templates/{row['id']}/render"
    assert client.post(url, json={"values": {}}).status_code == 422
    assert client.post(url, json={"values": {"subject": "cat", "scale": 9}}).status_code == 422
    assert client.post(url, json={"values": {"subject": "cat", "style": "unknown"}}).status_code == 422
    assert client.post(url, json={"values": {"subject": "cat", "extra": "ignored?"}}).status_code == 422
    result = client.post(url, json={"values": {"subject": "{{do_not_evaluate}}"}, "version": row["version"]})
    assert result.json()["text"].startswith("{{do_not_evaluate}} + 2")
    assert len(client.get(f"/api/v1/templates/{row['id']}/versions").json()["items"]) == 1
    assert detail(client, "templates", row["id"])["version"] == row["version"]


def test_h3_render_includes_frozen_reference(client):
    row = find(client, "templates", "MiniMax H3 Ref2VA 助手")
    result = client.post(f"/api/v1/templates/{row['id']}/render", json={"values": {"task": "A character walks", "references": "Picture 1 is attached", "duration": 10}})
    assert result.status_code == 200
    assert "# Full-Reference Mode Rewrite Output Format Guide" in result.json()["text"]
    assert "retention_analysis" in result.json()["text"]
    assert "<Subject N>" in result.json()["text"]
    template = detail(client, "templates", row["id"])
    skill = detail(client, "skills", find(client, "skills", "H3 官方提示词编写")["id"])
    assert next(f["hash"] for f in template["files"] if f["path"] == 'context/ref-en.txt') == next(f["hash"] for f in skill["files"] if f["path"] == 'references/ref-en.txt')


def test_skill_context_includes_text_asset_templates(client):
    row = find(client, "skills", "通用导演 Skill")
    result = client.post(f"/api/v1/skills/{row['id']}/context", json={})
    assert result.status_code == 200
    assert "assets/shot-plan.md" in result.json()["paths"]
    assert "# 分镜计划" in result.json()["text"]


def test_revision_history_restore_and_conflicts(client):
    row = make_skill(client, files=[{"path": "references/guide.md", "text": "original reference"}])
    url = f"/api/v1/skills/{row['id']}"
    changed = client.patch(url, json=payload(row, tags=["director"], body=skill_body(text="Second version"), files=[{"path": "references/guide.md", "text": "updated reference"}])).json()
    assert changed["number"] == 2
    assert client.patch(url, json=payload(row, title="stale")).status_code == 409
    assert client.delete(url, params={"version": row["version"]}).status_code == 409
    assert client.get(url + "/files/references/guide.md?number=1").text == "original reference"
    restored = client.post(url + "/restore", json={"version": changed["version"], "number": 1})
    assert restored.status_code == 200
    assert restored.json()["number"] == 3
    assert "First version" in restored.json()["body"]
    assert restored.json()["tags"] == []
    assert client.get(url + "/files/references/guide.md").text == "original reference"
    assert len(client.get(url + "/versions").json()["items"]) == 3
    assert client.post(url + "/restore", json={"version": restored.json()["version"], "number": 1}).json()["number"] == 4


def test_copy_old_version_and_blob_dedup(client, database):
    row = make_skill(client, files=[{"path": "assets/image.bin", "base64": base64.b64encode(b'\x00\xff\x10').decode()}])
    changed = client.patch(f"/api/v1/skills/{row['id']}", json=payload(row, body=skill_body(text="new"))).json()
    with sqlite3.connect(database) as conn: before = conn.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0]
    copied = client.post(f"/api/v1/skills/{row['id']}/copy", json={"version": changed["version"], "number": 1}).json()
    assert "First version" in copied["body"] and copied["derived_from"]["number"] == 1
    with sqlite3.connect(database) as conn: assert conn.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0] == before
    assert client.delete(f"/api/v1/skills/{row['id']}", params={"version": changed["version"]}).status_code == 200
    assert client.get(f"/api/v1/skills/{copied['id']}/files/assets/image.bin").content == b'\x00\xff\x10'


@pytest.mark.parametrize("kind,title", [("skills", "H3 官方提示词编写"), ("templates", "MiniMax H3 Ref2VA 助手")])
def test_zip_round_trip_preserves_all_bytes_metadata_and_tags(client, kind, title):
    original = detail(client, kind, find(client, kind, title)["id"])
    exported = client.get(f"/api/v1/{kind}/{original['id']}/export")
    assert exported.status_code == 200
    imported = client.post(f"/api/v1/{kind}/import", files={"files": ("package.zip", exported.content, "application/zip")})
    assert imported.status_code == 201, imported.text
    row = imported.json()
    assert not row["builtin"] and row["tags"] == original["tags"]
    assert row["variables"] == original["variables"] and row["body"] == original["body"]
    assert row["files"] == original["files"]
    assert row["context_paths"] == original["context_paths"]
    assert row["source"] == original["source"]


def test_directory_import_preserves_bom_unknown_frontmatter_and_binary(client):
    entry = b'\xef\xbb\xbf' + skill_body().encode()
    result = client.post("/api/v1/skills/import", files=[
        ("files", ("my-skill/SKILL.md", entry)),
        ("files", ("my-skill/assets/raw.bin", b'\x00\xff')),
        ("files", ("my-skill/references/tips.txt", b'plain reference'))])
    assert result.status_code == 201, result.text
    row = result.json()
    assert "unknown-field: keep-me" in row["body"]
    assert client.get(f"/api/v1/skills/{row['id']}/files/SKILL.md").content == entry
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/skills/{row['id']}/export").content)) as archive:
        assert archive.read("custom-skill/assets/raw.bin") == b'\x00\xff'


@pytest.mark.parametrize("path", ["../outside.txt", "/absolute.txt", "folder/../../bad", "folder\\bad", "a//b"])
def test_dangerous_archive_paths_rejected_without_partial_data(client, path):
    before = client.get("/api/v1/skills").json()["total"]
    package = zip_bytes({"SKILL.md": skill_body(), path: "bad"})
    response = client.post("/api/v1/skills/import", files={"files": ("bad.zip", package)})
    assert response.status_code == 400
    assert client.get("/api/v1/skills").json()["total"] == before


def test_symlink_and_duplicate_archives_rejected(client):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", skill_body())
        info = zipfile.ZipInfo("references/link"); info.external_attr = (0o120777 << 16)
        archive.writestr(info, "../../outside")
    assert client.post("/api/v1/skills/import", files={"files": ("bad.zip", output.getvalue())}).status_code == 400


def test_size_limit_checked_before_extraction(client, monkeypatch):
    monkeypatch.setattr("ai_library.resources.service.MAX_PACKAGE_BYTES", 300)
    package = zip_bytes({"SKILL.md": skill_body(), "large.txt": "x" * 5000})
    response = client.post("/api/v1/skills/import", files={"files": ("bad.zip", package)})
    assert response.status_code == 413


def test_invalid_update_rolls_back_files_tags_and_history(client, database):
    row = make_skill(client)
    with sqlite3.connect(database) as conn: before = conn.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0]
    result = client.patch(f"/api/v1/skills/{row['id']}", json=payload(row, tags=["new"], files=[
        {"path": "new.txt", "text": "must roll back"}, {"path": "SKILL.md", "text": "no frontmatter"}]))
    assert result.status_code == 422
    after = detail(client, "skills", row["id"])
    assert after["version"] == row["version"] and after["tags"] == []
    with sqlite3.connect(database) as conn: assert conn.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0] == before


def test_resource_changes_do_not_touch_prompt_revision_or_tags(client):
    before = client.get("/api/v1/prompts/revision").json()
    row = make_skill(client, tags=["only-skill"])
    client.patch(f"/api/v1/skills/{row['id']}", json=payload(row, title="changed"))
    assert client.get("/api/v1/prompts/revision").json() == before
    assert client.get("/api/v1/tags").json()["items"] == []
    assert client.get("/api/v1/prompts").json()["total"] == 0


def test_startup_sync_idempotence_updates_presets_not_copies(client, database, tmp_path):
    root = tmp_path / "presets"; shutil.copytree(presets_directory(), root)
    original = find(client, "templates", "MiniMax H3 Ref2VA 助手")
    custom = copy(client, "templates", original["id"])
    with sqlite3.connect(database) as conn: before = conn.execute("SELECT COUNT(*) FROM resource_versions").fetchone()[0]
    sync_presets(Database(database), root)
    with sqlite3.connect(database) as conn: assert conn.execute("SELECT COUNT(*) FROM resource_versions").fetchone()[0] == before
    context = root / "templates/minimax-h3-ref2va-assistant/context/ref-en.txt"
    context.write_bytes(context.read_bytes() + b'\nA new upstream rule.\n')
    sync_presets(Database(database), root)
    updated = detail(client, "templates", original["id"])
    copied = detail(client, "templates", custom["id"])
    assert updated["number"] == 2 and copied["number"] == 1
    assert updated["files"][0]["hash"] != copied["files"][0]["hash"]
    assert copied["version"] == custom["version"]


def test_bad_catalog_rolls_back_all_preset_updates(client, database, tmp_path):
    root = tmp_path / "presets"; shutil.copytree(presets_directory(), root)
    before = detail(client, "templates", find(client, "templates", "Krea 2 写实助手")["id"])
    path = root / "templates/krea2-photo-assistant/template.md"; path.write_text(path.read_text() + "\nChanged")
    path = root / "skills/original/creative-director/SKILL.md"; path.write_text("invalid")
    with pytest.raises(Exception): sync_presets(Database(database), root)
    assert detail(client, "templates", before["id"])["version"] == before["version"]


def test_removed_preset_is_archived_without_losing_history(client, database, tmp_path):
    root = tmp_path / "presets"; shutil.copytree(presets_directory(), root)
    catalog = json.loads((root / "catalog.json").read_text()); removed = catalog["resources"].pop()
    (root / "catalog.json").write_text(json.dumps(catalog))
    row = next(r for r in client.get("/api/v1/skills").json()["items"] if r["preset_id"] == removed["id"])
    sync_presets(Database(database), root)
    assert client.get("/api/v1/skills").json()["total"] == 9
    assert detail(client, "skills", row["id"])["active"] is False
    assert len(client.get(f"/api/v1/skills/{row['id']}/versions").json()["items"]) == 1


def test_v1_to_v2_migration_preserves_prompts_and_backup(tmp_path):
    path = tmp_path / "old.sqlite3"; migrate(path)
    client = TestClient(create_app(path))
    saved = client.post("/api/v1/prompts", json={"prompt": "preserve me", "tags": ["original"]}).json()
    with sqlite3.connect(path) as conn:
        for table in ("resource_files", "resource_versions", "resource_tags", "resource_tag_names", "resources", "file_blobs"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE library_meta SET value='1' WHERE key='schema_version'")
    backup = migrate(path)
    assert backup and backup.exists()
    assert client.get(f"/api/v1/prompts/{saved['id']}").json()["tags"] == ["original"]
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT value FROM library_meta WHERE key='schema_version'").fetchone()[0] == '1'
    sync_presets(Database(path))
    assert client.get("/api/v1/skills").json()["total"] == 10


def test_database_backup_restores_files_tags_and_complete_history(client, database):
    row = make_skill(client, tags=["backup"], files=[{"path":"assets/binary.bin","base64":base64.b64encode(b'\x00\xff\x01').decode()}])
    changed = client.patch(f"/api/v1/skills/{row['id']}", json=payload(row, body=skill_body(text="Second saved version"))).json()
    template = copy(client, "templates", find(client, "templates", "MiniMax H3 Ref2VA 助手")["id"])
    backup = backup_database(database)
    recovered = TestClient(create_app(backup))
    assert detail(recovered, "skills", row["id"])["version"] == changed["version"]
    assert recovered.get(f"/api/v1/skills/{row['id']}/files/assets/binary.bin").content == b'\x00\xff\x01'
    assert recovered.get(f"/api/v1/skills/{row['id']}/versions/1").json()["tags"] == ["backup"]
    assert len(recovered.get(f"/api/v1/skills/{row['id']}/versions").json()["items"]) == 2
    assert detail(recovered, "templates", template["id"])["files"] == template["files"]
