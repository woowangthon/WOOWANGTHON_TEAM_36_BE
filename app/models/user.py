"""USERS, USER_PROFILES, USER_SCHOLARSHIP_STATES (계획서 6장, 4장, 7.3).

- 민감 필드(소득분위, 본인·부모 주소, 특수자격)는 core/crypto.py로
  AES-256-GCM 암호화한 바이트로 저장합니다(4.1).
- 탈퇴(DELETE /auth/me) 시 프로필·관심/신청완료 기록이 함께 지워지도록
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


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100))
    gender: Mapped[str] = mapped_column(String(10))
    birth_year: Mapped[int] = mapped_column(Integer)
    gpa: Mapped[float] = mapped_column(Float)  # 직전학기 평점 (필수)
    score_100: Mapped[float | None] = mapped_column(Float)  # 직전학기 100점 환산
    credits: Mapped[int | None] = mapped_column(Integer)  # 직전학기 이수학점
    gpa_total: Mapped[float | None] = mapped_column(Float)
    score_100_total: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[int] = mapped_column(Integer)  # 1~4
    extra_semester: Mapped[bool] = mapped_column(Boolean)
    enrollment_status: Mapped[str] = mapped_column(String(10))
    department: Mapped[str] = mapped_column(String(20))  # 소프트웨어학부 / 기타
    aibc_member: Mapped[bool] = mapped_column(Boolean)
    enc_income_bracket: Mapped[bytes | None] = mapped_column(LargeBinary)
    enc_self_region: Mapped[bytes | None] = mapped_column(LargeBinary)
    enc_parent_region: Mapped[bytes | None] = mapped_column(LargeBinary)
    enc_special_flags: Mapped[bytes | None] = mapped_column(LargeBinary)
    full_tuition_waiver: Mapped[str | None] = mapped_column(String(10))  # 예 / 아니오 / 모름


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
