from __future__ import annotations

import re
from dataclasses import dataclass


_SECTION_RE = re.compile(r"(?:^|\s)(\d+[\.)]\s+[A-Za-z][^\n]{1,120})")
_ALL_CAPS_RE = re.compile(r"(?:^|\s)([A-Z][A-Z\s]{4,40})(?:\s|$)")


@dataclass(frozen=True)
class ChunkSlice:
    char_start: int
    char_end: int
    text: str
    split_reason: str


def _section_boundaries(text: str, min_distance: int = 250) -> list[int]:
    boundaries: list[int] = []
    for pattern in (_SECTION_RE, _ALL_CAPS_RE):
        for match in pattern.finditer(text):
            idx = match.start(1)
            if not boundaries or idx - boundaries[-1] >= min_distance:
                boundaries.append(idx)
    boundaries.sort()
    return boundaries


def split_text_into_chunks(text: str, max_chars: int) -> list[ChunkSlice]:
    if len(text) <= max_chars:
        return [ChunkSlice(0, len(text), text, "full_page")]

    boundaries = _section_boundaries(text)
    if not boundaries:
        boundaries = list(range(max_chars, len(text), max_chars))
        reason = "token_limit"
    else:
        reason = "section_split"

    chunks: list[ChunkSlice] = []
    start = 0

    for boundary in boundaries:
        if boundary <= start:
            continue
        while boundary - start > max_chars:
            split_at = min(start + max_chars, len(text))
            chunk_text = text[start:split_at]
            if chunk_text.strip():
                chunks.append(ChunkSlice(start, split_at, chunk_text, "token_limit"))
            start = split_at
        if boundary > start:
            chunk_text = text[start:boundary]
            if chunk_text.strip():
                chunks.append(ChunkSlice(start, boundary, chunk_text, reason))
            start = boundary

    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk_text = text[start:end]
        if chunk_text.strip():
            chunk_reason = reason if end == len(text) and reason == "section_split" else "token_limit"
            chunks.append(ChunkSlice(start, end, chunk_text, chunk_reason))
        start = end

    if not chunks:
        return [ChunkSlice(0, len(text), text, "token_limit")]

    return chunks
