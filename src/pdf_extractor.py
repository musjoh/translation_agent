from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
from docx import Document

from src.models import TextBlock


def extract_text_blocks(input_path: Path) -> list[TextBlock]:
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_from_pdf(input_path)
    if suffix == ".docx":
        return _extract_from_docx(input_path)
    if suffix == ".txt":
        return _extract_from_txt(input_path)
    raise ValueError(f"Unsupported input type: {suffix}")


def _extract_from_pdf(input_path: Path) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    order = 0
    with fitz.open(input_path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if not text:
                continue
            order += 1
            blocks.append(TextBlock(page_number=i, order=order, text=text))
    return blocks


def _extract_from_docx(input_path: Path) -> list[TextBlock]:
    doc = Document(str(input_path))
    text = "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    if not text:
        return []
    return [TextBlock(page_number=1, order=1, text=text)]


def _extract_from_txt(input_path: Path) -> list[TextBlock]:
    text = input_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return []
    return [TextBlock(page_number=1, order=1, text=text)]
