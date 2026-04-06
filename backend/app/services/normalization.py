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

_TYPOGRAPHIC = {
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote (apostrophe)
    "\u201a": "'",   # single low-9 quote
    "\u201b": "'",   # single high-reversed-9 quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u201e": '"',   # double low-9 quote
    "\u201f": '"',   # double high-reversed-9 quote
    "\u2013": "-",   # en-dash
    "\u2014": "-",   # em-dash
    "\u2015": "-",   # horizontal bar
    "\u2026": "...", # ellipsis
    "\u00a0": " ",   # non-breaking space
    "\u2009": " ",   # thin space
    "\u200a": " ",   # hair space
    "\u202f": " ",   # narrow no-break space
}


def normalize_text(text: str) -> str:
    """Normalize text for deterministic matching and chunking."""
    normalized = unicodedata.normalize("NFC", text or "")
    for ligature, replacement in _LIGATURES.items():
        normalized = normalized.replace(ligature, replacement)
    for char, replacement in _TYPOGRAPHIC.items():
        normalized = normalized.replace(char, replacement)
    normalized = re.sub(r"[ \t\r\n]+", " ", normalized)
    return normalized.strip()

