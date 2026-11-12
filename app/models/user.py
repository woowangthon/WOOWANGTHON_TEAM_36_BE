"""USERS, USER_PROFILES, USER_SCHOLARSHIP_STATES, USER_CONDITION_ANSWERS (계획서 6장, 4장, 7.3).

- 민감 필드(소득분위, 본인·부모 주소, 특수자격)는 core/crypto.py로
  AES-256-GCM 암호화한 바이트로 저장합니다(4.1).
- 자격을 좁히는 조건에 사용자가 답한 값도 같은 방식으로 암호화합니다(4.2, 5.3).
- 탈퇴(DELETE /auth/me) 시 프로필·관심/신청완료 기록·조건 답이 함께 지워지도록
  ON DELETE CASCADE를 겁니다(4.2).
- 추출 모듈(app/extraction)은 이 파일을 import하지 않습니다(8.1 개인정보 경계).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, in_values

USER_SCHOLARSHIP_STATES = ("BOOKMARKED", "APPLIED")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # Google 전용 계정은 NULL
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)  # Google 계정 ID
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    privacy_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sensitive_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 장학금 알림 메일 수신(7.4). 기본 켬, 수신 거부 링크나 내 정보 화면에서 끔
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100))  # 닉네임. 배포 ③에서 nullable로 바꾸고 실명 삭제
    gender: Mapped[str] = mapped_column(String(10))
    birth_year: Mapped[int] = mapped_column(Integer)
    gpa: Mapped[float | None] = mapped_column(Float)  # 직전학기 평점. 이수학기 0이면 null
    score_100: Mapped[float | None] = mapped_column(Float)  # 직전학기 100점 환산
    credits: Mapped[int | None] = mapped_column(Integer)  # 직전학기 이수학점
    gpa_total: Mapped[float | None] = mapped_column(Float)
    score_100_total: Mapped[float | None] = mapped_column(Float)
    # 마친 정규학기 수 0~16. 학년·초과학기는 여기서 계산(4.1). 배포 ④에서 NOT NULL
    completed_semesters: Mapped[int | None] = mapped_column(Integer)
    # 옛 칸: 전환 기간에는 이수학기에서 계산해 함께 저장하고, 배포 ④에서 지웁니다.
    grade: Mapped[int] = mapped_column(Integer)  # 1~4
    extra_semester: Mapped[bool] = mapped_column(Boolean)
    enrollment_status: Mapped[str] = mapped_column(String(10))  # 재학 / 휴학
    department: Mapped[str] = mapped_column(String(20))  # 옛 칸. 배포 ④에서 지움
    aibc_member: Mapped[bool | None] = mapped_column(Boolean)  # null = 모름
    # 편입생이면 completed_semesters는 편입 후 마친 학기 수 (T39)
    is_transfer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_foreign: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enc_income_bracket: Mapped[bytes | None] = mapped_column(LargeBinary)
    enc_self_region: Mapped[bytes | None] = mapped_column(LargeBinary)
    enc_parent_region: Mapped[bytes | None] = mapped_column(LargeBinary)
    enc_special_flags: Mapped[bytes | None] = mapped_column(LargeBinary)
    full_tuition_waiver: Mapped[str | None] = mapped_column(String(10))  # 예 / 아니오 / null(모름)


class UserScholarshipState(Base):
    __tablename__ = "user_scholarship_states"
    __table_args__ = (CheckConstraint(in_values("state", USER_SCHOLARSHIP_STATES), name="state"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scholarship_id: Mapped[int] = mapped_column(
        ForeignKey("scholarships.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(12))


class UserConditionAnswer(Base):
    """자격을 좁히는 조건(`extra_conditions`) 1줄에 대한 사용자 답 (5.3, 2026-11-12).

    `condition_key`는 문장 정규화 후 SHA-256 앞 16자(`matching.engine.condition_key`)입니다.
    답(YES/NO)은 조건 문장에 민감한 내용이 있을 수 있어 암호화합니다(4.2).
    """

    __tablename__ = "user_condition_answers"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scholarship_id: Mapped[int] = mapped_column(
        ForeignKey("scholarships.id", ondelete="CASCADE"), primary_key=True
    )
    condition_key: Mapped[str] = mapped_column(String(16), primary_key=True)
    enc_answer: Mapped[bytes] = mapped_column(LargeBinary)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
