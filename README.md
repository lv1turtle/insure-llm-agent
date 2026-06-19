# 보험 설계사 LLM Agent (Spring + nginx + RabbitMQ + LangChain + Ollama + PostgreSQL)

설계사가 자연어로 "특정 고객을 보험 상품에 가입시켜줘"라고 요청하면,
LLM 에이전트가 DB를 조회하고 자격을 확인한 뒤 가입(계약 생성)까지 수행하는 예시 프로젝트입니다.

웹 요청은 **nginx(LB) → Spring Boot WAS ×2(WebSocket) → RabbitMQ → Python Worker(agent)** 의
비동기 파이프라인으로 처리되며, 외부 노출은 **ngrok** 으로 합니다.

## 구성

| 서비스 | 역할 |
|--------|------|
| `nginx` | 로드밸런서. `spring1`/`spring2` 로 분산, WebSocket 프록시 (`http://localhost:8080`) |
| `spring1`, `spring2` | Spring Boot + Tomcat WAS. 정적 UI 서빙 + WebSocket 게이트웨이(RabbitMQ 중계) |
| `rabbitmq` | 메시지 브로커. 요청 큐(`agent.requests`) + DLQ. 관리 UI `http://localhost:15672` |
| `worker` | Python. 요청을 소비해 LangGraph agent 로 처리, 응답 회신 |
| `postgres` | 고객/상품/계약 + **대화 히스토리(`conversations`)** (`db/init.sql` 로 자동 생성) |
| `app` | (선택) 디버깅용 대화형 CLI |
| `ngrok` | (선택, `--profile tunnel`) 외부 도메인 노출 |

> LLM 서버는 **호스트에 설치된 Ollama** 를 사용합니다(컨테이너에서 `host.docker.internal:11434` 로 접근).
> macOS 에서 GPU(Metal) 가속을 받기 위함입니다.

### 아키텍처
```
외부 사용자 ──(ngrok 터널)──▶ nginx (LB + WS 프록시)
                                  ├──────────────┐
                                  ▼              ▼
                              spring1         spring2     ← WAS + WebSocket
                                  └──────┬───────┘           (인스턴스 전용 응답 큐 + correlationId→WS)
                                         ▼
                                     RabbitMQ  (agent.requests / DLQ)
                                         ▼
                                   Python Worker  ── 히스토리 로드/저장(PostgreSQL)
                                         │  LangGraph ReAct Agent + ChatOllama(tool calling)
                                         ▼
                              ollama(qwen2.5) · tools.py(SQLAlchemy) · postgres
```

핵심 설계(세 가지 안전장치):
- **응답 라우팅**: 각 Spring 인스턴스가 전용 응답 큐를 만들고 `replyTo`+`correlationId` 로 요청 →
  worker 가 그 큐로 회신 → "요청을 보낸(=사용자 WS 가 붙은) 인스턴스"에만 도착.
- **메시지 신뢰성**: worker 는 `prefetch=1` + manual ack. 실패해도 에러 응답을 돌려준 뒤 ack,
  파싱 불가 독성 메시지는 DLQ(`agent.requests.dlq`)로 격리.
- **히스토리 영속화**: 세션 히스토리를 PostgreSQL(`conversations`)에 저장 → worker 무상태(다중화 가능).

> 참고: 모든 추론은 호스트의 단일 Ollama 로 모이므로 실제 처리량 천장은 Ollama 입니다.
> WAS 2대 + LB 는 아키텍처 패턴 시연/학습 목적입니다.

## 실행

```bash
# 0) 호스트에 Ollama 설치 후 서버 기동 + 모델 다운로드 (최초 1회)
#    macOS: brew install ollama  (또는 https://ollama.com 설치)
ollama serve &                         # 11434 포트로 LLM 서버 기동
ollama pull qwen2.5:7b                 # 모델 다운로드 (수 분 소요)

# 1) (선택) 환경변수 설정: 모델/ngrok 토큰
cp .env.example .env

# 2) 전체 스택 기동 (postgres·rabbitmq·worker·spring1·spring2·nginx)
docker compose up -d --build

# 3) 브라우저에서 접속
open http://localhost:8080

# (선택) 외부 도메인 노출: .env 에 NGROK_AUTHTOKEN 설정 후
docker compose --profile tunnel up -d ngrok
#   → http://localhost:4040 에서 발급된 공개 URL 확인

# (선택) 디버깅용 CLI
docker compose run --rm app
```

### 대화 예시
```
설계사> 김민수가 가입할 수 있는 상품 보여줘
설계사> 건강보험 상세 보여줘 (보기/납기 플랜과 특약 확인)
설계사> 김민수를 건강보험 100세만기 20년납으로, 입원일당특약 넣어서 가입 설계해줘
        ← check_enrollment 로 자격/필수특약/보험료 검증
설계사> 신규 고객 등록할게. 이름 한지수, 1990-04-01, 여, 010-...   ← register_customer
설계사> 김민수 가입 현황 보여줘
```

## 핵심 동작
- 가입 설계는 **주계약 1개 + 플랜(보기/납기) + 특약(필수/선택)** 으로 구성됩니다.
- 에이전트는 `app/agent.py` 의 시스템 프롬프트 규칙에 따라 **가입 전 `check_enrollment`**
  (자격·플랜·특약 종속성·필수특약 누락·보험료)를 검증하고, **부족·불일치 항목이 있으면
  설계사에게 알린 뒤 추가 정보를 입력받아** 보완합니다. 실제 가입(`enroll_policy`)은
  설계사의 최종 동의 후에만 수행합니다.
- 모든 DB 쿼리는 SQLAlchemy 파라미터 바인딩을 사용합니다(`app/database.py`).

## 커스터마이즈
- **모델 변경**: `.env` 의 `OLLAMA_MODEL` (tool calling 지원 모델 권장: `qwen2.5:7b`, `qwen2.5:14b` 등)
- **데이터 변경**: `db/init.sql` 수정 후 `docker compose down -v` 로 볼륨 초기화 후 재기동
- **tool 추가**: `app/tools.py` 에 `@tool` 함수 작성 후 `TOOLS` 리스트에 등록

## Ollama 연결 참고
- `worker`/`app` 컨테이너는 `OLLAMA_BASE_URL=http://host.docker.internal:11434` 로 **호스트 Ollama** 에 접속합니다
  (compose 의 `extra_hosts: host.docker.internal:host-gateway` 로 Linux 에서도 동작).
- 도커 없이 로컬에서 `python main.py` 로 실행하면 기본값 `http://localhost:11434` 로 접속합니다.
- 연결이 안 되면 호스트에서 `ollama serve` 가 떠 있는지, `ollama list` 에 모델이 있는지 확인하세요.
