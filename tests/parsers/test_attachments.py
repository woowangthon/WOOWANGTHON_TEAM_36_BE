"""첨부 형식 판별·텍스트 추출 테스트. 저장한 실제 첨부와 테스트 안에서 만든 파일만 씁니다."""

import io
import logging
import zipfile
from pathlib import Path

import pytest

from app.parsers import attachments
from app.parsers.attachments import read_attachment

FIXTURES = Path(__file__).parents[1] / "fixtures"
HWPX = FIXTURES / "aibc" / "files" / "avlP7Izd8kzA7Ygfv67AO7.hwpx"  # 학생활동 지원 신청서
KOSAF_ZIP = FIXTURES / "kosaf" / "files" / "21427_1.zip"  # 안에 PDF 1개 + HWP 1개(CP949 이름)
PDF = FIXTURES / "kosaf" / "files" / "21475_1.pdf"
SCAN_PDF = FIXTURES / "sw_dept" / "pdf" / "1154721_0.pdf"


def make_zip(files: dict[str, bytes]) -> bytes:
    """UTF-8 이름 ZIP. CP949 이름은 실제 KOSAF ZIP으로 확인합니다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_docx(*paragraphs: str) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    return make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": xml.encode()})


# --- 형식별 ---


def test_pdf():
    [part] = read_attachment(PDF.read_bytes(), name="회의록.pdf")
    assert (part.kind, part.status) == ("PDF", "OK")
    assert "국가과학기술 장학사업 운영위원회 회의록" in part.text


def test_scanned_pdf_is_no_text():
    [part] = read_attachment(SCAN_PDF.read_bytes(), name="스캔.pdf")
    assert (part.kind, part.status, part.text) == ("PDF", "NO_TEXT", "")


def test_real_hwpx_keeps_paragraph_order():
    [part] = read_attachment(HWPX.read_bytes(), name="신청서.hwpx")
    assert (part.kind, part.status) == ("HWPX", "OK")
    lines = part.text.splitlines()
    assert lines[:2] == ["[별지 제1호 서식]", "학생활동 지원 신청서"]
    assert "□ 1학기 □ 2학기 ( 해당란에 표시 )" in lines


def test_docx():
    [part] = read_attachment(make_docx("평점 3.0 이상", "소득 8구간 이내"), name="공고.docx")
    assert (part.kind, part.status, part.text) == ("DOCX", "OK", "평점 3.0 이상\n소득 8구간 이내")


def test_extension_is_ignored_for_detection():
    [part] = read_attachment(PDF.read_bytes(), name="공고문.hwp")
    assert part.kind == "PDF"


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "OTHER"),  # HWP가 아닌 OLE
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 100, "IMAGE"),
        (
            b"<html>OOPS! \xec\x9e\x98\xeb\xaa\xbb\xeb\x90\x9c \xec\x9a\x94\xec\xb2\xad</html>",
            "OTHER",
        ),
    ],
)
def test_unreadable_formats_are_unsupported(data, kind):
    [part] = read_attachment(data, name="첨부.hwp")
    assert (part.kind, part.status, part.text) == (kind, "UNSUPPORTED", "")


# --- ZIP ---


def test_real_kosaf_zip_gives_one_part_per_file():
    parts = read_attachment(KOSAF_ZIP.read_bytes(), name="붙임1.zip")
    assert [(p.name, p.kind, p.status) for p in parts] == [
        (
            "붙임1.zip / (KOSAF기부펀드 일상회복) 고립은둔 지원 프로그램 확인서.hwp",
            "HWP",
            "OK",
        ),
        (
            "붙임1.zip / 2026년 2학기 푸른등대 기부장학금(2차) 사업계획서 및 업무처리기준.pdf",
            "PDF",
            "OK",
        ),
    ]
    assert "고립·은둔 지원 프로그램 참가 확인서" in parts[0].text
    assert "KOSAF기부펀드 일상회복" in parts[1].text


def test_zip_with_documents_inside_skips_folders():
    data = make_zip({"안내.docx": make_docx("지원 자격"), "폴더/": b""})
    [part] = read_attachment(data, name="첨부.zip")
    assert (part.name, part.kind, part.status, part.text) == (
        "첨부.zip / 안내.docx",
        "DOCX",
        "OK",
        "지원 자격",
    )


def test_zip_inside_zip_is_not_opened():
    inner = make_zip({"a.pdf": PDF.read_bytes()})
    [part] = read_attachment(make_zip({"inner.zip": inner}), name="outer.zip")
    assert (part.name, part.kind, part.status) == ("outer.zip / inner.zip", "ZIP", "UNSUPPORTED")


def test_broken_zip_is_failed():
    [part] = read_attachment(b"PK\x03\x04" + b"\0" * 50, name="broken.zip")
    assert (part.kind, part.status) == ("ZIP", "FAILED")


def test_size_limits(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_FILE_BYTES", 1000)
    [big] = read_attachment(b"%PDF-" + b"0" * 2000, name="big.pdf")
    assert big.status == "TOO_LARGE"

    # 헤더 크기와 상관없이 실제로 풀린 크기로 판단합니다.
    [inside] = read_attachment(make_zip({"big.pdf": b"0" * 2000}), name="a.zip")
    assert (inside.name, inside.status) == ("a.zip / big.pdf", "TOO_LARGE")


def test_too_many_zip_members(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_ZIP_MEMBERS", 2)
    [part] = read_attachment(make_zip({f"{i}.txt": b"x" for i in range(3)}), name="many.zip")
    assert (part.kind, part.status) == ("ZIP", "TOO_LARGE")


def test_logs_have_names_but_not_content(caplog):
    data = make_zip({"비밀.hwp": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1secret"})
    with caplog.at_level(logging.DEBUG, logger="app.parsers.attachments"):
        read_attachment(data, name="z.zip")
    [record] = caplog.records
    assert record.fields == {"file_name": "z.zip / 비밀.hwp"}
    assert "secret" not in caplog.text
