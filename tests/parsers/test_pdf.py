"""PDF 텍스트 추출 테스트 (T14). 저장 PDF fixture와 테스트 안에서 만든 PDF만 씁니다."""

import logging
from pathlib import Path

import pymupdf
import pytest

from app.parsers.pdf import extract_text, is_pdf

PDFS = Path(__file__).parents[1] / "fixtures" / "sw_dept" / "pdf"


def make_pdf(text: str = "장학금 안내", **save_options) -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text, fontname="korea")
    return document.tobytes(**save_options)


def test_extracts_fixture_pdf_text():
    text = extract_text((PDFS / "1154016_0.pdf").read_bytes(), name="1154016_0.pdf")
    assert text.startswith("2027학년도 1학기 소프트웨어학부")
    assert "성적우수 장학생" in text
    assert all(line.strip() for line in text.splitlines())


def test_extracts_multi_page_pdf():
    text = extract_text((PDFS / "1154665_1.pdf").read_bytes())
    assert "두산연강재단 장학생 선발 공고" in text


def test_extracts_generated_pdf():
    assert extract_text(make_pdf("장학금 안내")) == "장학금 안내"


def test_encrypted_pdf_returns_empty_with_warning(caplog):
    data = make_pdf(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    with caplog.at_level(logging.WARNING, logger="app.parsers.pdf"):
        assert extract_text(data, name="locked.pdf") == ""
    assert "암호" in caplog.text


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.4\n garbage without objects",
        make_pdf()[:200],  # 잘린 PDF
    ],
)
def test_broken_pdf_returns_empty_with_warning(data, caplog):
    with caplog.at_level(logging.WARNING, logger="app.parsers.pdf"):
        assert extract_text(data, name="broken.pdf") == ""
    assert caplog.records


def test_html_error_page_is_not_pdf(caplog):
    # 학부 게시판 첨부를 쿠키·Referer 없이 받으면 200 응답으로 오류 HTML이 옴
    html = b"<!DOCTYPE html><html><body>OOPS! \xec\x9e\x98\xeb\xaa\xbb\xeb\x90\x9c</body></html>"
    assert not is_pdf(html)
    with caplog.at_level(logging.WARNING, logger="app.parsers.pdf"):
        assert extract_text(html, name="oops.pdf") == ""
    assert "PDF가 아닌" in caplog.text


def test_scanned_pdf_without_text_returns_empty(caplog):
    # 1154721_0.pdf는 이미지만 있는 스캔본(OCR 하지 않음)
    with caplog.at_level(logging.WARNING, logger="app.parsers.pdf"):
        assert extract_text((PDFS / "1154721_0.pdf").read_bytes(), name="scan.pdf") == ""
    assert "스캔본" in caplog.text


def test_log_does_not_contain_pdf_text(caplog):
    with caplog.at_level(logging.DEBUG, logger="app.parsers.pdf"):
        extract_text(make_pdf("비밀 내용"), name="ok.pdf")
    assert "비밀 내용" not in caplog.text
