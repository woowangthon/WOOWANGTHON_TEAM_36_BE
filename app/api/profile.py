"""프로필 조회·수정 (계획서 4.1, 4.2, 7.2).

- 개인정보 동의 전에는 403(`ConsentedUser`, 7.3).
- 소득분위·본인/부모 주소·특수자격은 AES-256-GCM으로 암호화해 저장하고, 응답에서는 본인에게만
  복호화해 돌려줍니다. 암호문은 `field_aad(user_id, 필드)`로 행·필드에 묶습니다.
- 특수자격은 민감정보 동의가 있어야 저장합니다(4.2). 동의 없이 값을 보내면 400, 빈 목록이면
  `null`(알 수 없음)로 저장합니다(4.3).
- 입력 규칙은 저장(PUT)에만 두고, 조회와 판정은 저장된 값을 그대로 읽습니다(4.3).
- 전환 기간(배포 ①~③): 옛 화면이 보내는 학년·초과학기를 이수학기로 바꿔 저장하고, 옛 서버로
  되돌릴 수 있게 옛 칸(grade·extra_semester·department)도 함께 씁니다. 배포 ③에서 지웁니다.
- 프로필 값은 로그에 남기지 않습니다(4.2).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_json, encrypt_json, field_aad
from app.core.db import get_db
from app.core.security import ConsentedUser
from app.models import UserProfile
from app.schemas.api.profile import (
    Profile,
    ProfileUpdate,
    Region,
    grade_of,
    is_extra_semester,
    semesters_from_grade,
)

router = APIRouter(prefix="/profile", tags=["profile"])

DB = Annotated[Session, Depends(get_db)]
AUTH_ERRORS = {401: {"description": "로그인 필요"}, 403: {"description": "개인정보 동의 필요"}}

PLAIN_FIELDS = (
    "name",
    "gender",
    "birth_year",
    "completed_semesters",
    "enrollment_status",
    "aibc_member",
    "gpa",
    "score_100",
    "credits",
    "gpa_total",
    "score_100_total",
    "full_tuition_waiver",
)
# 옛 DB 값 → 새 선택지. 마이그레이션 ①이 바꾸지만, 배포 중 몇 분 동안 옛 서버가 다시 쓸 수 있어
# 조회가 실패하지 않게 읽을 때도 바꿉니다.
LEGACY_ENROLLMENT = {"수료": "재학", "졸업유예": "재학"}
# (Profile 필드, DB 컬럼, aad에 쓰는 이름)
ENCRYPTED_FIELDS = (
    ("income_bracket", "enc_income_bracket", "income_bracket"),
    ("self_region", "enc_self_region", "self_region"),
    ("parent_region", "enc_parent_region", "parent_region"),
    ("special_flags", "enc_special_flags", "special_flags"),
)


def _seal(user_id: int, name: str, value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, Region):
        value = value.model_dump()
    return encrypt_json(value, aad=field_aad(user_id, name))


def _open(user_id: int, name: str, data: bytes | None) -> Any:
    return None if data is None else decrypt_json(data, aad=field_aad(user_id, name))


def profile_from_row(row: UserProfile) -> Profile:
    values = {field: getattr(row, field) for field in PLAIN_FIELDS}
    if values["completed_semesters"] is None:  # 옛 서버가 쓴 행
        values["completed_semesters"] = semesters_from_grade(row.grade, row.extra_semester)
    status_ = row.enrollment_status
    values["enrollment_status"] = LEGACY_ENROLLMENT.get(status_, status_)
    if values["full_tuition_waiver"] == "모름":
        values["full_tuition_waiver"] = None
    for field, column, aad_name in ENCRYPTED_FIELDS:
        values[field] = _open(row.user_id, aad_name, getattr(row, column))
    return Profile.model_validate(values)


@router.get("", response_model=Profile, responses={**AUTH_ERRORS, 404: {"description": "미입력"}})
def get_profile(user: ConsentedUser, db: DB) -> Profile:
    """본인 프로필. 아직 입력하지 않았으면 404(프론트는 프로필 입력 화면으로 이동)."""
    row = db.get(UserProfile, user.id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="프로필을 입력하세요")
    return profile_from_row(row)


@router.put(
    "",
    response_model=Profile,
    responses={**AUTH_ERRORS, 400: {"description": "민감정보 동의 없음"}},
)
def put_profile(body: ProfileUpdate, user: ConsentedUser, db: DB) -> Profile:
    """프로필 전체를 저장합니다(처음 입력과 수정 모두). 선택 항목의 null은 '입력 안 함'입니다."""
    if user.sensitive_agreed_at is None:
        if body.special_flags:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="특수자격을 입력하려면 민감정보 수집·이용에 동의해야 합니다",
            )
        body.special_flags = None  # 동의 없이 보낸 빈 목록은 '해당 없음'이 아니라 '모름'
    row = db.get(UserProfile, user.id)
    if row is None:
        row = UserProfile(user_id=user.id)
        db.add(row)
    for field in PLAIN_FIELDS:
        setattr(row, field, getattr(body, field))
    semesters = body.completed_semesters
    assert semesters is not None  # ProfileUpdate가 채움
    # 옛 칸 (배포 ③에서 지움): 옛 서버로 되돌려도 읽을 수 있게 함께 씁니다.
    row.grade = grade_of(semesters)
    row.extra_semester = is_extra_semester(semesters)
    row.department = "소프트웨어학부"
    for field, column, aad_name in ENCRYPTED_FIELDS:
        setattr(row, column, _seal(user.id, aad_name, getattr(body, field)))
    db.commit()
    return profile_from_row(row)
