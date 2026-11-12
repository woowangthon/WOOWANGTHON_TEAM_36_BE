"""alembic 마이그레이션 head가 하나인지 확인합니다.

두 PR이 같은 부모에서 각각 마이그레이션을 만들면 head가 두 개가 됩니다. 그러면 운영 배포의
`alembic upgrade head`가 "Multiple head revisions"로 실패합니다(2026-11-12 #49·#50).
DB 연결 없이 마이그레이션 파일만 읽어 머지 전에 잡아 냅니다.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def test_alembic_has_single_head():
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"alembic head가 {len(heads)}개입니다: {heads}. "
        '`uv run alembic merge heads -m "..."`로 병합 마이그레이션을 추가하세요.'
    )
