"""첨부파일 텍스트 추출: 형식 판별 + 형식별 추출 (첨부 기반 조건 추출 설계 2단계).

- 형식은 확장자가 아니라 **파일 앞부분 시그니처**로 고릅니다. 학부 게시판은 세션이 없으면
  200 응답으로 HTML 오류 페이지를 주고, 확장자와 실제 형식이 다른 파일도 있습니다.
- 지원: PDF(`parsers/pdf.py`), HWP 5.0(`parsers/hwp.py`), HWPX·DOCX(ZIP 안의 XML),
  ZIP(안의 파일을 한 단계만 다시 판별). 이미지·스캔본은 읽지 않습니다(D3).
- ZIP은 **안의 파일마다 결과를 하나씩** 돌려줍니다. "ZIP 안의 HWP를 못 읽음"이 상태에 남아야
  판정에서 "확인 필요"로 낮출 수 있기 때문입니다(D8).
- 압축 폭탄을 막으려고 안의 파일 수·파일 크기·전체 크기에 상한을 두고, ZIP 안의 ZIP은 풀지 않습니다.
- lxml은 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).
- 로그에는 파일 이름과 이유만 남기고 내용은 남기지 않습니다.
"""

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from typing import Literal

from app.core.logging import log_event
from app.parsers.hwp import is_hwp, read_hwp
from app.parsers.pdf import is_pdf, read_pdf

logger = logging.getLogger(__name__)

FileKind = Literal["PDF", "HWP", "HWPX", "DOCX", "IMAGE", "ZIP", "OTHER"]
ReadStatus = Literal["OK", "NO_TEXT", "UNSUPPORTED", "FAILED", "TOO_LARGE"]

MAX_FILE_BYTES = 20 * 1024 * 1024  # 첨부 1개, ZIP 안의 파일 1개
MAX_ZIP_MEMBERS = 30
MAX_ZIP_TOTAL_BYTES = 50 * 1024 * 1024  # ZIP 하나를 풀었을 때 합계

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # HWP 5.0, 옛 DOC·XLS
_ZIP = b"PK\x03\x04"
_IMAGES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")
_SECTION = re.compile(r"^Contents/section(\d+)\.xml$")
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
