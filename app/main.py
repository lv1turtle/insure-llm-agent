"""대화형 CLI 진입점.

실행: docker compose run --rm app
종료: exit / quit / 빈 줄 후 Ctrl-D
"""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from agent import build_agent
from config import settings


def _strip_surrogates(value: str) -> str:
    """Ollama/httpx JSON 인코딩을 깨뜨리는 lone surrogate 문자를 제거한다."""
    return "".join(ch for ch in value if not 0xD800 <= ord(ch) <= 0xDFFF)


def _sanitize_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_surrogates(value)
    if isinstance(value, list):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_obj(item) for item in value)
    if isinstance(value, dict):
        return {_sanitize_obj(key): _sanitize_obj(item) for key, item in value.items()}
    return value


def _sanitize_messages(messages: list) -> list:
    """대화 히스토리 안의 비정상 유니코드를 제거해 다음 요청 실패를 막는다."""
    cleaned = []
    for message in messages:
        updates = {
            "content": _sanitize_obj(message.content),
            "additional_kwargs": _sanitize_obj(getattr(message, "additional_kwargs", {})),
            "response_metadata": _sanitize_obj(getattr(message, "response_metadata", {})),
        }
        if hasattr(message, "tool_calls"):
            updates["tool_calls"] = _sanitize_obj(message.tool_calls)
        if hasattr(message, "invalid_tool_calls"):
            updates["invalid_tool_calls"] = _sanitize_obj(message.invalid_tool_calls)
        cleaned.append(message.model_copy(update=updates))
    return cleaned


def main() -> None:
    print(f"보험 설계사 Agent (model={settings.ollama_model})")
    print("안녕하세요. 보험 설계사 분들을 위한 가입 설계 agent입니다.\n")

    agent = build_agent()
    messages = []

    while True:
        try:
            user_input = input("설계사> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "종료"}:
            break

        messages.append(HumanMessage(content=_strip_surrogates(user_input)))
        messages = _sanitize_messages(messages)

        try:
            result = agent.invoke({"messages": messages})
        except UnicodeEncodeError:
            messages = _sanitize_messages(messages)
            try:
                result = agent.invoke({"messages": messages})
            except UnicodeEncodeError:
                print("\nAgent> 이전 대화에 인코딩할 수 없는 문자가 섞여 있어 제거했지만, 요청을 다시 처리하지 못했습니다.\n")
                continue
        except Exception as exc:
            print(f"\nAgent> 처리 중 오류가 발생했습니다: {type(exc).__name__}: {exc}\n")
            continue

        messages = _sanitize_messages(result["messages"])

        # 마지막 AI 응답 출력
        last = messages[-1]
        if isinstance(last, AIMessage):
            print(f"\nAgent> {last.content}\n")


if __name__ == "__main__":
    main()
