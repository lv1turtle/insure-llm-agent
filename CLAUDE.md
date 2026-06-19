# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

보험 설계사가 자연어로 고객을 보험 상품에 가입시키는 작업을 돕는 LLM 에이전트 예시.
스택: **nginx(LB) + Spring Boot/Tomcat(WAS·WebSocket) ×2 + RabbitMQ + Python Worker(LangChain/LangGraph) + Ollama(qwen2.5:7b) + PostgreSQL**.
웹 요청은 **nginx → Spring ×2 → RabbitMQ → Python worker(agent)** 비동기 파이프라인으로 처리된다.
모든 컨테이너는 Docker Compose 로 구동하고, **Ollama 는 호스트에 설치된 것을 사용**한다
(컨테이너에서 `host.docker.internal:11434` 로 접속, macOS GPU 가속 목적).

## 실행 명령

```bash
ollama serve &                         # 호스트 LLM 서버 기동 (최초 1회 ollama pull qwen2.5:7b)
docker compose up -d --build           # 전체 스택 기동 (postgres·rabbitmq·worker·spring1·spring2·nginx)
open http://localhost:8080             # 웹 UI (nginx → spring ×2 → rabbitmq → worker)
docker compose --profile tunnel up -d ngrok   # (선택) 외부 노출, .env 의 NGROK_AUTHTOKEN 필요
docker compose run --rm app            # (선택) 디버깅용 대화형 CLI
```

- DB 스키마/시드 변경 후 재적용: `docker compose down -v && docker compose up -d --build`
  (`init.sql`은 **빈 볼륨 최초 기동 시에만** 실행되므로 `-v`로 볼륨을 비워야 반영됨)
- 모델 변경: `.env`의 `OLLAMA_MODEL` (저사양 `qwen2.5:3b`, 고성능 `qwen2.5:14b`)
- 도커 없이 로컬 실행 시: `app/`에서 `pip install -r requirements.txt && python main.py`
  (이때 `DATABASE_URL`/`OLLAMA_BASE_URL`이 `localhost` 기본값으로 동작)
- RabbitMQ 관리 UI: `http://localhost:15672` (guest/guest), ngrok 대시보드: `http://localhost:4040`

테스트 프레임워크는 아직 없음.

## 아키텍처

웹 요청 흐름: 브라우저 WS → `nginx`(LB) → `spring1`/`spring2`(WAS, WebSocket 게이트웨이)
→ `RabbitMQ`(요청 큐 `agent.requests`) → `worker.py`(LangGraph ReAct agent) → `ChatOllama`가 tool 결정
→ `tools.py`가 SQLAlchemy로 PostgreSQL 조회/쓰기 → 응답을 `replyTo`+`correlationId`로 해당 Spring 인스턴스에 회신 → WS 푸시.

**세 가지 핵심 안전장치(각각 코드로 구현됨, 자동 아님):**
- **응답 라우팅** — 각 Spring 인스턴스가 전용 응답 큐(`AnonymousQueue`)를 만들고 요청에 `replyTo`+`correlationId`를 실어 보냄. worker 가 그 큐로 회신 → 사용자 WS 가 붙은 인스턴스에만 도착. `ChatWebSocketHandler`의 `correlationId→WebSocketSession` 맵(인스턴스 로컬)으로 매칭.
- **메시지 신뢰성** — worker 는 `prefetch=1` + manual ack. 처리 실패해도 에러 응답 회신 후 ack, 파싱 불가 메시지는 DLQ(`agent.requests.dlq`)로. 요청 큐의 `x-dead-letter-exchange` 인자는 **worker 와 Spring `RabbitConfig` 양쪽이 동일하게** 선언해야 함(불일치 시 PRECONDITION_FAILED).
- **히스토리 영속화** — 세션 히스토리는 worker 메모리가 아닌 PostgreSQL `conversations` 테이블에 저장(`history.py`, `messages_to_dict/_from_dict` 직렬화). worker 는 무상태라 다중화 가능.

`app/` (Python worker·CLI, 플랫 모듈 구조 — `from tools import ...`처럼 직접 import):
- `config.py` — 환경변수 설정. **기본값은 localhost(로컬 디버깅용)이며, compose 실행 시 서비스 env가 덮어써 DB는 `postgres`, RabbitMQ는 `rabbitmq`, Ollama는 호스트(`host.docker.internal`)로 연결됨.**
- `database.py` — SQLAlchemy 엔진 + `fetch_all`/`fetch_one`/`execute` 헬퍼. 모든 쿼리는 `text()` 파라미터 바인딩 사용(직접 문자열 포매팅 금지).
- `history.py` — `conversations` 테이블에 세션별 메시지 저장/복원(`load_history`/`save_history`/`clear_history`). 저장은 delete 후 전체 재삽입(idempotent).
- `tools.py` — `@tool` 함수 9종이 `TOOLS` 리스트로 export(고객 검색/등록, 상품 목록/상세, 고객별 가입가능 상품, 가입 검증/실행, 가입현황 조회). tool 추가 시 함수 작성 후 `TOOLS`에 등록.
- `agent.py` — `create_react_agent(ChatOllama, TOOLS, prompt=SYSTEM_PROMPT)`. 에이전트 동작 규칙(가입 설계 절차, 불일치 시 추가 정보 요청 등)은 코드가 아닌 **`SYSTEM_PROMPT` 문자열**로 강제됨.
- `worker.py` — **메인 진입점**. RabbitMQ 컨슈머. `_handle`이 type(chat/reset)별로 히스토리 로드→agent 호출→저장. 유니코드 방어 로직(`main.py`의 `_sanitize_messages`/`_strip_surrogates`)을 재사용.
- `main.py` — 디버깅용 CLI 진입점. `messages` 리스트로 대화 히스토리를 누적해 매 턴 `agent.invoke`에 전달.

`spring/` (Spring Boot 게이트웨이, Gradle 빌드 — 동일 이미지를 `spring1`/`spring2`로 2회 기동):
- `ChatWebSocketHandler` — `/ws` 핸들러. WS 메시지를 요청 큐로 publish, worker 응답을 `correlationId`로 WS에 푸시. **DB/agent 를 모르는 얇은 중계 계층.**
- `RabbitConfig` — 요청 큐(worker 와 인자 일치) + 인스턴스 전용 `AnonymousQueue` + 응답 리스너 컨테이너 선언.
- `ReplyListener` — 응답 큐 메시지를 받아 `ChatWebSocketHandler.routeReply`로 전달.
- `src/main/resources/static/index.html` — WebSocket 채팅 UI(기존 FastAPI HTML 포팅).

`nginx/nginx.conf` — `least_conn` 으로 `spring1`/`spring2` 분산. WebSocket 업그레이드 헤더 + 긴 타임아웃 설정.

DB: `db/init.sql`이 테이블과 시드 데이터를 생성. 핵심은 **주계약(`main_products`) + 특약(`riders`)** 모델이다:
- `main_products` ↔ `main_product_plans`(보기/납기/보험료) ↔ `policies`
- `main_products` ↔ `main_product_riders`(종속 매핑, `is_mandatory`=필수특약) ↔ `riders`, 가입 시 `policy_riders`로 연결
- `customers`(직업급수 `job_class` 포함). 증권번호 `policy_no`는 시퀀스 기반 DEFAULT로 자동 생성.

## 중요 규약

- **모델은 tool calling 지원 필수**: LangChain tool 연결이 핵심이라 `qwen2.5` 계열을 사용. tool calling이 없는 구형 모델로 바꾸면 에이전트가 동작하지 않음.
- **가입 검증 로직은 `_validate_enrollment` 한 곳에 집중**: `check_enrollment`(읽기)와 `enroll_policy`(쓰기)가 이 함수를 공유함. 자격/플랜/특약 종속성/필수특약/보험료 규칙을 바꿀 때는 여기만 수정하면 양쪽에 반영됨.
- **`enroll_policy`는 되돌리기 어려운 쓰기 작업**: 코드(`_validate_enrollment` 재검증, 트랜잭션으로 policy+riders 원자적 삽입)와 시스템 프롬프트(설계사 최종 동의) 양쪽에 안전장치가 있음.
- tool 반환값은 LLM이 읽도록 **한국어 키의 JSON 문자열**(`_json` 헬퍼)로 통일.
- 연령은 `birth_date`에서 `_calc_age`로 계산(컬럼에 저장하지 않음).
