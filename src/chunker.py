from __future__ import annotations

from src.models import Chunk, UnifiedBlock


def build_chunks(blocks: list[UnifiedBlock], max_chars: int = 1400) -> list[Chunk]:
    chunks: list[Chunk] = []
    order = 0

    for block in blocks:
        if block.block_type == "text":
            paragraphs = [p.strip() for p in block.text.split("\n\n") if p.strip()]
            for p in paragraphs:
                for piece in _split_text(p, max_chars):
                    order += 1
                    chunks.append(
                        Chunk(
                            chunk_id=f"p{block.page_number}_txt_{order}",
                            page_number=block.page_number,
                            order=order,
                            chunk_type="text",
                            source_text=piece,
                        )
                    )
            continue

        if block.block_type == "table":
            for r_idx, row in enumerate(block.rows):
                for c_idx, cell in enumerate(row):
                    cell_text = cell.strip()
                    if not cell_text:
                        continue
                    order += 1
                    chunks.append(
                        Chunk(
                            chunk_id=f"{block.table_id}_r{r_idx}_c{c_idx}",
                            page_number=block.page_number,
                            order=order,
                            chunk_type="table_cell",
                            source_text=cell_text,
                            table_id=block.table_id,
                            row_index=r_idx,
                            col_index=c_idx,
                        )
                    )

    return chunks


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = remaining.rfind(". ", 0, max_chars)
        if split_at < max_chars // 2:
            split_at = max_chars
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts
