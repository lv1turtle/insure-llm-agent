# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

보험 설계사가 자연어로 고객을 보험 상품에 가입시키는 작업을 돕는 LLM 에이전트 예시.
스택: **LangChain/LangGraph + Ollama(qwen2.5:7b) + PostgreSQL**.
PostgreSQL 과 app 은 Docker Compose 로 구동하고, **Ollama 는 호스트에 설치된 것을 사용**한다
(컨테이너에서 `host.docker.internal:11434` 로 접속, macOS GPU 가속 목적).

## 실행 명령

```bash
ollama serve &                         # 호스트 LLM 서버 기동 (최초 1회 ollama pull qwen2.5:7b)
docker compose up -d postgres          # DB 기동
docker compose run --rm app            # 대화형 CLI (메인 사용 방식, 호스트 Ollama 사용)
```

- DB 스키마/시드 변경 후 재적용: `docker compose down -v && docker compose up -d postgres`
  (`init.sql`은 **빈 볼륨 최초 기동 시에만** 실행되므로 `-v`로 볼륨을 비워야 반영됨)
- 모델 변경: `.env`의 `OLLAMA_MODEL` (저사양 `qwen2.5:3b`, 고성능 `qwen2.5:14b`)
- 도커 없이 로컬 실행 시: `app/`에서 `pip install -r requirements.txt && python main.py`
  (이때 `DATABASE_URL`/`OLLAMA_BASE_URL`이 `localhost` 기본값으로 동작)

테스트 프레임워크는 아직 없음.

## 아키텍처

요청 흐름: 설계사(자연어) → `app`(LangGraph ReAct agent) → `ChatOllama`가 호출할 tool 결정
→ `tools.py`가 SQLAlchemy로 PostgreSQL 조회/쓰기 → 결과를 LLM이 해석해 응답.

`app/` (플랫 모듈 구조, 패키지 아님 — `from tools import ...`처럼 직접 import):
- `config.py` — 환경변수 설정. **기본값은 localhost(로컬 디버깅용)이며, compose 실행 시 `app` 서비스 env가 이를 덮어써 DB는 컨테이너 호스트(`postgres`), Ollama는 호스트(`host.docker.internal`)로 연결됨.**
- `database.py` — SQLAlchemy 엔진 + `fetch_all`/`fetch_one`/`execute` 헬퍼. 모든 쿼리는 `text()` 파라미터 바인딩 사용(직접 문자열 포매팅 금지).
- `tools.py` — `@tool` 함수 9종이 `TOOLS` 리스트로 export(고객 검색/등록, 상품 목록/상세, 고객별 가입가능 상품, 가입 검증/실행, 가입현황 조회). tool 추가 시 함수 작성 후 `TOOLS`에 등록.
- `agent.py` — `create_react_agent(ChatOllama, TOOLS, prompt=SYSTEM_PROMPT)`. 에이전트 동작 규칙(가입 설계 절차, 불일치 시 추가 정보 요청 등)은 코드가 아닌 **`SYSTEM_PROMPT` 문자열**로 강제됨.
- `main.py` — CLI 진입점. `messages` 리스트로 대화 히스토리를 누적해 매 턴 `agent.invoke`에 전달.

DB: `db/init.sql`이 7개 테이블과 시드 데이터를 생성. 핵심은 **주계약(`main_products`) + 특약(`riders`)** 모델이다:
- `main_products` ↔ `main_product_plans`(보기/납기/보험료) ↔ `policies`
- `main_products` ↔ `main_product_riders`(종속 매핑, `is_mandatory`=필수특약) ↔ `riders`, 가입 시 `policy_riders`로 연결
- `customers`(직업급수 `job_class` 포함). 증권번호 `policy_no`는 시퀀스 기반 DEFAULT로 자동 생성.

## 중요 규약

- **모델은 tool calling 지원 필수**: LangChain tool 연결이 핵심이라 `qwen2.5` 계열을 사용. tool calling이 없는 구형 모델로 바꾸면 에이전트가 동작하지 않음.
- **가입 검증 로직은 `_validate_enrollment` 한 곳에 집중**: `check_enrollment`(읽기)와 `enroll_policy`(쓰기)가 이 함수를 공유함. 자격/플랜/특약 종속성/필수특약/보험료 규칙을 바꿀 때는 여기만 수정하면 양쪽에 반영됨.
- **`enroll_policy`는 되돌리기 어려운 쓰기 작업**: 코드(`_validate_enrollment` 재검증, 트랜잭션으로 policy+riders 원자적 삽입)와 시스템 프롬프트(설계사 최종 동의) 양쪽에 안전장치가 있음.
- tool 반환값은 LLM이 읽도록 **한국어 키의 JSON 문자열**(`_json` 헬퍼)로 통일.
- 연령은 `birth_date`에서 `_calc_age`로 계산(컬럼에 저장하지 않음).
