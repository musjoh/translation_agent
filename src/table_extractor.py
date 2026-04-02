from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
from docx import Document

from src.models import TableBlock


def extract_tables(input_path: Path) -> list[TableBlock]:
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_tables_from_pdf(input_path)
    if suffix == ".docx":
        return _extract_tables_from_docx(input_path)
    return []


def _extract_tables_from_pdf(input_path: Path) -> list[TableBlock]:
    tables: list[TableBlock] = []
    seen_signatures: set[str] = set()
    order = 0
    with pdfplumber.open(str(input_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            raw_tables = _extract_page_tables(page)
            if not raw_tables:
                raw_tables = _fallback_extract_from_text(page)

            for table_idx, raw in enumerate(raw_tables, start=1):
                normalized = _normalize_table(raw)
                if not _looks_like_table(normalized):
                    continue

                signature = _table_signature(normalized)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)

                headers = normalized[0]
                rows = normalized[1:] if len(normalized) > 1 else []
                order += 1
                tables.append(
                    TableBlock(
                        page_number=page_num,
                        order=order,
                        table_id=f"p{page_num}_t{table_idx}",
                        headers=headers,
                        rows=rows,
                    )
                )
    return tables


def _extract_tables_from_docx(input_path: Path) -> list[TableBlock]:
    tables: list[TableBlock] = []
    order = 0
    doc = Document(str(input_path))
    for table_idx, table in enumerate(doc.tables, start=1):
        raw: list[list[str]] = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                raw.append(cells)

        normalized = _normalize_table(raw)
        if not _looks_like_table(normalized):
            continue

        headers = normalized[0]
        rows = normalized[1:] if len(normalized) > 1 else []
        order += 1
        tables.append(
            TableBlock(
                page_number=1,
                order=order,
                table_id=f"docx_t{table_idx}",
                headers=headers,
                rows=rows,
            )
        )
    return tables


def extract_pseudo_tables_from_text_pages(
    page_texts: list[tuple[int, str]],
    order_start: int = 0,
    table_prefix: str = "ocr",
) -> list[TableBlock]:
    """
    Build pseudo tables from key-value style text lines.
    Useful as final fallback for OCR extracted text.
    """
    tables: list[TableBlock] = []
    order = order_start
    idx = 0
    for page_number, text in page_texts:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        rows: list[list[str]] = []
        for line in lines:
            key, value = _split_key_value_line(line)
            if key:
                rows.append([key, value])
        if len(rows) < 4:
            continue
        idx += 1
        order += 1
        tables.append(
            TableBlock(
                page_number=page_number,
                order=order,
                table_id=f"{table_prefix}_p{page_number}_t{idx}",
                headers=["Field", "Value"],
                rows=rows,
            )
        )
    return tables


def _extract_page_tables(page: pdfplumber.page.Page) -> list[list[list[str | None]]]:
    settings_candidates = [
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 3,
            "join_tolerance": 3,
        },
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "text_x_tolerance": 2,
            "text_y_tolerance": 2,
        },
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "text",
            "text_x_tolerance": 2,
            "text_y_tolerance": 2,
        },
    ]

    all_tables: list[list[list[str | None]]] = []
    for settings in settings_candidates:
        extracted = page.extract_tables(table_settings=settings) or []
        all_tables.extend(extracted)
        try:
            found = page.find_tables(table_settings=settings) or []
            for item in found:
                all_tables.append(item.extract())
        except Exception:
            # Some PDFs may fail for specific strategies; keep the run robust.
            continue
    return all_tables


def _fallback_extract_from_text(page: pdfplumber.page.Page) -> list[list[list[str]]]:
    text = page.extract_text() or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blocks: list[list[list[str]]] = []
    current: list[list[str]] = []

    for line in lines:
        # Heuristic: table-like lines usually use pipe delimiters or wide spacing.
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
        else:
            cells = [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]

        if len(cells) >= 2:
            current.append(cells)
            continue

        if len(current) >= 2:
            blocks.append(current)
        current = []

    if len(current) >= 2:
        blocks.append(current)

    if blocks:
        return blocks
    layout_blocks = _fallback_extract_from_word_layout(page)
    if layout_blocks:
        return layout_blocks
    return _fallback_extract_key_value_table(page)


def _fallback_extract_from_word_layout(page: pdfplumber.page.Page) -> list[list[list[str]]]:
    """Infer tables from aligned word positions when explicit table detection fails."""
    words = page.extract_words(
        use_text_flow=True,
        keep_blank_chars=False,
        x_tolerance=2,
        y_tolerance=2,
    ) or []
    if not words:
        return []

    # Group words by visual row using rounded 'top' positions.
    rows_map: dict[int, list[dict]] = {}
    for w in words:
        top = int(round(float(w.get("top", 0.0)) / 3.0) * 3)
        rows_map.setdefault(top, []).append(w)

    ordered_rows: list[list[dict]] = []
    for top in sorted(rows_map):
        row_words = sorted(rows_map[top], key=lambda it: float(it.get("x0", 0.0)))
        if row_words:
            ordered_rows.append(row_words)

    if len(ordered_rows) < 2:
        return []

    # Candidate columns are x0 clusters that appear across multiple rows.
    x_positions: list[float] = []
    for row in ordered_rows:
        for w in row:
            x_positions.append(float(w.get("x0", 0.0)))
    x_positions.sort()
    if not x_positions:
        return []

    col_centers: list[float] = []
    cluster: list[float] = [x_positions[0]]
    for x in x_positions[1:]:
        if abs(x - cluster[-1]) <= 16:
            cluster.append(x)
        else:
            col_centers.append(sum(cluster) / len(cluster))
            cluster = [x]
    col_centers.append(sum(cluster) / len(cluster))
    if len(col_centers) < 2:
        return []

    table_rows: list[list[str]] = []
    for row in ordered_rows:
        buckets = [""] * len(col_centers)
        used_cols: set[int] = set()
        for w in row:
            text = str(w.get("text", "")).strip()
            if not text:
                continue
            x0 = float(w.get("x0", 0.0))
            col_idx = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - x0))
            used_cols.add(col_idx)
            if buckets[col_idx]:
                buckets[col_idx] = f"{buckets[col_idx]} {text}"
            else:
                buckets[col_idx] = text

        # Keep rows that span at least 2 aligned columns.
        if len(used_cols) >= 2:
            table_rows.append([c.strip() for c in buckets])
        else:
            # Break on non-table row; this creates contiguous table blocks.
            if len(table_rows) >= 2:
                break
            table_rows = []

    if len(table_rows) < 2:
        return []
    return [table_rows]


def _fallback_extract_key_value_table(page: pdfplumber.page.Page) -> list[list[list[str]]]:
    """
    Final fallback:
    Convert key-value form lines into a pseudo-table, e.g.:
    'Tenant Name: John Doe' -> ['Tenant Name', 'John Doe']
    """
    text = page.extract_text() or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    rows: list[list[str]] = []
    for line in lines:
        key, value = _split_key_value_line(line)
        if not key:
            continue
        rows.append([key, value])

    # Require enough rows to avoid converting normal prose paragraphs.
    if len(rows) < 4:
        return []

    header = ["Field", "Value"]
    return [[header, *rows]]


def _split_key_value_line(line: str) -> tuple[str, str]:
    # Common key-value delimiters in contracts/forms.
    for sep in [":", "："]:
        if sep in line:
            left, right = line.split(sep, 1)
            key = left.strip()
            value = right.strip()
            if _looks_like_key(key):
                return key, value

    # Many PDFs visually separate columns with wide spaces.
    parts = [p.strip() for p in re.split(r"\s{3,}", line) if p.strip()]
    if len(parts) >= 2 and _looks_like_key(parts[0]):
        return parts[0], " ".join(parts[1:])

    return "", ""


def _looks_like_key(text: str) -> bool:
    if not text:
        return False
    # Short labels are likely keys; long sentences are likely prose.
    if len(text) > 60:
        return False
    word_count = len(text.split())
    if word_count > 10:
        return False
    # Keys often include label-like words or trailing punctuation markers.
    key_markers = ["name", "address", "date", "term", "rent", "deposit", "id", "phone", "email"]
    lower = text.lower()
    if any(marker in lower for marker in key_markers):
        return True
    return text.endswith((")", "）")) or bool(re.search(r"[A-Za-z\u4e00-\u9fff]{2,}", text))


def _normalize_table(raw: list[list[str | None]]) -> list[list[str]]:
    cleaned = [
        [(cell or "").strip() for cell in row]
        for row in raw
        if row and any((cell or "").strip() for cell in row)
    ]
    if not cleaned:
        return []

    col_count = max((len(r) for r in cleaned), default=0)
    if col_count == 0:
        return []
    return [r + [""] * (col_count - len(r)) for r in cleaned]


def _looks_like_table(table: list[list[str]]) -> bool:
    if len(table) < 2:
        return False
    col_count = max((len(r) for r in table), default=0)
    if col_count < 2:
        return False
    non_empty_cells = sum(1 for row in table for cell in row if cell.strip())
    return non_empty_cells >= 4


def _table_signature(table: list[list[str]]) -> str:
    preview_rows = table[:4]
    return "|".join("||".join(c[:40] for c in row) for row in preview_rows)
