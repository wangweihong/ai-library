import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_library.cli import migrate, prepare_database
from ai_library.database import Database, create_prompts
from ai_library.main import create_app
from ai_library.prompts.models import Filters, PromptInput, RandomInput


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "prompts.sqlite3"
    migrate(path)
    return path


@pytest.fixture
def client(database):
    return TestClient(create_app(database))


def create(client, text="森林中的人像", **kwargs):
    response = client.post("/api/v1/prompts", json={"prompt": text, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def revision(client):
    return client.get("/api/v1/prompts/revision").json()["revision"]


def batch(client, selection, action, tags=None):
    body = {"selection": selection, "action": action, "tags": tags or []}
    preview = client.post("/api/v1/prompts/batch/preview", json=body)
    assert preview.status_code == 200, preview.text
    return body, preview.json()


def test_crud_and_timestamp_preservation(client):
    initial_revision = revision(client)
    row = create(client, "  森林中的人像  ", tags=["Portrait", "portrait", " forest "])
    assert row["prompt"] == "森林中的人像"
    assert row["tags"] == ["forest", "portrait"]
    assert revision(client) != initial_revision
    changed = client.patch(f"/api/v1/prompts/{row['id']}", json={"version": row["version"], "prompt": "城市夜景", "tags": []})
    assert changed.status_code == 200
    assert changed.json()["created_at"] == row["created_at"]
    assert changed.json()["shasum"] != row["shasum"]
    assert changed.json()["tags"] == []
    assert client.delete(f"/api/v1/prompts/{row['id']}", params={"version": changed.json()["version"]}).status_code == 200
    assert client.get(f"/api/v1/prompts/{row['id']}").status_code == 404


def test_new_always_creates_but_writer_upserts_lowest_id(client):
    one = create(client, "duplicate", tags=["old"])
    two = create(client, "duplicate", tags=["other"])
    result = client.post("/api/v1/prompts/write", json={"id": 0, "prompt": " duplicate ", "tags": "new, replacement"})
    assert result.status_code == 200
    assert result.json()["id"] == one["id"]
    assert result.json()["tags"] == ["new", "replacement"]
    assert client.get(f"/api/v1/prompts/{two['id']}").json()["tags"] == ["other"]
    result = client.post("/api/v1/prompts/write", json={"id": two["id"], "prompt": "duplicate", "language": "en"})
    assert result.json()["id"] == two["id"]
    assert result.json()["language"] == "en"


def test_missing_writer_id_no_mutation(client):
    before = revision(client)
    for prompt_id in (999, 2**64 - 1):
        response = client.post("/api/v1/prompts/write", json={"id": prompt_id, "prompt": "missing"})
        assert response.status_code == 404
    assert revision(client) == before


def test_search_filters_pagination_and_tag_counts(client):
    one = create(client, "森林中的人像 100%_", tags=["forest", "portrait"], language="zh", width=768, height=1024)
    create(client, "forest", tags=["forest"], language="en", nsfw=True, media_type="video", dynamic=True)
    empty = create(client, "无标签")
    response = client.get("/api/v1/prompts", params=[("q", "森林"), ("tags", "forest"), ("tags", "portrait"), ("page_size", "50")])
    assert response.status_code == 200, response.text
    assert [r["id"] for r in response.json()["items"]] == [one["id"]]
    assert "prompt" not in response.json()["items"][0]
    assert client.get("/api/v1/prompts", params={"q": "100%_"}).json()["total"] == 1
    assert client.get("/api/v1/prompts", params={"q": str(empty["id"])}).json()["items"][0]["id"] == empty["id"]
    assert client.get("/api/v1/prompts", params={"untagged": True}).json()["total"] == 1
    assert client.get("/api/v1/prompts", params={"language": "en", "safety": "nsfw", "dynamic": "dynamic", "media_type": "video"}).json()["total"] == 1
    assert client.get("/api/v1/prompts", params={"match_size": True, "width": 768, "height": 1024}).json()["total"] == 1
    tags = client.get("/api/v1/tags").json()
    assert tags["untagged"] == 1
    assert tags["items"][0]["name"] == "forest"
    assert tags["items"][0]["count"] == 2
    for i in range(55): create(client, f"page {i}")
    first = client.get("/api/v1/prompts?page_size=50&page=1").json()
    second = client.get("/api/v1/prompts?page_size=50&page=2").json()
    assert first["total"] == 58 and len(first["items"]) == 50 and len(second["items"]) == 8
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}


def test_random_uint64_seed_and_filter_semantics(client):
    ids = [create(client, str(i), tags=["same"])['id'] for i in range(5)]
    create(client, "excluded", tags=[])
    for seed in (0, 1, 9, 2**64 - 1):
        response = client.post("/api/v1/prompts/random", json={"seed": seed, "tags": "same"})
        assert response.status_code == 200
        assert response.json()["id"] == ids[seed % 5]
    assert client.post("/api/v1/prompts/random", json={"tags": ["missing"]}).status_code == 404


def test_concurrent_edit_and_delete_conflict(client):
    row = create(client)
    url = f"/api/v1/prompts/{row['id']}"
    client.patch(url, json={"version": row["version"], "tags": ["new"]})
    assert client.patch(url, json={"version": row["version"], "prompt": "overwrite"}).status_code == 409
    assert client.delete(url, params={"version": row["version"]}).status_code == 409
    assert client.get(url).json()["tags"] == ["new"]


@pytest.mark.parametrize("action,tags,expected", [
    ("append", ["new"], ["new", "old"]), ("remove", ["old"], []),
    ("replace", ["new"], ["new"]), ("replace", [], []),
])
def test_bulk_tag_actions(client, action, tags, expected):
    rows = [create(client, str(i), tags=["old"]) for i in range(3)]
    selection = {"mode": "ids", "ids": [rows[0]["id"], rows[2]["id"]]}
    body, preview = batch(client, selection, action, tags)
    result = client.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]})
    assert result.status_code == 200, result.text
    assert result.json()["affected"] == 2
    for row in (rows[0], rows[2]):
        assert client.get(f"/api/v1/prompts/{row['id']}").json()["tags"] == expected
    assert client.get(f"/api/v1/prompts/{rows[1]['id']}").json()["tags"] == ["old"]


def test_all_filtered_results_and_changed_scope(client):
    create(client, "one")
    selection = {"mode": "filter", "filters": {"untagged": True}}
    body, preview = batch(client, selection, "append", ["new"])
    create(client, "two")
    assert client.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]}).status_code == 409
    body, preview = batch(client, selection, "append", ["new"])
    assert preview["count"] == 2
    assert client.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]}).status_code == 200
    assert client.get("/api/v1/prompts?untagged=true").json()["total"] == 0


def test_batch_delete_conflict_and_shared_tags(client):
    a = create(client, "one", tags=["shared"])
    b = create(client, "two", tags=["shared"])
    body, preview = batch(client, {"ids": [a["id"]]}, "delete")
    changed = client.patch(f"/api/v1/prompts/{a['id']}", json={"version": a["version"], "prompt": "edited"})
    assert changed.status_code == 200
    assert client.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]}).status_code == 409
    body, preview = batch(client, {"ids": [a["id"]]}, "delete")
    assert client.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]}).status_code == 200
    assert client.get(f"/api/v1/prompts/{b['id']}").json()["tags"] == ["shared"]
    assert client.get("/api/v1/tags").json()["items"][0]["count"] == 1


def test_batch_tampering_and_restart_invalidates_token(client, database):
    a = create(client)
    body, preview = batch(client, {"ids": [a["id"]]}, "append", ["tag"])
    assert client.post("/api/v1/prompts/batch/apply", json={**body, "action": "delete", "token": preview["token"]}).status_code == 409
    other = TestClient(create_app(database))
    assert other.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]}).status_code == 409


def test_bulk_rollback_keeps_records_tags_and_revision(client, database):
    row = create(client, tags=["old"])
    body, preview = batch(client, {"ids": [row["id"]]}, "replace", ["fail"])
    before = revision(client)
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TRIGGER forced_failure BEFORE INSERT ON prompt_tags BEGIN SELECT RAISE(ABORT,'test failure'); END")
    result = client.post("/api/v1/prompts/batch/apply", json={**body, "token": preview["token"]})
    assert result.status_code == 500
    assert client.get(f"/api/v1/prompts/{row['id']}").json()["tags"] == ["old"]
    assert revision(client) == before
    assert [t["name"] for t in client.get('/api/v1/tags').json()["items"]] == ["old"]


@pytest.mark.parametrize("body", [{"prompt": "   "}, {"prompt": "x", "width": 0}, {"prompt": "x", "tags": None}, {"prompt": "x", "language": "invalid"}])
def test_invalid_inputs_no_mutation(client, body):
    before = revision(client)
    assert client.post("/api/v1/prompts", json=body).status_code == 422
    assert revision(client) == before


def test_sql_injection_is_literal(client):
    create(client)
    assert client.get("/api/v1/prompts", params={"q": "' OR 1=1 --"}).json()["total"] == 0
    assert client.get("/api/v1/prompts", params={"language": "' OR 1=1 --"}).status_code == 422


def test_missing_database_is_not_silently_created(tmp_path):
    path = tmp_path / "missing.sqlite3"
    client = TestClient(create_app(path))
    assert client.get("/api/v1/health").status_code == 503
    assert not path.exists()


def test_writer_serialization_under_concurrent_upserts(client):
    with ThreadPoolExecutor(max_workers=6) as workers:
        responses = list(workers.map(lambda _: client.post("/api/v1/prompts/write", json={"prompt": "same"}), range(12)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert client.get("/api/v1/prompts").json()["total"] == 1


def test_reader_snapshot_and_nonblocking_writer(database, client):
    row = create(client)
    with Database(database).connect() as conn:
        old = conn.execute("SELECT prompt FROM prompts WHERE id=?", (row["id"],)).fetchone()[0]
        changed = client.patch(f"/api/v1/prompts/{row['id']}", json={"version": row["version"], "prompt": "new"})
        assert changed.status_code == 200
        assert conn.execute("SELECT prompt FROM prompts WHERE id=?", (row["id"],)).fetchone()[0] == old
    assert client.get(f"/api/v1/prompts/{row['id']}").json()["prompt"] == "new"


def test_lock_returns_retryable_error_without_mutation(client, database):
    before = revision(client)
    conn = sqlite3.connect(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = client.post("/api/v1/prompts", json={"prompt": "blocked"})
        assert result.status_code == 503
    finally:
        conn.rollback(); conn.close()
    assert revision(client) == before


@pytest.mark.parametrize("inline", [True, False])
def test_migration_backs_up_legacy_unique_and_preserves_relations_sequence(tmp_path, inline):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        create_prompts(conn)
        if inline:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='prompts'").fetchone()[0]
            conn.execute("DROP TABLE prompts")
            conn.execute(sql.replace("shasum TEXT NOT NULL", "shasum TEXT NOT NULL UNIQUE"))
        else:
            conn.execute("CREATE UNIQUE INDEX idx_prompts_shasum ON prompts(shasum)")
        conn.execute("INSERT INTO prompts VALUES (5,'legacy','duplicate','zh','image','natural',0,'plaintext',0,1024,768,'old')")
        conn.execute("INSERT INTO prompts VALUES (10,'other','duplicate','en','video','natural',0,'plaintext',0,1024,768,'old')")
        conn.execute("DELETE FROM prompts WHERE id=10")
        conn.execute("CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        conn.execute("INSERT INTO tags VALUES(1,'kept')")
        conn.execute("CREATE TABLE prompt_tags(prompt_id INTEGER REFERENCES prompts(id),tag_id INTEGER REFERENCES tags(id),PRIMARY KEY(prompt_id,tag_id))")
        conn.execute("INSERT INTO prompt_tags VALUES(5,1)")
    backup = migrate(path)
    assert backup and backup.exists()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT shasum FROM prompts").fetchone()[0] == "legacy"
    client = TestClient(create_app(path))
    assert client.get("/api/v1/prompts/5").json()["tags"] == ["kept"]
    assert create(client)["id"] == 11
    assert create(client, "duplicate")["id"] == 12
    old_revision = revision(client)
    migrate(path)
    assert revision(client) != old_revision
    assert client.get("/api/v1/prompts").json()["total"] == 3


def test_incompatible_schema_migration_rolls_back(tmp_path):
    path = tmp_path / "incomplete.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE prompts(id INTEGER PRIMARY KEY,prompt TEXT)")
        conn.execute("INSERT INTO prompts VALUES(1,'keep')")
    with pytest.raises(ValueError, match="missing"):
        migrate(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA table_info(prompts)").fetchall()[-1][1] == "prompt"
        assert conn.execute("SELECT * FROM prompts").fetchall() == [(1,"keep")]


def test_empty_legacy_table_keeps_autoincrement_sequence(tmp_path):
    path = tmp_path / "empty.sqlite3"
    with sqlite3.connect(path) as conn:
        create_prompts(conn)
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='prompts'").fetchone()[0]
        conn.execute("DROP TABLE prompts")
        conn.execute(sql.replace("shasum TEXT NOT NULL", "shasum TEXT NOT NULL UNIQUE"))
        conn.execute("INSERT INTO prompts VALUES(99,'x','x','zh','image','natural',0,'plaintext',0,1024,768,'old')")
        conn.execute("DELETE FROM prompts")
    migrate(path)
    assert create(TestClient(create_app(path)))["id"] == 100


def test_expired_batch_preview_cannot_mutate(client, monkeypatch):
    from types import SimpleNamespace
    row = create(client)
    body, preview = batch(client, {"ids": [row["id"]]}, "delete")
    monkeypatch.setattr("ai_library.prompts.service.time", SimpleNamespace(time=lambda: preview["expires_at"] + 1))
    assert client.post("/api/v1/prompts/batch/apply", json={**body,"token":preview["token"]}).status_code == 409
    assert client.get(f"/api/v1/prompts/{row['id']}").status_code == 200


def test_first_start_initializes_once_and_preserves_data(tmp_path):
    path = tmp_path / "new" / "prompts.sqlite3"
    prepare_database(path)
    client = TestClient(create_app(path))
    row = create(client, "keep between starts")
    before = revision(client)
    prepare_database(path)
    assert revision(client) == before
    assert client.get(f"/api/v1/prompts/{row['id']}").json()["prompt"] == "keep between starts"
    assert not list(path.parent.glob("*.backup-*"))


def test_first_start_refuses_to_migrate_existing_legacy_database(tmp_path):
    from ai_library.errors import LibraryError
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        create_prompts(conn)
        conn.execute("CREATE UNIQUE INDEX legacy_unique ON prompts(shasum)")
    before = path.read_bytes()
    with pytest.raises(LibraryError, match="唯一约束"):
        prepare_database(path)
    assert path.read_bytes() == before


def test_first_start_does_not_overwrite_existing_empty_file(tmp_path):
    from ai_library.errors import LibraryError
    path = tmp_path / "existing.sqlite3"
    path.touch()
    with pytest.raises(LibraryError):
        prepare_database(path)
    assert path.stat().st_size == 0
