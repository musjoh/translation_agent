from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
import pytesseract

from src.models import TextBlock


def extract_text_blocks_from_pdf_ocr(input_path: Path) -> tuple[list[TextBlock], dict]:
    """
    OCR fallback for scanned/image PDFs.
    Returns text blocks and a small debug summary.
    """
    blocks: list[TextBlock] = []
    pages_scanned = 0
    pages_with_text = 0
    order = 0

    with fitz.open(input_path) as doc:
        for page_no, page in enumerate(doc, start=1):
            pages_scanned += 1
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = _ocr_image(img).strip()
            if not text:
                continue
            pages_with_text += 1
            order += 1
            blocks.append(TextBlock(page_number=page_no, order=order, text=text))

    return blocks, {
        "pages_scanned": pages_scanned,
        "pages_with_text": pages_with_text,
        "text_blocks": len(blocks),
        "engine": "tesseract",
    }


def _ocr_image(image: Image.Image) -> str:
    # Try Chinese+English first; fallback to English if language pack missing.
    try:
        return pytesseract.image_to_string(image, lang="chi_sim+eng")
    except Exception:
        try:
            return pytesseract.image_to_string(image, lang="eng")
        except Exception:
            return ""
