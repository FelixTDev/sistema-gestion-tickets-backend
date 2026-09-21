from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

ItemT = TypeVar("ItemT")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


@dataclass(frozen=True)
class PaginationResult(Generic[ItemT]):
    page: int
    page_size: int
    total: int
    total_pages: int
    items: list[ItemT]
