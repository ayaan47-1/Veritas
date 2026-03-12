import pytest

from backend.app.services.ocr import OCRUnavailableError, _load_ocr_config


def test_ocr_config_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    with pytest.raises(OCRUnavailableError):
        _load_ocr_config()

