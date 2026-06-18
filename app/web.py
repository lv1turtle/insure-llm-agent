"""FastAPI 웹 서버 — 외부에서 자연어 prompt 를 입력받아 agent 응답을 보여준다.

실행: uvicorn web:app --host 0.0.0.0 --port 8000
대화 히스토리는 브라우저별 session_id 로 메모리에 유지한다(재시작 시 초기화).
"""

import uuid

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from agent import build_agent
from config import settings
from main import _sanitize_messages, _strip_surrogates

app = FastAPI(title="보험 설계사 Agent")

# 에이전트는 무거우므로 프로세스 시작 시 1회만 생성
_agent = build_agent()

# session_id -> 대화 히스토리(messages)
_sessions: dict[str, list] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML_PAGE


@app.post("/chat")
def chat(req: ChatRequest):
    message = (req.message or "").strip()
    if not message:
        return JSONResponse(status_code=400, content={"error": "메시지가 비어 있습니다."})

    session_id = req.session_id or uuid.uuid4().hex
    messages = _sessions.get(session_id, [])
    messages.append(HumanMessage(content=_strip_surrogates(message)))
    messages = _sanitize_messages(messages)

    try:
        result = _agent.invoke({"messages": messages})
    except UnicodeEncodeError:
        messages = _sanitize_messages(messages)
        try:
            result = _agent.invoke({"messages": messages})
        except UnicodeEncodeError:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "이전 대화에 인코딩할 수 없는 문자가 섞여 있어 요청을 처리하지 못했습니다.",
                    "session_id": session_id,
                },
            )
    except Exception as exc:  # noqa: BLE001 — 사용자에게 오류 메시지로 전달
        return JSONResponse(
            status_code=500,
            content={"error": f"처리 중 오류: {type(exc).__name__}: {exc}", "session_id": session_id},
        )

    messages = _sanitize_messages(result["messages"])
    _sessions[session_id] = messages

    last = messages[-1]
    reply = last.content if isinstance(last, AIMessage) else ""
    return {"reply": reply, "session_id": session_id}


@app.post("/reset")
def reset(req: ChatRequest):
    """대화 히스토리 초기화."""
    if req.session_id:
        _sessions.pop(req.session_id, None)
    return {"ok": True}


HTML_PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>보험 설계사 Agent</title>
<style>
  :root { --bg:#0f172a; --panel:#1e293b; --me:#2563eb; --bot:#334155; --text:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background:var(--bg); color:var(--text); height:100vh; display:flex; flex-direction:column; }
  header { padding:14px 18px; background:var(--panel); font-weight:600; font-size:15px;
           display:flex; justify-content:space-between; align-items:center; }
  header button { background:transparent; color:#94a3b8; border:1px solid #475569;
                  border-radius:6px; padding:5px 10px; font-size:12px; cursor:pointer; }
  #log { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:78%; padding:10px 13px; border-radius:12px; line-height:1.5;
         white-space:pre-wrap; word-break:break-word; font-size:14px; }
  .me  { align-self:flex-end; background:var(--me); border-bottom-right-radius:3px; }
  .bot { align-self:flex-start; background:var(--bot); border-bottom-left-radius:3px; }
  .err { align-self:flex-start; background:#7f1d1d; }
  .typing { opacity:.65; font-style:italic; }
  form { display:flex; gap:8px; padding:14px; background:var(--panel); }
  #input { flex:1; padding:11px 13px; border-radius:9px; border:1px solid #475569;
           background:#0f172a; color:var(--text); font-size:14px; outline:none; }
  #send { padding:0 18px; border:none; border-radius:9px; background:var(--me);
          color:#fff; font-size:14px; cursor:pointer; }
  #send:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
  <header>
    <span>보험 설계사 Agent</span>
    <button id="reset" type="button">대화 초기화</button>
  </header>
  <div id="log"></div>
  <form id="form">
    <input id="input" autocomplete="off" placeholder="예) 김철수 고객 가입 가능한 상품 보여줘" />
    <button id="send" type="submit">전송</button>
  </form>
<script>
  const log = document.getElementById('log');
  const form = document.getElementById('form');
  const input = document.getElementById('input');
  const send = document.getElementById('send');
  let sessionId = localStorage.getItem('session_id') || null;

  function add(text, cls) {
    const div = document.createElement('div');
    div.className = 'msg ' + cls;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  add('안녕하세요. 보험 가입 설계를 도와드리는 agent 입니다. 무엇을 도와드릴까요?', 'bot');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    add(text, 'me');
    input.value = '';
    send.disabled = true;
    const typing = add('입력 중…', 'bot typing');
    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      const data = await res.json();
      typing.remove();
      if (data.session_id) { sessionId = data.session_id; localStorage.setItem('session_id', sessionId); }
      if (res.ok) add(data.reply || '(빈 응답)', 'bot');
      else add(data.error || '오류가 발생했습니다.', 'err');
    } catch (err) {
      typing.remove();
      add('네트워크 오류: ' + err, 'err');
    } finally {
      send.disabled = false;
      input.focus();
    }
  });

  document.getElementById('reset').addEventListener('click', async () => {
    await fetch('/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: '', session_id: sessionId }),
    });
    localStorage.removeItem('session_id');
    sessionId = null;
    log.innerHTML = '';
    add('대화를 초기화했습니다. 무엇을 도와드릴까요?', 'bot');
  });
</script>
</body>
</html>"""
