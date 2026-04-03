from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from urllib import request
from urllib.error import HTTPError, URLError

import fitz


class OCRUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRConfig:
    api_key: str
    endpoint: str
    model: str


def _load_ocr_config() -> OCRConfig:
    api_key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if not api_key:
        raise OCRUnavailableError("DEEPINFRA_API_KEY is not configured")

    return OCRConfig(
        api_key=api_key,
        endpoint=os.getenv("DEEPINFRA_OLMOCR_URL", "https://api.deepinfra.com/v1/openai/chat/completions"),
        model=os.getenv("DEEPINFRA_OLMOCR_MODEL", "allenai/olmOCR-2-7B-1025"),
    )


def ocr_pdf_page(file_path: str, page_number: int) -> str:
    cfg = _load_ocr_config()

    with fitz.open(file_path) as doc:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(dpi=150, alpha=False)
        image_bytes = pix.tobytes("jpeg")

    payload = {
        "model": cfg.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract text from this page. Return plain text only."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(image_bytes).decode("ascii")
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
    }

    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        cfg.endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise OCRUnavailableError(f"DeepInfra OCR HTTP error: {exc.code}") from exc
    except URLError as exc:
        raise OCRUnavailableError(f"DeepInfra OCR unavailable: {exc.reason}") from exc

    choices = response.get("choices", [])
    if not choices:
        raise OCRUnavailableError("DeepInfra OCR returned no choices")

    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        content = "\n".join(parts)

    text = str(content).strip()
    if not text:
        raise OCRUnavailableError("DeepInfra OCR returned empty text")

    return text

