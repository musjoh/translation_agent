from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import time
from typing import Callable

from src.chunker import build_chunks
from src.cleaner import normalize_blocks
from src.config import AppConfig
from src.docx_writer import write_docx
from src.models import PipelineResult, TranslatedChunk
from src.ocr_extractor import extract_text_blocks_from_pdf_ocr
from src.pdf_extractor import extract_text_blocks
from src.reconstructor import reconstruct
from src.table_extractor import extract_pseudo_tables_from_text_pages, extract_tables
from src.translator import preflight_live_api, translate_chunks
from src.utils import sanitize_output_name

ProgressCallback = Callable[[dict], None]


def _build_job_key(input_path: Path, config: AppConfig) -> str:
    file_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    cfg_fingerprint = "|".join(
        [
            config.base_url.strip().lower(),
            config.model_name.strip(),
            config.source_lang.strip(),
            config.target_lang.strip(),
        ]
    )
    return hashlib.sha256(f"{file_hash}|{cfg_fingerprint}".encode("utf-8")).hexdigest()[:24]


def _checkpoint_path(output_dir: Path, job_key: str) -> Path:
    return output_dir / "checkpoints" / f"{job_key}.json"


def _load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {"completed": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("completed"), dict):
            return data
    except Exception:
        pass
    return {"completed": {}}


def _save_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = int(time())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _emit(progress_callback: ProgressCallback | None, event: dict) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(event)
    except Exception:
        # Progress callbacks must never break core translation flow.
        return


def get_extraction_preview(input_path: Path) -> dict:
    """Return a compact preview of extracted text and tables."""
    if input_path.suffix.lower() not in {".pdf", ".docx", ".txt"}:
        return {"error": "Unsupported file type."}

    text_blocks = extract_text_blocks(input_path)
    table_blocks = extract_tables(input_path)
    ocr_debug: dict | None = None
    used_ocr = False
    if input_path.suffix.lower() == ".pdf" and not text_blocks and not table_blocks:
        text_blocks, ocr_debug = extract_text_blocks_from_pdf_ocr(input_path)
        if text_blocks:
            table_blocks = extract_pseudo_tables_from_text_pages(
                [(b.page_number, b.text) for b in text_blocks],
                order_start=0,
                table_prefix="ocr_preview",
            )
            used_ocr = True
    unified = normalize_blocks(text_blocks=text_blocks, table_blocks=table_blocks)

    text_preview = []
    for block in unified:
        if block.block_type != "text":
            continue
        snippet = block.text[:500]
        if len(block.text) > 500:
            snippet += "..."
        text_preview.append(
            {
                "page_number": block.page_number,
                "order": block.order,
                "snippet": snippet,
            }
        )

    table_preview = []
    for block in unified:
        if block.block_type != "table":
            continue
        table_preview.append(
            {
                "page_number": block.page_number,
                "table_id": block.table_id,
                "headers": block.headers,
                "rows_preview": block.rows[:5],
            }
        )

    return {
        "text_block_count": len([b for b in unified if b.block_type == "text"]),
        "table_block_count": len([b for b in unified if b.block_type == "table"]),
        "text_preview": text_preview[:8],
        "table_preview": table_preview[:3],
        "used_ocr_fallback": used_ocr,
        "ocr_debug": ocr_debug,
    }


def run_translation_pipeline(
    input_path: Path,
    output_dir: Path,
    config: AppConfig,
    progress_callback: ProgressCallback | None = None,
) -> PipelineResult:
    try:
        if input_path.suffix.lower() not in {".pdf", ".docx", ".txt"}:
            return PipelineResult(success=False, message="Unsupported file type.")

        text_blocks = extract_text_blocks(input_path)
        table_blocks = extract_tables(input_path)
        ocr_debug: dict | None = None
        used_ocr_fallback = False
        if input_path.suffix.lower() == ".pdf" and not text_blocks and not table_blocks:
            text_blocks, ocr_debug = extract_text_blocks_from_pdf_ocr(input_path)
            if text_blocks:
                table_blocks = extract_pseudo_tables_from_text_pages(
                    [(b.page_number, b.text) for b in text_blocks],
                    order_start=0,
                    table_prefix="ocr",
                )
                used_ocr_fallback = True

        unified = normalize_blocks(text_blocks=text_blocks, table_blocks=table_blocks)
        if not unified:
            return PipelineResult(success=False, message="No readable content found.")

        chunks = build_chunks(unified, max_chars=config.max_chunk_chars)
        job_key = _build_job_key(input_path, config)
        cp_path = _checkpoint_path(output_dir, job_key)
        cp_data = _load_checkpoint(cp_path) if config.resume_from_checkpoint else {"completed": {}}
        completed_map: dict[str, str] = {
            str(k): str(v)
            for k, v in cp_data.get("completed", {}).items()
        }
        valid_chunk_ids = {c.chunk_id for c in chunks}
        completed_map = {k: v for k, v in completed_map.items() if k in valid_chunk_ids}
        pending_chunks = [c for c in chunks if c.chunk_id not in completed_map]
        _emit(
            progress_callback,
            {
                "type": "start",
                "total_chunks": len(chunks),
                "completed_chunks": len(completed_map),
                "pending_chunks": len(pending_chunks),
                "checkpoint_path": str(cp_path),
            },
        )

        if not config.use_mock_translator and pending_chunks:
            ok, err = preflight_live_api(config)
            if not ok:
                _emit(
                    progress_callback,
                    {"type": "preflight_failed", "error": err},
                )
                return PipelineResult(
                    success=False,
                    message="Live API preflight failed. Check key, model, network, or SSL settings.",
                    debug={
                        "preflight_error": err,
                        "resume_info": {
                            "checkpoint_path": str(cp_path),
                            "completed_chunks": len(completed_map),
                            "pending_chunks": len(pending_chunks),
                        },
                    },
                )

        for idx, chunk in enumerate(pending_chunks, start=1):
            translated_one = translate_chunks([chunk], config=config)[0]
            if translated_one.success:
                completed_map[chunk.chunk_id] = translated_one.translated_text
                _emit(
                    progress_callback,
                    {
                        "type": "chunk_success",
                        "chunk_id": chunk.chunk_id,
                        "page_number": chunk.page_number,
                        "completed_chunks": len(completed_map),
                        "total_chunks": len(chunks),
                    },
                )
                _save_checkpoint(
                    cp_path,
                    {
                        "job_key": job_key,
                        "input_name": input_path.name,
                        "model_name": config.model_name,
                        "created_at": cp_data.get("created_at", int(time())),
                        "completed": completed_map,
                        "last_status": "in_progress",
                        "completed_chunks": len(completed_map),
                        "total_chunks": len(chunks),
                    },
                )
                continue

            _save_checkpoint(
                cp_path,
                {
                    "job_key": job_key,
                    "input_name": input_path.name,
                    "model_name": config.model_name,
                    "created_at": cp_data.get("created_at", int(time())),
                    "completed": completed_map,
                    "last_status": "failed",
                    "failed_chunk_id": chunk.chunk_id,
                    "failed_error": translated_one.error or "Unknown error",
                    "completed_chunks": len(completed_map),
                    "total_chunks": len(chunks),
                },
            )
            _emit(
                progress_callback,
                {
                    "type": "chunk_failed",
                    "chunk_id": chunk.chunk_id,
                    "page_number": chunk.page_number,
                    "error": translated_one.error or "Unknown error",
                    "completed_chunks": len(completed_map),
                    "total_chunks": len(chunks),
                },
            )
            return PipelineResult(
                success=False,
                message="Translation interrupted. Progress has been saved; rerun to resume.",
                debug={
                    "failed_chunk_count": 1,
                    "failed_chunk_ids": [chunk.chunk_id],
                    "failed_examples": [
                        {
                            "chunk_id": chunk.chunk_id,
                            "error": translated_one.error or "Unknown error",
                        }
                    ],
                    "resume_info": {
                        "checkpoint_path": str(cp_path),
                        "completed_chunks": len(completed_map),
                        "pending_chunks": len(chunks) - len(completed_map),
                        "next_chunk_index": idx,
                    },
                },
            )

        translated: list[TranslatedChunk] = [
            TranslatedChunk(chunk_id=c.chunk_id, translated_text=completed_map.get(c.chunk_id, ""))
            for c in chunks
        ]

        text_items, table_items = reconstruct(unified, chunks, translated)

        output_name = sanitize_output_name(input_path)
        output_path = output_dir / output_name
        write_docx(output_path, text_items, table_items, bilingual=config.bilingual)
        _save_checkpoint(
            cp_path,
            {
                "job_key": job_key,
                "input_name": input_path.name,
                "model_name": config.model_name,
                "created_at": cp_data.get("created_at", int(time())),
                "completed": completed_map,
                "last_status": "completed",
                "completed_chunks": len(completed_map),
                "total_chunks": len(chunks),
            },
        )
        _emit(
            progress_callback,
            {
                "type": "completed",
                "completed_chunks": len(completed_map),
                "total_chunks": len(chunks),
                "output_path": str(output_path),
            },
        )

        return PipelineResult(
            success=True,
            message="Translation complete.",
            output_path=str(output_path),
            debug={
                "source_blocks": len(unified),
                "chunks": len(chunks),
                "tables": len(table_items),
                "resume_info": {
                    "checkpoint_path": str(cp_path),
                    "reused_completed_chunks": len(chunks) - len(pending_chunks),
                    "translated_this_run": len(pending_chunks),
                },
                    "used_ocr_fallback": used_ocr_fallback,
                    "ocr_debug": ocr_debug,
            },
        )
    except Exception as exc:
        return PipelineResult(success=False, message=f"Pipeline error: {exc}")
