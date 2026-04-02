from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BlockType = Literal["text", "table"]
ChunkType = Literal["text", "table_cell"]


@dataclass
class TextBlock:
    page_number: int
    order: int
    text: str


@dataclass
class TableBlock:
    page_number: int
    order: int
    table_id: str
    headers: list[str]
    rows: list[list[str]]


@dataclass
class UnifiedBlock:
    block_type: BlockType
    page_number: int
    order: int
    text: str = ""
    table_id: str | None = None
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Chunk:
    chunk_id: str
    page_number: int
    order: int
    chunk_type: ChunkType
    source_text: str
    table_id: str | None = None
    row_index: int | None = None
    col_index: int | None = None


@dataclass
class TranslatedChunk:
    chunk_id: str
    translated_text: str
    success: bool = True
    error: str | None = None


@dataclass
class ReconstructedText:
    page_number: int
    translated_text: str
    source_text: str = ""


@dataclass
class ReconstructedTable:
    page_number: int
    table_id: str
    translated_headers: list[str]
    translated_rows: list[list[str]]
    source_headers: list[str] = field(default_factory=list)
    source_rows: list[list[str]] = field(default_factory=list)


@dataclass
class PipelineResult:
    success: bool
    message: str
    output_path: str = ""
    debug: dict | None = None
