"""RabbitMQ Worker — 요청 큐에서 메시지를 받아 LangGraph agent 로 처리한다.

흐름:
  Spring(WAS) --[agent.requests 큐]--> worker --(agent 처리)--> [props.reply_to 큐] --> Spring --> WebSocket

설계 포인트(세 가지 안전장치):
- 응답 라우팅: 응답을 메시지의 reply_to(요청을 보낸 Spring 인스턴스 전용 큐) + correlation_id 로
  돌려보내, "그 사용자의 WS 가 붙은 인스턴스"에만 도착하게 한다.
- 메시지 신뢰성: prefetch=1, manual ack. 처리 실패해도 에러 응답을 돌려준 뒤 ack 하고,
  파싱조차 안 되는 독성 메시지는 nack(requeue=False) 로 DLQ(agent.requests.dlq)로 보낸다.
- 히스토리 영속화: 세션 히스토리는 프로세스 메모리가 아닌 PostgreSQL(history.py)에서 로드/저장.
"""

import json
import re
import uuid

import pika
from langchain_core.messages import AIMessage, HumanMessage

from agent import build_agent
from config import settings
from history import clear_history, load_history, save_history
from tools import TOOLS
# web.py 에 있던 유니코드 방어 로직을 그대로 재사용
from main import _sanitize_messages, _strip_surrogates

# 응답에 새어나오면 안 되는 tool/함수 이름(코드와 항상 동기화, 긴 이름부터 매칭)
_TOOL_NAMES = sorted((t.name for t in TOOLS), key=len, reverse=True)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_BRACE_RE = re.compile(r"\{[^{}]*\}")
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]")
# 함수명 뒤에 흔히 붙는 한국어 조사까지 함께 제거(잔여 어색함 최소화)
_JOSA = "(을|를|이|가|은|는|로|으로|에|에서|와|과|의)?"


def _strip_json_blocks(text: str) -> str:
    """문장 안에 박힌 JSON 객체/배열 원문만 인라인으로 제거한다(중첩 포함, 일반 문장은 보존)."""
    prev = None
    while prev != text:  # 가장 안쪽 {...} 부터 반복 제거 → 중첩 JSON 처리
        prev = text
        text = _BRACE_RE.sub(
            lambda m: "" if ('"' in m.group() or ":" in m.group()) else m.group(), text
        )
    prev = None
    while prev != text:  # 따옴표가 든 [...] 배열 제거(일반 대괄호는 보존)
        prev = text
        text = _BRACKET_RE.sub(lambda m: "" if '"' in m.group() else m.group(), text)
    return text


def _sanitize_reply(text: str) -> str:
    """LLM 최종 응답에서 함수명·JSON 원문 노출을 결정적으로 제거한다.

    프롬프트만으로는 소형 모델이 반복적으로 흘리므로, 사용자에게 보내기 직전 한 번 더 거른다.
    """
    if not text:
        return text
    text = _FENCE_RE.sub("", text)              # 코드펜스 블록 제거
    text = _strip_json_blocks(text)             # 인라인 JSON 제거(프로즈 보존)
    text = _INLINE_CODE_RE.sub("", text)        # 인라인 백틱 코드 제거
    for name in _TOOL_NAMES:                    # 함수명(+조사) 제거
        text = re.sub(rf"\s*\b{re.escape(name)}\b\s*{_JOSA}\s*", " ", text)
    # 순수 구조 줄/빈 줄/공백 정리
    text = "\n".join(ln for ln in text.splitlines() if ln.strip() not in "{}[]")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# DLX (agent.dlx) = 죽은 메시지를 받는 교환기(라우터). "거부된 메시지는 여기로 보내라"의 목적지 주소 역할.
# DLQ (agent.requests.dlq) = 그 메시지가 실제로 쌓이는 큐(저장소).

DLX = "agent.dlx"
DLQ = "agent.requests.dlq"

# agent 는 무거우므로 프로세스 시작 시 1회만 생성
_agent = build_agent()


def _declare_topology(channel) -> None:
    """요청 큐 + DLX/DLQ 선언. Spring 쪽 큐 선언과 인자가 일치해야 한다."""
    channel.exchange_declare(exchange=DLX, exchange_type="fanout", durable=True)
    channel.queue_declare(queue=DLQ, durable=True)
    channel.queue_bind(queue=DLQ, exchange=DLX)
    channel.queue_declare(
        queue=settings.request_queue,
        durable=True,
        arguments={"x-dead-letter-exchange": DLX},
    )


def _handle(data: dict) -> dict:
    """메시지 한 건을 처리하고 응답 본문(dict)을 만든다."""
    msg_type = data.get("type", "chat")
    session_id = data.get("session_id") or uuid.uuid4().hex

    if msg_type == "reset":
        clear_history(session_id)
        return {"ok": True, "session_id": session_id, "reply": "대화를 초기화했습니다."}

    message = (data.get("message") or "").strip()
    if not message:
        return {"ok": False, "session_id": session_id, "error": "메시지가 비어 있습니다."}

    messages = load_history(session_id)
    messages.append(HumanMessage(content=_strip_surrogates(message)))
    messages = _sanitize_messages(messages)

    try:
        result = _agent.invoke({"messages": messages})
    except UnicodeEncodeError:
        messages = _sanitize_messages(messages)
        try:
            result = _agent.invoke({"messages": messages})
        except UnicodeEncodeError:
            return {
                "ok": False,
                "session_id": session_id,
                "error": "이전 대화에 인코딩할 수 없는 문자가 섞여 있어 요청을 처리하지 못했습니다.",
            }

    messages = _sanitize_messages(result["messages"])
    save_history(session_id, messages)

    last = messages[-1]
    raw_reply = last.content if isinstance(last, AIMessage) else ""
    reply = _sanitize_reply(raw_reply)
    # 필터로 내용이 모두 사라지면(예: 응답이 통째로 JSON이었던 경우) 중립 문구로 대체
    if raw_reply and not reply:
        reply = "요청을 처리했습니다. 추가로 도와드릴 내용이 있을까요?"
    return {"ok": True, "session_id": session_id, "reply": reply}


def _on_message(channel, method, props, body: bytes) -> None:
    # 1) 파싱: 실패하면 응답을 돌려줄 수도 없으니 DLQ 로 보낸다.
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # 2) 처리: 어떤 오류든 에러 응답으로 변환해 사용자에게 전달(무한 재전송 방지)
    try:
        reply_body = _handle(data)
    except Exception as exc:  # noqa: BLE001
        reply_body = {
            "ok": False,
            "session_id": data.get("session_id"),
            "error": f"처리 중 오류: {type(exc).__name__}: {exc}",
        }

    # 3) 응답 라우팅: reply_to 가 있으면 그 인스턴스 전용 큐로 correlation_id 와 함께 회신
    if props.reply_to:
        channel.basic_publish(
            exchange="",
            routing_key=props.reply_to,
            properties=pika.BasicProperties(correlation_id=props.correlation_id),
            body=json.dumps(reply_body, ensure_ascii=False).encode("utf-8"),
        )

    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    print(f"agent worker 시작 (model={settings.ollama_model}) — 큐: {settings.request_queue}")
    params = pika.URLParameters(settings.rabbitmq_url)
    # LLM 추론이 길어 하트비트가 끊기지 않도록 비활성화(데모 단순화)
    params.heartbeat = 0
    params.blocked_connection_timeout = 300

    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    _declare_topology(channel)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=settings.request_queue, on_message_callback=_on_message)

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
