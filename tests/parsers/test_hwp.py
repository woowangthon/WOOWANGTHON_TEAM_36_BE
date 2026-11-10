"""HWP 5.0 텍스트 추출 테스트.

실제 학부 공지 첨부(천사장학금 선발 계획)와 직접 만든 레코드를 씁니다.
"""

import shutil
import struct
import zlib
from pathlib import Path

import olefile
import pytest

from app.parsers import hwp
from app.parsers.attachments import read_attachment
from app.parsers.hwp import HWPTAG_PARA_TEXT, HwpError, hwp_text, is_hwp, read_hwp

PLAN = Path(__file__).parents[1] / "fixtures" / "sw_dept" / "hwp" / "1154590_0.hwp"


def record(tag: int, payload: bytes) -> bytes:
    size = len(payload)
    if size >= 0xFFF:
        return struct.pack("<I", tag | (0xFFF << 20)) + struct.pack("<I", size) + payload
    return struct.pack("<I", tag | (size << 20)) + payload


def utf16(*codes: int | str) -> bytes:
    out = b""
    for code in codes:
        out += code.encode("utf-16-le") if isinstance(code, str) else struct.pack("<H", code)
    return out


def with_flags(tmp_path: Path, flags: int) -> bytes:
    """실제 HWP의 FileHeader 속성 비트만 바꾼 사본(스트림 크기는 그대로)."""
    path = tmp_path / "copy.hwp"
    shutil.copy(PLAN, path)
    with olefile.OleFileIO(str(path), write_mode=True) as ole:
        header = bytearray(ole.openstream("FileHeader").read())
        struct.pack_into("<I", header, 36, struct.unpack_from("<I", header, 36)[0] | flags)
        ole.write_stream("FileHeader", bytes(header))
    return path.read_bytes()


# --- 실제 문서 ---


def test_real_hwp_criteria_text():
    data = PLAN.read_bytes()
    assert is_hwp(data)
    text = hwp_text(data)
    assert "2026학년도 2학기 천사장학생 선발 계획" in text
    assert "정규학기에 재학 중인 학부생" in text  # 선발대상(자격 조건)
    assert "징계 등 처분 이력이 없는 학생" in text


def test_real_hwp_through_attachment_reader():
    [part] = read_attachment(PLAN.read_bytes(), name="선발 계획.hwp")
    assert (part.kind, part.status) == ("HWP", "OK")
    lines = part.text.splitlines()
    assert "◦ 정규학기에 재학 중인 학부생" in lines
    assert all(line == line.strip() and line for line in lines)  # 정리된 줄


@pytest.mark.parametrize(("flag", "reason"), [(1 << 1, "암호"), (1 << 2, "배포용")])
def test_password_and_distribution_documents_fail(tmp_path, flag, reason):
    status, text, why = read_hwp(with_flags(tmp_path, flag))
    assert (status, text) == ("FAILED", "")
    assert reason in why


def test_not_hwp():
    assert not is_hwp(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    assert read_hwp(b"not ole")[0] == "FAILED"


# --- 레코드·컨트롤 문자 ---


def test_para_text_skips_controls():
    payload = utf16(
        "평점",
        9,
        *[0] * 7,  # 탭(인라인 컨트롤, 8글자) → 공백
        "3.0",
        11,
        *[0] * 7,  # 표·그림 등 확장 컨트롤(8글자) → 건너뜀
        " 이상",
        10,  # 줄바꿈
        "소득",
        13,  # 문단 끝
    )
    assert hwp._para_text(payload) == "평점 3.0 이상\n소득"


def test_records_with_extended_size():
    long_text = "가" * 3000  # 6000바이트 > 0xFFF
    stream = record(HWPTAG_PARA_TEXT, utf16(long_text, 13)) + record(0x10, b"\0" * 4)
    assert list(hwp._section_paragraphs(stream)) == [long_text]


def test_truncated_record_raises():
    stream = record(HWPTAG_PARA_TEXT, utf16("평점"))[:-1]
    with pytest.raises(HwpError):
        list(hwp._records(stream))


def test_compressed_section_is_raw_deflate():
    compressor = zlib.compressobj(wbits=-15)
    body = record(HWPTAG_PARA_TEXT, utf16("재학생", 13))
    raw = compressor.compress(body) + compressor.flush()
    assert hwp._section_paragraphs(zlib.decompress(raw, -15)) == ["재학생"]
