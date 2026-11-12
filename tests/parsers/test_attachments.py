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
# Excel로 만든 작은 통합 문서: 시트 3개(참여 대학 표, 날짜 서식이 있는 일정, 빈 시트)
XLSX = FIXTURES / "xlsx" / "participating_universities.xlsx"


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


XLSX_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_xlsx(
    sheets: dict[str, str],
    *,
    shared: list[str] | None = None,
    styles: str | None = None,
    date1904: bool = False,
) -> bytes:
    """시트 이름 → `<sheetData>` 안의 XML. `shared`는 `<si>` 안의 XML입니다."""
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    entries = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheets, 1)
    )
    workbook = f'<workbook {XLSX_NS} xmlns:r="{REL_NS}">{pr}<sheets>{entries}</sheets></workbook>'
    # 실제 파일처럼 관계 순서와 시트 파일 번호가 시트 순서와 다르게 둡니다.
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="{REL_NS}/worksheet" Target="worksheets/s{9 - i}.xml"/>'
        for i in reversed(range(1, len(sheets) + 1))
    )
    files = {
        "[Content_Types].xml": b"<Types/>",
        "xl/workbook.xml": workbook.encode(),
        "xl/_rels/workbook.xml.rels": f"<Relationships>{rels}</Relationships>".encode(),
    }
    for i, data in enumerate(sheets.values(), 1):
        xml = f"<worksheet {XLSX_NS}><sheetData>{data}</sheetData></worksheet>"
        files[f"xl/worksheets/s{9 - i}.xml"] = xml.encode()
    if shared is not None:
        items = "".join(f"<si>{s}</si>" for s in shared)
        files["xl/sharedStrings.xml"] = f"<sst {XLSX_NS}>{items}</sst>".encode()
    if styles is not None:
        files["xl/styles.xml"] = f"<styleSheet {XLSX_NS}>{styles}</styleSheet>".encode()
    return make_zip(files)


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


def test_real_xlsx_gives_sheet_headers_and_rows():
    [part] = read_attachment(XLSX.read_bytes(), name="붙임3. 참여 대학.xlsx")
    assert (part.kind, part.status) == ("XLSX", "OK")
    assert part.text == (
        "[시트: 참여 대학]\n"
        "연번 | 지역 | 대학명 | 비고\n"
        "1 | 서울 | 가나대학교\n"
        "2 | 충북 | 충북대학교 | 본교 분교 포함\n"  # 칸 안의 줄바꿈은 공백으로
        "3 | | 다라대학교 | 2.5\n"  # 빈 칸도 자리를 지켜 열이 어긋나지 않음
        "[시트: 일정]\n"
        "신청 마감 | 2026-12-01\n"  # 날짜 서식 칸은 일련번호(46357)가 아니라 날짜로
        "근로 시간 | 주 40시간 이내"
    )  # 빈 시트는 넣지 않음


def test_xlsx_inside_zip_is_read():
    data = make_zip({"붙임3. 2026년 동계방학 집중근로 참여 대학.xlsx": XLSX.read_bytes()})
    [part] = read_attachment(data, name="붙임.zip")
    assert (part.name, part.kind, part.status) == (
        "붙임.zip / 붙임3. 2026년 동계방학 집중근로 참여 대학.xlsx",
        "XLSX",
        "OK",
    )
    assert "2 | 충북 | 충북대학교" in part.text


def test_xlsx_cell_types():
    shared = [
        "<t>공유 문자열</t>",
        "<r><t>서식 </t></r><r><t>섞인 글자</t></r><rPh><t>후리가나</t></rPh>",
    ]
    styles = (
        '<numFmts><numFmt numFmtId="164" formatCode="yyyy&quot;년&quot; m&quot;월&quot;"/>'
        '<numFmt numFmtId="165" formatCode="0.0&quot;점&quot;"/></numFmts>'
        '<cellStyleXfs><xf numFmtId="14"/></cellStyleXfs>'  # 셀 서식(cellXfs)만 봄
        '<cellXfs><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/>'
        '<xf numFmtId="22"/></cellXfs>'
    )
    row = (
        '<row r="1">'
        '<c r="A1" t="s"><v>0</v></c>'
        '<c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="inlineStr"><is><t>직접 입력</t></is></c>'
        '<c r="D1" t="b"><v>1</v></c>'
        '<c r="E1" t="str"><f>A1</f><v>수식 결과</v></c>'
        '<c r="F1" s="1"><v>46357</v></c>'  # 사용자 날짜 서식
        '<c r="G1" s="2"><v>3.5</v></c>'  # 숫자 서식(날짜 아님)
        '<c r="H1" s="3"><v>46357.75</v></c>'  # 날짜+시각
        '<c r="I1"><v>4.4999999999999996</v></c>'  # 부동소수점 끝자리
        '<c r="J1" t="s"><v>99</v></c>'  # 없는 공유 문자열 번호
        "</row>"
    )
    [part] = read_attachment(make_xlsx({"표": row}, shared=shared, styles=styles), name="a.xlsx")
    assert part.text == (
        "[시트: 표]\n"
        "공유 문자열 | 서식 섞인 글자 | 직접 입력 | TRUE | 수식 결과 | 2026-12-01 | 3.5 "
        "| 2026-12-01 18:00 | 4.5"
    )


def test_xlsx_1904_date_system():
    styles = '<cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs>'
    row = '<row r="1"><c r="A1" s="1"><v>44895</v></c></row>'
    data = make_xlsx({"표": row}, styles=styles, date1904=True)
    [part] = read_attachment(data, name="a.xlsx")
    assert part.text == "[시트: 표]\n2026-12-01"


def test_xlsx_without_cell_references_keeps_order():
    """`r` 속성이 없는 행·칸(다른 프로그램이 만든 파일)은 나온 순서대로 놓습니다."""
    rows = "<row><c><v>1</v></c><c><v>2</v></c></row><row><c><v>3</v></c></row>"
    [part] = read_attachment(make_xlsx({"표": rows}), name="a.xlsx")
    assert part.text == "[시트: 표]\n1 | 2\n3"


def test_xlsx_aligns_columns_from_leftmost_used_column():
    rows = '<row r="2"><c r="C2"><v>1</v></c><c r="E2"><v>2</v></c></row>'
    rows += '<row r="3"><c r="D3"><v>3</v></c></row>'
    [part] = read_attachment(make_xlsx({"표": rows}), name="a.xlsx")
    assert part.text == "[시트: 표]\n1 | | 2\n| 3"


def test_xlsx_very_wide_row_is_not_padded():
    """A열과 XFD열(16384번째)만 쓴 행을 빈 칸 수만 개로 부풀리지 않습니다."""
    rows = '<row r="1"><c r="A1"><v>1</v></c><c r="XFD1"><v>2</v></c></row>'
    [part] = read_attachment(make_xlsx({"표": rows}), name="a.xlsx")
    assert part.text == "[시트: 표]\n1 | 2"


def test_empty_xlsx_is_no_text():
    [part] = read_attachment(make_xlsx({"빈 시트": ""}), name="a.xlsx")
    assert (part.kind, part.status, part.text) == ("XLSX", "NO_TEXT", "")


def test_broken_xlsx_sheet_is_failed():
    data = make_xlsx({"표": "<row><c><v>1</v></row>"})  # 닫는 태그가 어긋남
    [part] = read_attachment(data, name="a.xlsx")
    assert (part.kind, part.status) == ("XLSX", "FAILED")


def test_xlsx_sheet_xml_size_is_capped(monkeypatch):
    """압축을 풀면 커지는 시트 XML도 첨부 1개 상한으로 막습니다."""
    rows = "".join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, 200))
    data = make_xlsx({"표": rows})
    monkeypatch.setattr(attachments, "MAX_FILE_BYTES", len(data) + 100)
    [part] = read_attachment(data, name="a.xlsx")
    assert (part.kind, part.status) == ("XLSX", "FAILED")


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
