"""422 입력 오류 응답 (정의서 18-2).

FastAPI 기본 422는 영어 메시지에 사용자가 보낸 값(`input`)까지 돌려줍니다. 가입 요청이면 비밀번호가
그대로 응답에 실립니다. 여기서는 모양(`detail: [{loc, msg, type}]`)은 그대로 두고
- `msg`를 한국어로 바꾸고 (`ValueError` 문장은 "Value error, " 머리말만 떼고 그대로)
- `input`·`ctx`는 빼서
화면이 칸별로 보여 줄 수 있게 합니다. 값은 로그에도 남기지 않습니다(4.2).
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# pydantic 오류 type → 한국어. 없는 type은 DEFAULT_MESSAGE.
MESSAGES = {
    "missing": "입력하세요",
    "extra_forbidden": "알 수 없는 항목이에요",
    "literal_error": "목록에 있는 값을 고르세요",
    "enum": "목록에 있는 값을 고르세요",
    "bool_type": "예·아니오 중에서 고르세요",
    "bool_parsing": "예·아니오 중에서 고르세요",
    "int_type": "정수로 입력하세요",
    "int_parsing": "정수로 입력하세요",
    "int_from_float": "정수로 입력하세요",
    "float_type": "숫자로 입력하세요",
    "float_parsing": "숫자로 입력하세요",
    "finite_number": "숫자로 입력하세요",
    "string_type": "글자로 입력하세요",
    "string_too_short": "너무 짧아요",
    "string_too_long": "너무 길어요",
    "greater_than_equal": "범위를 확인하세요",
    "less_than_equal": "범위를 확인하세요",
    "list_type": "목록 형식이 아니에요",
    "model_type": "형식이 맞지 않아요",
    "model_attributes_type": "형식이 맞지 않아요",
    "dict_type": "형식이 맞지 않아요",
    "json_invalid": "요청 형식이 맞지 않아요",
}
DEFAULT_MESSAGE = "입력값을 확인하세요"


def korean_message(error: dict[str, Any]) -> str:
    if error.get("type") == "value_error":  # 스키마의 ValueError: 이미 한국어 문장
        return str(error.get("ctx", {}).get("error") or DEFAULT_MESSAGE)
    return MESSAGES.get(str(error.get("type")), DEFAULT_MESSAGE)


def clean_errors(errors: Any) -> list[dict[str, Any]]:
    return [
        {"loc": list(e.get("loc", ())), "msg": korean_message(e), "type": e.get("type")}
        for e in errors
    ]


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder({"detail": clean_errors(exc.errors())}),
    )


def install_validation_errors(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
