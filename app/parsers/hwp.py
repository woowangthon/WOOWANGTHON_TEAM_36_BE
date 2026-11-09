"""HWP 5.0(한글 바이너리 문서) 텍스트 추출 (첨부 기반 조건 추출 설계 D2).

HWP 5.0은 OLE 복합 파일입니다. 본문은 `BodyText/Section0`, `Section1`, … 스트림에 레코드로
들어 있고, `FileHeader`의 속성 비트 0이 켜져 있으면 각 스트림이 raw deflate로 압축돼 있습니다.
문단 텍스트 레코드(`HWPTAG_PARA_TEXT`)의 UTF-16LE 글자만 모으고 컨트롤 문자는 건너뜁니다.
표 안의 문단도 같은 스트림에 차례로 들어 있어 칸마다 한 줄이 됩니다.

- 암호가 걸린 문서와 배포용 문서(본문이 `ViewText`에 암호화돼 있음)는 읽지 않습니다(FAILED).
- `pyhwp`는 AGPL이라 쓰지 않고, BSD 라이선스인 `olefile`로 직접 읽습니다(D2).
- olefile은 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).
- 형식 참고: 한글과컴퓨터 "한글 문서 파일 형식 5.0" 공개 문서.
"""

import io
import re
import struct
import zlib
from typing import Literal

SIGNATURE = b"HWP Document File"
_FLAG_COMPRESSED = 1 << 0
_FLAG_PASSWORD = 1 << 1
_FLAG_DISTRIBUTION = 1 << 2

HWPTAG_BEGIN = 0x10
HWPTAG_PARA_HEADER = HWPTAG_BEGIN + 50
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51

# 문단 텍스트 안의 컨트롤 문자(0~31). 글자 컨트롤은 1글자, 인라인·확장 컨트롤은 8글자(16바이트)를
# 차지합니다. 9(탭)는 인라인 컨트롤이지만 공백으로 바꾸고, 10(줄바꿈)·13(문단 끝)은 줄을 나눕니다.
_CHAR_CONTROLS = frozenset({0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31})
_SECTION = re.compile(r"^BodyText/Section(\d+)$")

HwpStatus = Literal["OK", "NO_TEXT", "FAILED"]


class HwpError(ValueError):
    """HWP를 읽을 수 없습니다. 메시지는 이유만 담고 문서 내용은 담지 않습니다."""


def is_hwp(data: bytes) -> bool:
    """OLE 파일이고 `FileHeader` 스트림이 HWP 시그니처로 시작하면 HWP 5.0입니다."""
    import olefile

    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            return ole.exists("FileHeader") and ole.openstream("FileHeader").read(32).startswith(
                SIGNATURE
            )
    except Exception:  # olefile은 깨진 파일에 여러 종류의 예외를 던집니다.
        return False


def hwp_text(data: bytes) -> str:
    """HWP 5.0 본문 텍스트. 읽을 수 없으면 `HwpError`."""
    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as error:
        raise HwpError(f"OLE 파일을 열지 못함({type(error).__name__})") from None
    with ole:
        header = ole.openstream("FileHeader").read()
        if not header.startswith(SIGNATURE) or len(header) < 40:
            raise HwpError("HWP 5.0 헤더가 아님")
        (flags,) = struct.unpack_from("<I", header, 36)
        if flags & _FLAG_PASSWORD:
            raise HwpError("암호가 걸린 문서")
        if flags & _FLAG_DISTRIBUTION:
            raise HwpError("배포용 문서")
        sections = sorted(
            (int(m.group(1)), path)
            for path in ("/".join(entry) for entry in ole.listdir())
            if (m := _SECTION.match(path))
        )
        if not sections:
            raise HwpError("본문 스트림이 없음")
        paragraphs: list[str] = []
        for _, path in sections:
            stream = ole.openstream(path).read()
            if flags & _FLAG_COMPRESSED:
                try:
                    stream = zlib.decompress(stream, -15)
                except zlib.error:
                    raise HwpError("본문 압축을 풀지 못함") from None
            paragraphs.extend(_section_paragraphs(stream))
    return "\n".join(paragraphs)


def _section_paragraphs(stream: bytes) -> list[str]:
    paragraphs: list[str] = []
    for tag, payload in _records(stream):
        if tag == HWPTAG_PARA_TEXT:
            paragraphs.append(_para_text(payload))
    return paragraphs


def _records(stream: bytes):  # noqa: ANN202
    """레코드 헤더(4바이트): 태그 10비트, 레벨 10비트, 크기 12비트. 크기가 0xFFF면 다음 4바이트."""
    offset = 0
    end = len(stream)
    while offset + 4 <= end:
        (header,) = struct.unpack_from("<I", stream, offset)
        offset += 4
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if offset + 4 > end:
                raise HwpError("레코드 크기를 읽지 못함")
            (size,) = struct.unpack_from("<I", stream, offset)
            offset += 4
        if offset + size > end:
            raise HwpError("레코드가 스트림 끝을 넘음")
        yield tag, stream[offset : offset + size]
        offset += size


def _para_text(payload: bytes) -> str:
    chars: list[str] = []
    count = len(payload) // 2
    i = 0
    while i < count:
        (code,) = struct.unpack_from("<H", payload, i * 2)
        if code >= 32:
            chars.append(chr(code))
            i += 1
        elif code in _CHAR_CONTROLS:
            if code == 10:
                chars.append("\n")
            i += 1
        else:  # 인라인·확장 컨트롤: 8글자 차지(표·그림 등은 내용이 다른 레코드에 있음)
            if code == 9:
                chars.append(" ")
            i += 8
    return "".join(chars)


def read_hwp(data: bytes) -> tuple[HwpStatus, str, str]:
    """(상태, 텍스트, 실패 이유). 텍스트 정리는 호출하는 쪽에서 합니다."""
    try:
        text = hwp_text(data)
    except HwpError as error:
        return "FAILED", "", str(error)
    except Exception as error:  # 예상 못 한 손상
        return "FAILED", "", f"HWP를 읽지 못함({type(error).__name__})"
    return ("OK" if text.strip() else "NO_TEXT"), text, ""
