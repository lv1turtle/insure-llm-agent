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
import uuid

import pika
from langchain_core.messages import AIMessage, HumanMessage

from agent import build_agent
from config import settings
from history import clear_history, load_history, save_history
# web.py 에 있던 유니코드 방어 로직을 그대로 재사용
from main import _sanitize_messages, _strip_surrogates

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
    reply = last.content if isinstance(last, AIMessage) else ""
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
