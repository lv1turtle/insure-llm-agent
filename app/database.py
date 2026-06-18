"""PostgreSQL 접근 헬퍼. 모든 쿼리는 파라미터 바인딩을 사용합니다."""

from typing import Any

from sqlalchemy import create_engine, text

from config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def fetch_all(sql: str, **params: Any) -> list[dict]:
    """SELECT 결과를 dict 리스트로 반환."""
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        return [dict(row) for row in rows]


def fetch_one(sql: str, **params: Any) -> dict | None:
    rows = fetch_all(sql, **params)
    return rows[0] if rows else None


def execute(sql: str, **params: Any) -> dict | None:
    """INSERT/UPDATE 실행. RETURNING 절이 있으면 첫 행을 반환."""
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        if result.returns_rows:
            row = result.mappings().first()
            return dict(row) if row else None
        return None
