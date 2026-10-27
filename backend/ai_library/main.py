import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError, field_validator

from .database import Database, configured_path
from .errors import LibraryError
from .prompts.models import BatchApply, BatchInput, Filters, PromptInput, PromptPatch, RandomInput, WriterInput
from .prompts.service import PromptService


class ListQuery(Filters):
    page: int = Field(default=1, ge=1, le=10_000_000)
    page_size: Literal[50, 100] = 50
    order: Literal["asc", "desc"] = "desc"

    @field_validator("page_size", mode="before")
    @classmethod
    def parse_page_size(cls, value):
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return value


def create_app(db_path=None, static_dir=None):
    app = FastAPI(title="AI Library", version="0.1.0")
    service = PromptService(Database(db_path or configured_path()))
    app.state.prompts = service

    @app.exception_handler(LibraryError)
    async def library_error(_request, exc):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.exception_handler(ValidationError)
    async def validation_error(_request, exc):
        return JSONResponse({"error": "输入字段无效", "detail": json.loads(exc.json())}, status_code=422)

    @app.exception_handler(sqlite3.DatabaseError)
    async def database_error(_request, exc):
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc)):
            return JSONResponse({"error": "数据库忙，请稍后重试；写入未自动重试"}, status_code=503)
        logging.getLogger(__name__).exception("Database request failed", exc_info=exc)
        return JSONResponse({"error": "数据库操作失败，请检查服务日志"}, status_code=500)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", **service.revision()}

    @app.get("/api/v1/prompts")
    def prompts(query: Annotated[ListQuery, Query()]):
        filters = Filters.model_validate(query.model_dump(exclude={"page", "page_size", "order"}))
        return service.list(filters, query.page, query.page_size, query.order)

    @app.get("/api/v1/prompts/revision")
    def revision():
        return service.revision()

    @app.post("/api/v1/prompts/random")
    def random_prompt(value: RandomInput):
        return service.random(value)

    @app.post("/api/v1/prompts/write")
    def write_prompt(value: WriterInput):
        return service.write(value)

    @app.post("/api/v1/prompts/batch/preview")
    def batch_preview(value: BatchInput):
        return service.preview(value)

    @app.post("/api/v1/prompts/batch/apply")
    def batch_apply(value: BatchApply):
        return service.apply(value)

    @app.get("/api/v1/prompts/{prompt_id}")
    def get_prompt(prompt_id: Annotated[int, Field(ge=1, le=2**63 - 1)]):
        return service.get(prompt_id)

    @app.post("/api/v1/prompts", status_code=201)
    def create_prompt(value: PromptInput):
        return service.create(value)

    @app.patch("/api/v1/prompts/{prompt_id}")
    def update_prompt(prompt_id: Annotated[int, Field(ge=1, le=2**63 - 1)], value: PromptPatch):
        return service.patch(prompt_id, value)

    @app.delete("/api/v1/prompts/{prompt_id}")
    def delete_prompt(prompt_id: Annotated[int, Field(ge=1, le=2**63 - 1)], version: Annotated[str, Query(min_length=1)]):
        return service.delete(prompt_id, version)

    @app.get("/api/v1/tags")
    def tags(q: str = "", page: Annotated[int, Query(ge=1)] = 1,
             page_size: Annotated[int, Query(ge=1, le=200)] = 100):
        return service.tags(q, page, page_size)

    assets = Path(static_dir or os.environ.get("AI_LIBRARY_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    else:
        @app.get("/")
        def missing_frontend():
            return JSONResponse({"error": "前端尚未构建，请运行 npm run build，或使用 Docker Compose"}, status_code=503)
    return app


app = create_app()
