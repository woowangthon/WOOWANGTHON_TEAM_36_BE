"""SQLAlchemy 선언 기반 (계획서 6장).

제약 이름 규칙을 고정해 autogenerate 마이그레이션의 이름이 환경마다 달라지지 않게 합니다.
시각 컬럼은 모두 timestamptz(DateTime(timezone=True))입니다.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def in_values(column: str, values: tuple[str, ...]) -> str:
    """CHECK 제약용 `column IN (...)` 식을 만듭니다."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"
