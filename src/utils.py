from __future__ import annotations

from pathlib import Path
from uuid import uuid4


def make_job_id() -> str:
    return uuid4().hex[:10]


def sanitize_output_name(input_path: Path) -> str:
    stem = input_path.stem.replace(" ", "_")
    return f"translated_{stem}.docx"
