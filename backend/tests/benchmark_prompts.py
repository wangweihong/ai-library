"""Repeatable 120k-row smoke benchmark using only a synthetic temporary database."""
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_library.cli import migrate
from ai_library.database import Database
from ai_library.prompts.models import BatchApply, BatchInput, Filters, RandomInput
from ai_library.prompts.service import PromptService


def main():
    measurements = {}
    def measure(name, action):
        started = time.perf_counter()
        result = action()
        measurements[name] = round((time.perf_counter() - started) * 1000, 1)
        return result

    with TemporaryDirectory(prefix="ai-library-benchmark-") as directory:
        path = Path(directory) / "prompts.sqlite3"
        migrate(path)
        count = 120_000
        text = "森林中的人像，柔和的自然光，清晰细节。A cinematic portrait, soft light, natural colors. " * 10
        with sqlite3.connect(path) as conn:
            conn.executemany("""INSERT INTO prompts (shasum,prompt,language,media_type,prompt_type,nsfw,
                content_format,dynamic,suggested_width,suggested_height,created_at)
                VALUES (?,?, 'zh','image','natural',0,'plaintext',0,1024,768,'2026-10-27')""",
                ((hashlib.sha1(str(i).encode()).hexdigest(), f"{i} {text}") for i in range(count)))
        service = PromptService(Database(path))
        first = measure("first_page_ms", lambda: service.list(Filters()))
        assert first["total"] == count and len(first["items"]) == 50
        assert all("prompt" not in item for item in first["items"])
        measure("deep_page_ms", lambda: service.list(Filters(), page=2400))
        measure("substring_search_ms", lambda: service.list(Filters(q="自然光")))
        measure("untagged_filter_ms", lambda: service.list(Filters(untagged=True)))
        measure("random_uint64_ms", lambda: service.random(RandomInput(seed=2**64-1)))
        body = BatchInput.model_validate({"selection": {"mode": "filter", "filters": {"untagged": True}}, "action": "append", "tags": ["reviewed"]})
        preview = measure("batch_preview_120k_ms", lambda: service.preview(body))
        assert preview["count"] == count
        applied = measure("batch_apply_120k_ms", lambda: service.apply(BatchApply(**body.model_dump(), token=preview["token"])))
        assert applied["affected"] == count
        assert service.list(Filters(untagged=True))["total"] == 0
        assert service.tags()["items"][0]["count"] == count
        measurements["records"] = count
        measurements["database_mib"] = round(path.stat().st_size / 1024**2, 1)
        measurements["page_json_kib"] = round(len(json.dumps(first, ensure_ascii=False).encode()) / 1024, 1)
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()
