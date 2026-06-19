"""대화 히스토리 영속화 — session_id 별 LangChain 메시지를 PostgreSQL에 저장/복원.

worker 를 무상태로 유지하기 위해 히스토리를 프로세스 메모리가 아닌 DB(conversations 테이블)에 둔다.
LangChain 메시지는 tool 호출/결과까지 포함되므로 messages_to_dict/_from_dict 로 충실히 직렬화한다.
"""

import json
from typing import Any

from langchain_core.messages import messages_from_dict, messages_to_dict
from sqlalchemy import text

from database import engine


def load_history(session_id: str) -> list:
    """세션의 전체 메시지를 seq 순서대로 복원한다. 없으면 빈 리스트."""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT message FROM conversations "
                    "WHERE session_id = :sid ORDER BY seq"
                ),
                {"sid": session_id},
            )
            .mappings()
            .all()
        )
    if not rows:
        return []
    # JSONB 컬럼은 드라이버에 따라 dict 또는 str 로 올 수 있어 모두 처리
    dicts: list[dict[str, Any]] = []
    for row in rows:
        msg = row["message"]
        dicts.append(json.loads(msg) if isinstance(msg, str) else msg)
    return messages_from_dict(dicts)


def save_history(session_id: str, messages: list) -> None:
    """세션의 메시지를 통째로 덮어쓴다(한 트랜잭션).

    agent.invoke 는 기존 히스토리 + 신규 메시지를 함께 돌려주므로
    delete 후 전체 재삽입이 가장 단순하고 정확하다.
    """
    serialized = messages_to_dict(messages)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM conversations WHERE session_id = :sid"),
            {"sid": session_id},
        )
        for seq, item in enumerate(serialized):
            conn.execute(
                text(
                    "INSERT INTO conversations (session_id, seq, message) "
                    "VALUES (:sid, :seq, :msg)"
                ),
                {"sid": session_id, "seq": seq, "msg": json.dumps(item, ensure_ascii=False)},
            )


def clear_history(session_id: str) -> None:
    """세션의 대화 히스토리를 모두 삭제(대화 초기화)."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM conversations WHERE session_id = :sid"),
            {"sid": session_id},
        )
