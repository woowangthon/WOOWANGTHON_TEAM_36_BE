"""첨부파일 텍스트 추출: 형식 판별 + 형식별 추출 (첨부 기반 조건 추출 설계 2단계).

- 형식은 확장자가 아니라 **파일 앞부분 시그니처**로 고릅니다. 학부 게시판은 세션이 없으면
  200 응답으로 HTML 오류 페이지를 주고, 확장자와 실제 형식이 다른 파일도 있습니다.
- 지원: PDF(`parsers/pdf.py`), HWP 5.0(`parsers/hwp.py`), HWPX·DOCX·XLSX(ZIP 안의 XML),
  ZIP(안의 파일을 한 단계만 다시 판별). 이미지·스캔본·옛 XLS는 읽지 않습니다(D3).
- XLSX는 시트마다 `[시트: 이름]` 줄 뒤에 행마다 칸을 ` | `로 이어 붙여 표 모양을 남깁니다
  (참여 대학 목록처럼 조건이 엑셀에만 있는 공지가 있음).
- ZIP은 **안의 파일마다 결과를 하나씩** 돌려줍니다. "ZIP 안의 HWP를 못 읽음"이 상태에 남아야
  판정에서 "확인 필요"로 낮출 수 있기 때문입니다(D8).
- 압축 폭탄을 막으려고 안의 파일 수·파일 크기·전체 크기에 상한을 두고, ZIP 안의 ZIP은 풀지 않습니다.
- lxml은 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).
- 로그에는 파일 이름과 이유만 남기고 내용은 남기지 않습니다.
"""

import io
import logging
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from app.core.logging import log_event
from app.parsers.hwp import is_hwp, read_hwp
from app.parsers.pdf import is_pdf, read_pdf

logger = logging.getLogger(__name__)

FileKind = Literal["PDF", "HWP", "HWPX", "DOCX", "XLSX", "IMAGE", "ZIP", "OTHER"]
ReadStatus = Literal["OK", "NO_TEXT", "UNSUPPORTED", "FAILED", "TOO_LARGE"]

MAX_FILE_BYTES = 20 * 1024 * 1024  # 첨부 1개, ZIP 안의 파일 1개
MAX_ZIP_MEMBERS = 30
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024  # ZIP 하나를 풀었을 때 합계

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # HWP 5.0, 옛 DOC·XLS
_ZIP = b"PK\x03\x04"
_IMAGES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")
_SECTION = re.compile(r"^Contents/section(\d+)\.xml$")

# XLSX
MAX_XLSX_COLUMNS = 64  # 행의 칸 자리를 맞추는 최대 열 수. 넘으면 값이 있는 칸만 잇습니다.
_CELL_COLUMN = re.compile(r"^([A-Z]{1,3})")
# 엑셀 내장 날짜·시각 서식 번호(ECMA-376 18.8.30). 사용자 서식은 서식 문자열로 판단합니다.
_BUILTIN_DATE_FORMATS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})
_FORMAT_LITERAL = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')  # 따옴표 글자·[색]·[$-412]·\x
_EPOCH_1900 = date(1899, 12, 30)  # 1900 날짜 체계(윤년 버그 반영)의 0
_EPOCH_1904 = date(1904, 1, 1)
# 한글 문서의 체크 표시 등은 글꼴 전용 사용자 영역 문자(U+E000–U+F8FF)라 텍스트로는 의미가 없습니다.
_PRIVATE_USE = re.compile(r"[-]")


@dataclass(frozen=True)
class AttachmentPart:
    """첨부 1개(또는 ZIP 안의 파일 1개)를 읽은 결과."""

    name: str  # ZIP 안의 파일은 "ZIP 이름 / 안의 파일 이름"
    kind: FileKind
    status: ReadStatus
    text: str = ""


def read_attachment(data: bytes, *, name: str) -> list[AttachmentPart]:
    """첨부 바이트를 읽습니다. 보통 1개, ZIP이면 안의 파일 수만큼 돌려줍니다."""
    if len(data) > MAX_FILE_BYTES:
        return [_part(name, "OTHER", "TOO_LARGE", reason="크기 상한 초과", size=len(data))]
    if data.startswith(_ZIP):
        return _read_zip_family(data, name=name, inside_zip=False)
    return [_read_single(data, name=name)]


def _read_single(data: bytes, *, name: str) -> AttachmentPart:
    if is_pdf(data):
        status, text = read_pdf(data, name=name)
        return AttachmentPart(name=name, kind="PDF", status=status, text=text)
    if data.startswith(_OLE):
        if not is_hwp(data):
            return _part(name, "OTHER", "UNSUPPORTED", reason="HWP가 아닌 OLE 형식(DOC·XLS 등)")
        status, text, reason = read_hwp(data)
        if status == "FAILED":
            return _part(name, "HWP", "FAILED", reason=reason)
        text = _clean(text)
        if not text:
            return _part(name, "HWP", "NO_TEXT", reason="텍스트가 없는 HWP")
        return AttachmentPart(name=name, kind="HWP", status="OK", text=text)
    if data.startswith(_IMAGES):
        return _part(name, "IMAGE", "UNSUPPORTED", reason="이미지는 읽지 않음")
    return _part(name, "OTHER", "UNSUPPORTED", reason="알 수 없는 형식", size=len(data))


def _read_zip_family(data: bytes, *, name: str, inside_zip: bool) -> list[AttachmentPart]:
    """PK로 시작하는 파일: HWPX·DOCX는 문서로 읽고, 그 밖의 ZIP은 안의 파일을 다시 판별합니다."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = set(archive.namelist())
        if any(_SECTION.match(n) for n in names):
            return [_document(name, "HWPX", lambda: _hwpx_text(archive))]
        if "word/document.xml" in names:
            return [_document(name, "DOCX", lambda: _docx_text(archive))]
        if "xl/workbook.xml" in names:
            return [_document(name, "XLSX", lambda: _xlsx_text(archive))]
        if inside_zip:
            return [_part(name, "ZIP", "UNSUPPORTED", reason="ZIP 안의 ZIP은 풀지 않음")]
        return _read_zip(archive, name=name)
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        kind: FileKind = "ZIP" if not inside_zip else "OTHER"
        return [_part(name, kind, "FAILED", reason="ZIP을 열지 못함", error=type(error).__name__)]


def _read_zip(archive: zipfile.ZipFile, *, name: str) -> list[AttachmentPart]:
    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_ZIP_MEMBERS:
        return [_part(name, "ZIP", "TOO_LARGE", reason="ZIP 안 파일 수 초과", count=len(members))]
    parts: list[AttachmentPart] = []
    total = 0
    for info in members:
        member_name = f"{name} / {_member_name(info)}"
        # 헤더의 크기는 속일 수 있으므로 상한 + 1바이트까지만 읽어 실제 크기를 확인합니다.
        with archive.open(info) as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_ZIP_TOTAL_BYTES:
            parts.append(_part(member_name, "OTHER", "TOO_LARGE", reason="ZIP 안 파일 크기 초과"))
            continue
        if data.startswith(_ZIP):
            parts.extend(_read_zip_family(data, name=member_name, inside_zip=True))
        else:
            parts.append(_read_single(data, name=member_name))
    return parts


def _member_name(info: zipfile.ZipInfo) -> str:
    """ZIP 안의 파일 이름. UTF-8 표시가 없으면 한국 윈도우 압축 프로그램의 CP949로 봅니다."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _document(name: str, kind: FileKind, read) -> AttachmentPart:  # noqa: ANN001
    try:
        text = _clean(read())
    except Exception as error:  # 깨진 XML 등
        return _part(name, kind, "FAILED", reason="문서를 읽지 못함", error=type(error).__name__)
    if not text:
        return _part(name, kind, "NO_TEXT", reason="텍스트가 없는 문서")
    return AttachmentPart(name=name, kind=kind, status="OK", text=text)


def _hwpx_text(archive: zipfile.ZipFile) -> str:
    """HWPX: `Contents/section0.xml`, `section1.xml`, … 순서로 문단(`hp:p`)의 글자(`hp:t`)."""
    sections = sorted((int(m.group(1)), n) for n in archive.namelist() if (m := _SECTION.match(n)))
    return "\n".join(_paragraphs(archive.read(n), paragraph="p", run="t") for _, n in sections)


def _docx_text(archive: zipfile.ZipFile) -> str:
    """DOCX: `word/document.xml`의 문단(`w:p`)의 글자(`w:t`)."""
    return _paragraphs(archive.read("word/document.xml"), paragraph="p", run="t")


def _xlsx_text(archive: zipfile.ZipFile) -> str:
    """XLSX: 통합 문서의 시트 순서대로 `[시트: 이름]` + 행마다 `칸 | 칸 | …`. 빈 시트는 뺍니다.

    공유 문자열(`xl/sharedStrings.xml`)과 셀 서식(`xl/styles.xml`)은 없을 수도 있습니다.
    날짜 서식 칸은 일련번호 대신 `2026-12-01`(시각이 있으면 `2026-12-01 18:00`)로 적습니다.
    """
    workbook = _read_xml(archive, "xl/workbook.xml")
    names = set(archive.namelist())
    shared = _shared_strings(archive) if "xl/sharedStrings.xml" in names else []
    date_styles = _date_styles(archive) if "xl/styles.xml" in names else frozenset()
    properties = workbook.find("{*}workbookPr")
    date1904 = properties is not None and properties.get("date1904") in ("1", "true")
    epoch = _EPOCH_1904 if date1904 else _EPOCH_1900

    blocks = []
    for sheet_name, path in _sheet_paths(archive, workbook):
        rows = _sheet_rows(_read_xml(archive, path), shared, date_styles, epoch)
        if rows:
            blocks.append("\n".join([f"[시트: {sheet_name}]", *rows]))
    return "\n".join(blocks)


def _read_xml(archive: zipfile.ZipFile, path: str):  # noqa: ANN202
    """ZIP 안의 XML을 첨부 1개 상한까지만 풀어 읽습니다(압축 폭탄 대비)."""
    from lxml import etree

    with archive.open(path) as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("XML 크기 상한 초과")
    return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _sheet_paths(archive: zipfile.ZipFile, workbook) -> list[tuple[str, str]]:  # noqa: ANN001
    """`workbook.xml`의 시트 이름과, 관계 파일로 찾은 시트 XML 경로. 경로가 없는 시트는 뺍니다."""
    rels = _read_xml(archive, "xl/_rels/workbook.xml.rels")
    targets = {r.get("Id"): r.get("Target", "") for r in rels.iter("{*}Relationship")}
    names = set(archive.namelist())
    sheets = []
    for sheet in workbook.iter("{*}sheet"):
        rel_id = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), None)
        target = targets.get(rel_id, "")
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        path = posixpath.normpath(path)
        if path in names:
            sheets.append((sheet.get("name", ""), path))
    return sheets


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """공유 문자열마다 글자(`t`)를 잇습니다. 일본어 후리가나(`rPh`) 글자는 뺍니다."""
    root = _read_xml(archive, "xl/sharedStrings.xml")
    return [_inline_text(item) for item in root.iter("{*}si")]


def _inline_text(element) -> str:  # noqa: ANN001
    return "".join(t.text or "" for t in element.iter("{*}t") if _nearest(t, "rPh") is None)


def _date_styles(archive: zipfile.ZipFile) -> frozenset[int]:
    """날짜·시각 서식을 쓰는 셀 서식 번호(`c`의 `s` 값) 모음."""
    root = _read_xml(archive, "xl/styles.xml")
    custom = {int(f.get("numFmtId", "-1")): f.get("formatCode", "") for f in root.iter("{*}numFmt")}
    cell_formats = root.find("{*}cellXfs")
    if cell_formats is None:
        return frozenset()
    dates = set()
    for index, xf in enumerate(cell_formats.iter("{*}xf")):
        format_id = int(xf.get("numFmtId", "0"))
        if format_id in _BUILTIN_DATE_FORMATS or _is_date_format(custom.get(format_id, "")):
            dates.add(index)
    return frozenset(dates)


def _is_date_format(code: str) -> bool:
    """사용자 서식에 연(y)·일(d)이 있으면 날짜로 봅니다. `m`만 있으면 분일 수 있어 보지 않습니다."""
    bare = _FORMAT_LITERAL.sub("", code).lower()
    return "y" in bare or "d" in bare


def _sheet_rows(
    root,  # noqa: ANN001
    shared: list[str],
    date_styles: frozenset[int],
    epoch: date,
) -> list[str]:
    """시트의 행마다 한 줄. 시트에서 가장 왼쪽에 쓴 열부터 칸 자리를 맞춰 빈 칸도 남깁니다."""
    table: list[dict[int, str]] = []
    for row in root.iter("{*}row"):
        cells: dict[int, str] = {}
        column = 0
        for cell in row.iterfind("{*}c"):
            column = _column_index(cell.get("r", "")) or column + 1
            value = " ".join(_cell_text(cell, shared, date_styles, epoch).split())
            if value:
                cells[column] = value
        if cells:
            table.append(cells)
    if not table:
        return []
    first = min(min(cells) for cells in table)
    lines = []
    for cells in table:
        last = max(cells)
        if last - first + 1 > MAX_XLSX_COLUMNS:
            values = [cells[c] for c in sorted(cells)]  # 열이 너무 넓으면 값만 잇습니다
        else:
            values = [cells.get(c, "") for c in range(first, last + 1)]
        lines.append(" | ".join(values))
    return lines


def _column_index(reference: str) -> int:
    """`C12` → 3. 참조가 없거나 형식이 다르면 0."""
    match = _CELL_COLUMN.match(reference)
    if not match:
        return 0
    index = 0
    for letter in match.group(1):
        index = index * 26 + ord(letter) - ord("A") + 1
    return index


def _cell_text(cell, shared: list[str], date_styles: frozenset[int], epoch: date) -> str:  # noqa: ANN001
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find("{*}is")
        return _inline_text(inline) if inline is not None else ""
    value = cell.find("{*}v")
    raw = (value.text or "") if value is not None else ""
    if not raw:
        return ""
    if kind == "s":
        index = int(raw)
        return shared[index] if 0 <= index < len(shared) else ""
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if kind != "n":  # str(수식 결과 글자)·e(오류)·d(ISO 날짜)는 적힌 그대로
        return raw
    if int(cell.get("s", "0")) in date_styles:
        return _excel_date(raw, epoch)
    return _number(raw)


def _excel_date(raw: str, epoch: date) -> str:
    """엑셀 일련번호 → `YYYY-MM-DD`(시각이 있으면 ` HH:MM`).

    시간대를 붙이지 않는 글자 변환이라 `datetime`을 쓰지 않습니다(날짜 해석은 `parsers/dates.py`).
    시각은 분 단위로 반올림합니다(부동소수점 오차로 17:59:59.99가 되는 것을 막음).
    """
    try:
        serial = float(raw)
        days = math.floor(serial)
        minutes = round((serial - days) * 24 * 60)
        if minutes == 24 * 60:
            days, minutes = days + 1, 0
        day = epoch + timedelta(days=days)
    except (ValueError, OverflowError):
        return raw
    if minutes == 0:
        return day.isoformat()
    return f"{day.isoformat()} {minutes // 60:02d}:{minutes % 60:02d}"


def _number(raw: str) -> str:
    """`4.4999999999999996` 같은 저장 오차를 엑셀 화면처럼 15자리로 정리합니다."""
    try:
        return f"{float(raw):.15g}"
    except ValueError:
        return raw


def _paragraphs(xml: bytes, *, paragraph: str, run: str) -> str:
    """이름공간과 관계없이 로컬 이름으로 문단·글자 요소를 찾아 문단마다 한 줄로 만듭니다.

    표 안의 문단도 문단이므로 칸마다 한 줄이 됩니다. 문단 안에 표가 들어 있으면(HWPX)
    바깥 문단은 바로 아래 글자만 모으고, 안쪽 문단은 따로 한 줄이 됩니다.
    """
    from lxml import etree

    root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    lines = []
    for element in root.iter("{*}" + paragraph):
        texts = [
            t.text or "" for t in element.iter("{*}" + run) if _nearest(t, paragraph) is element
        ]
        lines.append("".join(texts))
    return "\n".join(lines)


def _nearest(element, local_name: str):  # noqa: ANN001, ANN202
    parent = element.getparent()
    while parent is not None and _local(parent) != local_name:
        parent = parent.getparent()
    return parent


def _local(element) -> str:  # noqa: ANN001
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _clean(text: str) -> str:
    lines = (" ".join(_PRIVATE_USE.sub(" ", line).split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _part(
    name: str, kind: FileKind, status: ReadStatus, *, reason: str, **fields
) -> AttachmentPart:  # noqa: ANN003
    log_event(logger, f"첨부 {reason}", logging.WARNING, file_name=name, **fields)
    return AttachmentPart(name=name, kind=kind, status=status)
