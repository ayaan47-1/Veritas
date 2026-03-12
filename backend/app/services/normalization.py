from __future__ import annotations

import re
import unicodedata


_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}


def normalize_text(text: str) -> str:
    """Normalize text for deterministic matching and chunking."""
    normalized = unicodedata.normalize("NFC", text or "")
    for ligature, replacement in _LIGATURES.items():
        normalized = normalized.replace(ligature, replacement)
    normalized = re.sub(r"[ \t\r\n]+", " ", normalized)
    return normalized.strip()

