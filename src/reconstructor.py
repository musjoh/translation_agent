from __future__ import annotations

from src.models import (
    Chunk,
    ReconstructedTable,
    ReconstructedText,
    TranslatedChunk,
    UnifiedBlock,
)


def reconstruct(
    blocks: list[UnifiedBlock],
    chunks: list[Chunk],
    translations: list[TranslatedChunk],
) -> tuple[list[ReconstructedText], list[ReconstructedTable]]:
    translated_by_id = {t.chunk_id: t for t in translations}

    text_items: list[ReconstructedText] = []
    table_items: list[ReconstructedTable] = []

    source_text_by_page: dict[int, list[str]] = {}
    for block in blocks:
        if block.block_type == "text" and block.text:
            source_text_by_page.setdefault(block.page_number, []).append(block.text)

    translated_text_by_page: dict[int, list[str]] = {}
    for chunk in sorted(chunks, key=lambda c: c.order):
        if chunk.chunk_type != "text":
            continue
        translated = translated_by_id.get(chunk.chunk_id)
        if translated and translated.translated_text:
            translated_text_by_page.setdefault(chunk.page_number, []).append(translated.translated_text)

    for page_number in sorted(source_text_by_page):
        text_items.append(
            ReconstructedText(
                page_number=page_number,
                source_text="\n\n".join(source_text_by_page[page_number]),
                translated_text="\n\n".join(translated_text_by_page.get(page_number, [])),
            )
        )

    for block in blocks:
        if block.block_type == "table":
            translated_rows: list[list[str]] = []
            for r_idx, row in enumerate(block.rows):
                translated_row = []
                for c_idx, cell in enumerate(row):
                    cid = f"{block.table_id}_r{r_idx}_c{c_idx}"
                    translated = translated_by_id.get(cid)
                    translated_row.append(translated.translated_text if translated else cell)
                translated_rows.append(translated_row)

            table_items.append(
                ReconstructedTable(
                    page_number=block.page_number,
                    table_id=block.table_id or "table",
                    source_headers=block.headers,
                    source_rows=block.rows,
                    translated_headers=block.headers,
                    translated_rows=translated_rows,
                )
            )

    return text_items, table_items
