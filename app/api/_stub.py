"""T06: 엔드포인트를 DTO와 함께 등록만 하고 본문은 501로 둡니다. 각 기능 카드에서 채웁니다."""

from typing import NoReturn

from fastapi import HTTPException, status

NOT_IMPLEMENTED = {501: {"description": "아직 구현되지 않음"}}


def not_implemented() -> NoReturn:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Not Implemented")
