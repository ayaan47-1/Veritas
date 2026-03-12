from backend.app.services.normalization import normalize_text


def test_normalize_text_collapses_whitespace_and_trims() -> None:
    raw = "  Alpha\n\nBeta\t\tGamma  "
    assert normalize_text(raw) == "Alpha Beta Gamma"


def test_normalize_text_expands_ligatures() -> None:
    raw = "o\ufb03ce \ufb00 \ufb01 \ufb02 \ufb04"
    assert normalize_text(raw) == "office ff fi fl ffl"

