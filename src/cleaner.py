from __future__ import annotations

import re

from src.models import TableBlock, TextBlock, UnifiedBlock


def normalize_blocks(text_blocks: list[TextBlock], table_blocks: list[TableBlock]) -> list[UnifiedBlock]:
    unified: list[UnifiedBlock] = []

    for block in text_blocks:
        text = _normalize_text(block.text)
        if not text:
            continue
        unified.append(
            UnifiedBlock(
                block_type="text",
                page_number=block.page_number,
                order=block.order,
                text=text,
            )
        )

    for table in table_blocks:
        headers = [h.strip() for h in table.headers]
        rows = [[cell.strip() for cell in row] for row in table.rows]
        unified.append(
            UnifiedBlock(
                block_type="table",
                page_number=table.page_number,
                order=table.order,
                table_id=table.table_id,
                headers=headers,
                rows=rows,
            )
        )

    # Maintain stable reading order by page then local order.
    unified.sort(key=lambda b: (b.page_number, b.order, 0 if b.block_type == "text" else 1))
    return unified


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
