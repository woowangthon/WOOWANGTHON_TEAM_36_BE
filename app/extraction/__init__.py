"""AI 추출 모듈 (계획서 8.1).

입력은 공지 텍스트(ExtractRequest)뿐이며 사용자 관련 모듈을 import하지 않습니다.
"""

from app.extraction.extractor import ExtractionError, extract
from app.extraction.schemas import (
    ExtractedCriteria,
    ExtractionResult,
    ExtractRequest,
    ScholarshipCriteria,
)

__all__ = [
    "ExtractRequest",
    "ExtractedCriteria",
    "ExtractionError",
    "ExtractionResult",
    "ScholarshipCriteria",
    "extract",
]
