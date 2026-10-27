from typing import Annotated, Literal

from fastapi import APIRouter, File, Path, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..errors import LibraryError
from .models import ContextInput, CopyInput, MAX_PACKAGE_BYTES, RenderInput, ResourceInput, ResourcePatch, RestoreInput

ResourceID = Annotated[int, Path(ge=1, le=2**63-1)]


def resource_router(service):
    router = APIRouter(prefix=f"/api/v1/{service.kind}", tags=[service.kind])

    @router.get("")
    def listing(q: Annotated[str, Query(max_length=2000)] = "", tags: Annotated[list[str] | None, Query(max_length=1000)] = None,
                target_model: str = "", origin: Literal["all", "builtin", "custom"] = "all",
                template_kind: Literal["", "assistant", "prompt"] = "", untagged: bool = False,
                page: Annotated[int, Query(ge=1, le=10_000_000)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 50):
        return service.list(q, tags or [], target_model, origin, template_kind, untagged, page, page_size)

    @router.get("/tags")
    def tags(q: str = ""):
        return service.tags(q)

    @router.post("/import", status_code=201)
    async def import_package(files: Annotated[list[UploadFile], File()]):
        try:
            total, uploaded = 0, {}
            if len(files) > 1001:
                raise LibraryError("文件数量超限", 413)
            for file in files:
                content = bytearray()
                while chunk := await file.read(1024 * 1024):
                    content.extend(chunk); total += len(chunk)
                    if total > MAX_PACKAGE_BYTES:
                        raise LibraryError("上传超过 100 MiB", 413)
                if not file.filename:
                    raise LibraryError("上传缺少文件名")
                if file.filename in uploaded:
                    raise LibraryError("上传包含重复路径")
                uploaded[file.filename or "upload"] = bytes(content)
            if len(uploaded) == 1 and next(iter(uploaded)).lower().endswith(".zip"):
                return await run_in_threadpool(service.import_zip, next(iter(uploaded.values())))
            return await run_in_threadpool(service.import_files, uploaded)
        finally:
            for file in files:
                await file.close()

    @router.post("", status_code=201)
    def create(value: ResourceInput):
        return service.create(value)

    @router.get("/{resource_id}")
    def detail(resource_id: ResourceID):
        return service.get(resource_id)

    @router.patch("/{resource_id}")
    def update(resource_id: ResourceID, value: ResourcePatch):
        return service.update(resource_id, value)

    @router.delete("/{resource_id}")
    def delete(resource_id: ResourceID, version: str):
        return service.delete(resource_id, version)

    @router.post("/{resource_id}/copy", status_code=201)
    def copy(resource_id: ResourceID, value: CopyInput):
        return service.copy(resource_id, value.version, value.number)

    @router.get("/{resource_id}/versions")
    def versions(resource_id: ResourceID):
        return service.versions(resource_id)

    @router.get("/{resource_id}/versions/{number}")
    def version(resource_id: ResourceID, number: ResourceID):
        return service.get(resource_id, number)

    @router.post("/{resource_id}/restore")
    def restore(resource_id: ResourceID, value: RestoreInput):
        return service.restore(resource_id, value)

    @router.get("/{resource_id}/export")
    def export(resource_id: ResourceID, number: Annotated[int | None, Query(ge=1)] = None):
        content, name = service.export(resource_id, number)
        return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})

    @router.get("/{resource_id}/files/{path:path}")
    def file(resource_id: ResourceID, path: str, number: Annotated[int | None, Query(ge=1)] = None):
        content = service.file(resource_id, path, number)
        import mimetypes
        media = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if media not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            try:
                content.decode("utf-8-sig")
                media = "text/plain; charset=utf-8"
            except UnicodeError:
                media = "application/octet-stream"
        return Response(content, media_type=media, headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"})

    if service.kind == "templates":
        @router.post("/{resource_id}/render")
        def render(resource_id: ResourceID, value: RenderInput):
            return service.render(resource_id, value)
    else:
        @router.post("/{resource_id}/context")
        def context(resource_id: ResourceID, value: ContextInput):
            return service.context(resource_id, value)
    return router
