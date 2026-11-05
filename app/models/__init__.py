"""SQLAlchemy 모델 (계획서 6장). 바꾸면 Alembic 마이그레이션을 함께 만듭니다."""

from app.models.base import Base
from app.models.notice import Notice, Scholarship
from app.models.source import CrawlRun, Source
from app.models.user import User, UserProfile, UserScholarshipState

__all__ = [
    "Base",
    "CrawlRun",
    "Notice",
    "Scholarship",
    "Source",
    "User",
    "UserProfile",
    "UserScholarshipState",
]
