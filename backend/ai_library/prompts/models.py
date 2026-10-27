import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_tags(value: str | list[str]) -> list[str]:
    if not isinstance(value, (str, list)) or (isinstance(value, list) and any(not isinstance(t, str) for t in value)):
        raise ValueError("tags must be a string or list of strings")
    items = value.split(",") if isinstance(value, str) else value
    # Commas have always delimited tags in the ComfyUI nodes.
    return list(dict.fromkeys(part.strip().lower() for item in items for part in item.split(",") if part.strip()))


def shasum(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptInput(Input):
    prompt: str = Field(min_length=1, max_length=2_000_000)
    language: Literal["zh", "en"] = "zh"
    media_type: Literal["image", "video", "audio", "music"] = "image"
    prompt_type: Literal["natural", "danbooru"] = "natural"
    nsfw: bool = False
    content_format: Literal["plaintext", "json"] = "plaintext"
    dynamic: bool = False
    width: int = Field(default=1024, ge=1, le=16384)
    height: int = Field(default=768, ge=1, le=16384)
    tags: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("prompt")
    @classmethod
    def trim_prompt(cls, value):
        if not value.strip():
            raise ValueError("prompt cannot be empty")
        return value.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def tags_input(cls, value):
        return normalize_tags(value)


class PromptPatch(Input):
    version: str
    prompt: str | None = Field(default=None, min_length=1, max_length=2_000_000)
    language: Literal["zh", "en"] | None = None
    media_type: Literal["image", "video", "audio", "music"] | None = None
    prompt_type: Literal["natural", "danbooru"] | None = None
    nsfw: bool | None = None
    content_format: Literal["plaintext", "json"] | None = None
    dynamic: bool | None = None
    width: int | None = Field(default=None, ge=1, le=16384)
    height: int | None = Field(default=None, ge=1, le=16384)
    tags: list[str] | None = Field(default=None, max_length=1000)


class WriterInput(PromptInput):
    id: int = Field(default=0, ge=0, le=2**64 - 1)


class Filters(Input):
    q: str = Field(default="", max_length=2000)
    id: int | None = Field(default=None, ge=1, le=2**63 - 1)
    language: Literal["全部", "zh", "en"] = "全部"
    media_type: Literal["全部", "image", "video", "audio", "music"] = "全部"
    prompt_type: Literal["全部", "natural", "danbooru"] = "全部"
    safety: Literal["全部", "safe", "nsfw"] = "全部"
    content_format: Literal["全部", "plaintext", "json"] = "全部"
    dynamic: Literal["全部", "dynamic", "static"] = "全部"
    match_size: bool = False
    width: int = Field(default=1024, ge=1, le=16384)
    height: int = Field(default=768, ge=1, le=16384)
    tags: list[str] = Field(default_factory=list, max_length=1000)
    untagged: bool = False

    @field_validator("tags", mode="before")
    @classmethod
    def tags_input(cls, value):
        return normalize_tags(value)


class RandomInput(Filters):
    seed: int = Field(default=0, ge=0, le=2**64 - 1)


class Selection(Input):
    mode: Literal["ids", "filter"] = "ids"
    ids: list[int] = Field(default_factory=list, max_length=20_000)
    filters: Filters = Field(default_factory=Filters)

    @field_validator("ids")
    @classmethod
    def valid_ids(cls, value):
        if any(isinstance(i, bool) or i <= 0 or i > 2**63 - 1 for i in value):
            raise ValueError("Invalid prompt IDs")
        return sorted(set(value))


class BatchInput(Input):
    selection: Selection
    action: Literal["append", "remove", "replace", "delete"]
    tags: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("tags", mode="before")
    @classmethod
    def tags_input(cls, value):
        return normalize_tags(value)


class BatchApply(BatchInput):
    token: str
