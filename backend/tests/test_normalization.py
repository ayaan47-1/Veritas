from backend.app.services.normalization import normalize_text


def test_normalize_text_collapses_whitespace_and_trims() -> None:
    raw = "  Alpha\n\nBeta\t\tGamma  "
    assert normalize_text(raw) == "Alpha Beta Gamma"


def test_normalize_text_expands_ligatures() -> None:
    raw = "o\ufb03ce \ufb00 \ufb01 \ufb02 \ufb04"
    assert normalize_text(raw) == "office ff fi fl ffl"


def test_normalize_text_converts_smart_quotes() -> None:
    raw = "the tenant\u2019s obligation to pay \u201crent\u201d"
    assert normalize_text(raw) == "the tenant's obligation to pay \"rent\""


def test_normalize_text_converts_dashes() -> None:
    raw = "8:00 AM \u2013 8:00 PM \u2014 business hours"
    assert normalize_text(raw) == "8:00 AM - 8:00 PM - business hours"


def test_normalize_text_converts_non_breaking_spaces() -> None:
    raw = "section\u00a05.1\u2009applies"
    assert normalize_text(raw) == "section 5.1 applies"


def test_normalize_text_converts_ellipsis() -> None:
    raw = "including but not limited to\u2026"
    assert normalize_text(raw) == "including but not limited to..."


def test_normalize_text_smart_quote_match_enables_exact_verification() -> None:
    """The core bug: PDF text has smart quotes, LLM returns straight quotes."""
    pdf_text = "recover 1 month\u2019s rent or twice the damages"
    llm_text = "recover 1 month's rent or twice the damages"
    assert normalize_text(pdf_text) == normalize_text(llm_text)

