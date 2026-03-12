from backend.app.services.chunking import split_text_into_chunks


def test_chunking_uses_full_page_when_under_limit() -> None:
    text = "Payment due within 10 days."
    chunks = split_text_into_chunks(text, max_chars=4000)

    assert len(chunks) == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)
    assert chunks[0].split_reason == "full_page"
    assert chunks[0].text == text


def test_chunking_uses_token_limit_when_no_section_boundaries() -> None:
    text = "x" * 120
    chunks = split_text_into_chunks(text, max_chars=50)

    assert len(chunks) == 3
    assert all(c.split_reason == "token_limit" for c in chunks)
    assert sum(len(c.text) for c in chunks) == len(text)


def test_chunking_detects_section_headers() -> None:
    text = (
        "1. SCOPE contractor shall submit documents promptly. "
        "2. PAYMENT owner shall pay within 15 days of invoice."
    )
    chunks = split_text_into_chunks(text, max_chars=80)

    assert len(chunks) >= 2
    assert any(c.split_reason == "section_split" for c in chunks)
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end <= len(text)

