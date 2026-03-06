# opencode2openwebui

OpenWebUI에서 필요할 때만 OpenCode를 서브에이전트처럼 호출하기 위한 로컬 전용 오케스트레이터입니다.

핵심 원칙은 두 가지입니다.

- 새 OpenWebUI 대화가 생겨도 OpenCode 세션은 자동 생성하지 않습니다.
- 사용자가 명시적으로 OpenCode 위임을 요청했을 때만 첫 세션을 lazy-create 합니다.

## 동작 요약

- 새 대화만 만든 상태:
  - OpenCode 세션 없음
  - SQLite 바인딩 없음
  - `oc/session/list`는 빈 배열 반환
- 사용자가 일반 질문만 하는 상태:
  - OpenCode tool 호출 금지
  - 오케스트레이터 상태 변화 없음
- 사용자가 "OpenCode로 해줘"처럼 명시적으로 위임:
  - 첫 `oc_task_async` 또는 `oc_task_sync` 호출 시 세션 생성
  - 이후 같은 `chat + slot` 요청은 기존 세션 재사용

## 디렉터리

```text
opencode2openwebui/
  app.py
  README.md
  requirements.txt
  requirements-dev.txt
  openwebui_action_opencode_approval.py
  openwebui_system_prompt.md
  opencode.json.example
  data/
  tests/
```

## 요구사항

- Python 3.9+
- 같은 WSL 안에서 실행 중인 OpenWebUI
- 같은 WSL 안에서 실행 중인 OpenCode

이 프로젝트는 외부 네트워크 연결을 전제로 하지 않습니다. `OPENCODE_BASE_URL`도 로컬 주소만 허용합니다.

허용 주소:

- `http://127.0.0.1:...`
- `http://localhost:...`
- `http://[::1]:...`

## 설치

```bash
cd /path/to/opencode2openwebui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

테스트까지 돌리려면:

```bash
pip install -r requirements-dev.txt
```

## OpenCode 실행

```bash
export OPENCODE_SERVER_PASSWORD='change-me'
opencode serve --hostname 127.0.0.1 --port 4096
```

프로젝트 루트 고정 정책 때문에, 이 오케스트레이터는 OpenCode 서버가 현재 바라보는 작업 루트를 기준으로만 동작합니다.

`workspace_root`를 별도로 넘겨도 OpenCode의 현재 프로젝트 루트와 다르면 요청을 거부합니다.

## 오케스트레이터 실행

```bash
cd /path/to/opencode2openwebui
source .venv/bin/activate

export OPENCODE_BASE_URL='http://127.0.0.1:4096'
export OPENCODE_SERVER_PASSWORD='change-me'
export ORCH_DB_PATH='/path/to/opencode2openwebui/data/orchestrator.db'
export ENABLE_OPENCODE_EVENT_LISTENER='true'
export ALLOW_MISSING_OWUI_HEADERS='false'

python3 app.py
```

기본 바인딩 주소는 `127.0.0.1:8787`입니다.

## OpenWebUI 설정

OpenWebUI 백엔드 환경변수:

```bash
ENABLE_FORWARD_USER_INFO_HEADERS=true
```

이 헤더가 있어야 오케스트레이터가 다음 키로 세션을 구분할 수 있습니다.

- `X-OpenWebUI-User-Id`
- `X-OpenWebUI-Chat-Id`
- `X-OpenWebUI-Message-Id`

OpenWebUI에서 Tool Server 등록:

- Admin Settings -> Tools
- Global Tool Server URL: `http://127.0.0.1:8787`

## OpenCode tool 사용 규칙

메인 모델에는 [openwebui_system_prompt.md](openwebui_system_prompt.md)를 시스템 프롬프트로 넣는 것을 권장합니다.

핵심 규칙:

- 사용자가 명시적으로 OpenCode 위임을 요청한 경우에만 tool 호출
- 새 대화 시작만으로 `oc_session_ensure` 호출 금지
- 첫 작업 요청은 `oc_task_async` 우선

## 엔드포인트

### `GET /oc/session/list`

- 현재 대화의 active binding 목록
- 새 대화에서는 빈 배열이 정상

### `POST /oc/session/ensure`

- 사용자가 명시적으로 준비를 원할 때만 수동 호출
- 자동 호출 용도 아님

### `POST /oc/task/async`

- 첫 실제 OpenCode 작업의 기본 진입점
- binding이 없으면 lazy-create

### `POST /oc/task/sync`

- 짧은 작업용
- 역시 binding이 없으면 lazy-create

### `POST /oc/status`

- binding이 없으면 `404`가 아니라 `started: false` 반환

### `GET /oc/permission/list`

- binding이 없으면 `started: false`, 빈 목록 반환

### `POST /oc/permission/reply`

- OpenCode permission ask에 `once`, `always`, `reject` 응답

### `POST /oc/fork`

- 다른 slot으로 OpenCode 세션 분기

## 세션 매핑 규칙

바인딩 키:

```text
owui_user_id + owui_chat_id + fixed_workspace_root + slot
```

기본 slot은 `default`입니다.

즉 한 대화에서 아무 요청도 없으면 세션이 없고, 첫 위임 요청 이후에만 아래처럼 생깁니다.

- `default`
- `review`
- `experiment`

## Action Function

[openwebui_action_opencode_approval.py](openwebui_action_opencode_approval.py)를 OpenWebUI Action Function으로 등록할 수 있습니다.

버튼:

- `OC Status`
- `OC Approve Once`
- `OC Approve Always`
- `OC Reject`

OpenCode 세션이 아직 시작되지 않은 대화에서는 이 Action Function이 오류 대신 안내 메시지를 반환합니다.

## OpenCode 설정 예시

프로젝트 루트에 아래 파일을 두고 시작하면 됩니다.

- [opencode.json.example](opencode.json.example)

복사 예시:

```bash
cp ./opencode.json.example /path/to/repo/opencode.json
```

## curl 예시

세션 목록 조회:

```bash
curl -s http://127.0.0.1:8787/oc/session/list \
  -H 'X-OpenWebUI-User-Id: user-1' \
  -H 'X-OpenWebUI-Chat-Id: chat-1'
```

새 대화에서는 `active_bindings: []`가 정상입니다.

첫 작업 요청:

```bash
curl -s http://127.0.0.1:8787/oc/task/async \
  -H 'Content-Type: application/json' \
  -H 'X-OpenWebUI-User-Id: user-1' \
  -H 'X-OpenWebUI-Chat-Id: chat-1' \
  -d '{
    "slot": "default",
    "prompt": "이 작업을 OpenCode로 맡겨서 수정해줘"
  }'
```

상태 조회:

```bash
curl -s http://127.0.0.1:8787/oc/status \
  -H 'Content-Type: application/json' \
  -H 'X-OpenWebUI-User-Id: user-1' \
  -H 'X-OpenWebUI-Chat-Id: chat-1' \
  -d '{"slot":"default"}'
```

## 테스트

```bash
cd /path/to/opencode2openwebui
source .venv/bin/activate
pytest
```

테스트 범위:

- 새 대화에서 세션이 자동 생성되지 않음
- `oc_status` not-started 응답
- `oc_permission_list` not-started 응답
- 첫 `oc_task_async`에서만 lazy-create
- 같은 slot 재호출 시 기존 세션 재사용
- 다른 slot 요청 시 새 세션 생성
- Action Function이 no-session 상태를 안전하게 처리

## 참고

- `data/orchestrator.db`는 런타임에 자동 생성되며 git에 포함하지 않습니다.
- 이 저장소는 provider 설정을 다루지 않습니다.
- OpenCode 세션 생성을 언제 시작할지는 메인 모델 시스템 프롬프트와 사용자 요청이 결정합니다.
