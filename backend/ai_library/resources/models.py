import base64
import math
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import LibraryError
from ..prompts.models import normalize_tags

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_FILES = 1000
VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def safe_path(path: str) -> str:
    if not path or "\\" in path or "\x00" in path or any(ord(c) < 32 for c in path):
        raise LibraryError("文件路径无效")
    parts = path.split("/")
    if path.startswith("/") or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise LibraryError("文件路径必须位于资源包内")
    if len(path) > 500:
        raise LibraryError("文件路径过长")
    return str(PurePosixPath(path))


class Variable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=64)
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "textarea", "select", "number"] = "text"
    description: str = Field(default="", max_length=2000)
    required: bool = True
    default: str | float | None = None
    options: list[str] = Field(default_factory=list, max_length=200)
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def check(self):
        if self.type == "select" and not self.options:
            raise ValueError("下拉变量需要选项")
        if any(v is not None and not math.isfinite(v) for v in (self.min, self.max)):
            raise ValueError("数字范围必须为有限值")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("数字范围无效")
        if self.default is not None and str(self.default).strip():
            if self.type == "select" and str(self.default) not in self.options:
                raise ValueError("默认值不在下拉选项中")
            if self.type == "number":
                try:
                    value = float(self.default)
                    if not math.isfinite(value) or (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
                        raise ValueError
                except (TypeError, ValueError) as exc:
                    raise ValueError("数字默认值超出范围") from exc
        return self


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    text: str | None = None
    base64: str | None = None
    remove: bool = False

    @field_validator("path")
    @classmethod
    def path_valid(cls, value):
        return safe_path(value)

    @model_validator(mode="after")
    def content_valid(self):
        if sum((self.text is not None, self.base64 is not None, self.remove)) != 1:
            raise ValueError("文件必须包含 text、base64 或 remove 中的一项")
        return self

    def content(self):
        try:
            value = self.text.encode("utf-8") if self.text is not None else base64.b64decode(self.base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise LibraryError("附件编码无效") from exc
        if len(value) > MAX_FILE_BYTES:
            raise LibraryError("单个文件超过 20 MiB")
        return value


class ResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=1000)
    target_models: list[str] = Field(default_factory=list, max_length=100)
    language: str = Field(default="zh", max_length=30)
    template_kind: Literal["assistant", "prompt"] = "assistant"
    body: str = Field(default="", max_length=2_000_000)
    variables: list[Variable] = Field(default_factory=list, max_length=100)
    files: list[FileInput] = Field(default_factory=list, max_length=MAX_FILES)
    context_paths: list[str] = Field(default_factory=list, max_length=MAX_FILES)

    @field_validator("title")
    @classmethod
    def title_valid(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def tags_valid(cls, value):
        return normalize_tags(value)

    @field_validator("target_models")
    @classmethod
    def models_valid(cls, value):
        return list(dict.fromkeys(m.strip() for m in value if m.strip()))

    @field_validator("context_paths")
    @classmethod
    def paths_valid(cls, value):
        return [safe_path(p) for p in value]


class ResourcePatch(ResourceInput):
    version: str


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str


class RestoreInput(VersionInput):
    number: int = Field(ge=1, le=2**63-1)


class CopyInput(VersionInput):
    number: int | None = Field(default=None, ge=1, le=2**63-1)


class RenderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str | float | None] = Field(default_factory=dict)
    version: str | None = None


class ContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] | None = None
    version: str | None = None
