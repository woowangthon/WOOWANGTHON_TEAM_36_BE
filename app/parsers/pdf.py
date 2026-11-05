"""PDF 텍스트 추출 (계획서 2장, 3장 수집 행: PyMuPDF).

- 암호가 걸렸거나 깨진 PDF, PDF가 아닌 응답, 텍스트 없는 스캔본은 **빈 문자열**을 돌려주고
  경고 로그를 남깁니다.
  수집은 멈추지 않고 본문만으로 추출을 계속합니다.
- 학부 게시판 첨부는 세션 쿠키·Referer가 없으면 200 응답으로 "잘못된 요청" HTML이 오므로
  (`tests/fixtures/README.md`), `%PDF` 시그니처로 PDF인지 먼저 확인합니다.
- PyMuPDF는 학생 요청 경로에서 불러오지 않도록 함수 안에서 import합니다(9.5).
- 로그에는 파일 이름과 이유만 남기고 내용은 남기지 않습니다.
"""

import logging

from app.core.logging import log_event

logger = logging.getLogger(__name__)

PDF_SIGNATURE = b"%PDF-"
_SIGNATURE_SEARCH_BYTES = 1024  # 일부 PDF는 시그니처 앞에 쓰레기 바이트가 있음


def is_pdf(data: bytes) -> bool:
    return PDF_SIGNATURE in data[:_SIGNATURE_SEARCH_BYTES]


def extract_text(data: bytes, *, name: str = "") -> str:
    """PDF 바이트에서 텍스트를 쪽 순서대로 뽑습니다. 실패하면 빈 문자열."""
    if not is_pdf(data):
        log_event(logger, "PDF가 아닌 첨부", logging.WARNING, file_name=name, size=len(data))
        return ""

    import pymupdf

    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                log_event(logger, "암호가 걸린 PDF", logging.WARNING, file_name=name)
                return ""
            pages = [page.get_text("text") for page in document]
    except Exception as error:  # PyMuPDF는 깨진 파일에 여러 종류의 예외를 던집니다.
        log_event(
            logger, "PDF를 읽지 못함", logging.WARNING, file_name=name, error=type(error).__name__
        )
        return ""
    text = _clean("\n".join(pages))
    if not text:
        # 스캔 이미지만 있는 PDF. OCR은 하지 않습니다(계획서 3장에 없음).
        log_event(logger, "텍스트가 없는 PDF(스캔본)", logging.WARNING, file_name=name)
    return text


def _clean(text: str) -> str:
    """줄마다 공백을 정리하고 빈 줄을 뺍니다."""
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)
