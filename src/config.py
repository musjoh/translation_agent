from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppConfig:
    api_key: str
    base_url: str
    model_name: str
    source_lang: str
    target_lang: str
    bilingual: bool = False
    use_mock_translator: bool = True
    max_chunk_chars: int = 1400
    retry_count: int = 4
    connect_timeout_sec: int = 15
    read_timeout_sec: int = 120
    resume_from_checkpoint: bool = True
