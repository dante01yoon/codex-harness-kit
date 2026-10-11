# 이 프로젝트의 하네스

codex-harness-kit로 설치했습니다. 코덱스가 일하는 방식을 네 가지 장치로 붙잡습니다.

| 역할 | 장치 | 언제 동작하나 | 강제되나 |
| --- | --- | --- | --- |
| 알려 주기 | `AGENTS.md`의 하네스 규칙 | 모든 채팅 시작 | 아니요. 코덱스가 따르는 지침입니다 |
| 막기 | `.codex/harness/guard.py` (PreToolUse 훅) | 셸 명령과 파일 수정 직전 | 예. 보호 파일 수정, 비밀 파일 읽기, 비밀 값 입력을 거부합니다 |
| 확인하기 | `.codex/harness/verify.py` (Stop 훅) | 응답을 마치기 직전 | 예. 실패하면 코덱스가 이어서 고칩니다 |
| 알아차리기 | `.codex/harness/verify.py` (UserPromptSubmit + Stop) | 턴의 시작과 끝 | 예. 가드가 보지 못한 경로로 보호 파일이 바뀌면 멈추고 보고하게 합니다 |
| 멈추기 | `verify.max_continuations_per_turn` | 검증이 계속 실패하거나 코덱스가 BLOCKED로 넘길 때 | 예. 더 시키지 않고 경고를 남깁니다 |
| 규칙 검사 | `harness.py lint` (`lint.rules`) | 검증 명령에 넣었으면 응답을 마치기 직전 | 예. 위반한 파일·줄과 고치는 법을 코덱스에게 돌려줍니다 |
| 보기 | `harness.py see` (`see`) | 화면을 바꾼 뒤, 또는 검증 명령에 넣었으면 응답을 마치기 직전 | 검증에 넣으면 예. 스크린샷·콘솔 오류·서버 로그를 남겨 코덱스가 직접 보게 합니다 |

## 자주 쓰는 명령

```sh
python3 .codex/harness/harness.py doctor   # 설치 상태 점검
python3 .codex/harness/harness.py test     # harness.json의 예시가 기대대로 막히고 통과하는지
python3 .codex/harness/harness.py check --bash "rm tests/a.test.ts"
python3 .codex/harness/harness.py verify   # 검증 명령을 지금 실행
python3 .codex/harness/harness.py log      # 최근에 막거나 검증한 기록
python3 .codex/harness/harness.py lint     # 프로젝트 규칙 검사(위반 위치와 고치는 법)
python3 .codex/harness/harness.py lint --baseline   # 사람만: 지금 있는 위반을 기존 위반으로 기록
python3 .codex/harness/harness.py see      # 앱을 띄워 스크린샷·콘솔 오류·서버 로그 수집
python3 .codex/harness/harness.py ci --base origin/main   # CI에서: 보호 파일이 바뀐 브랜치면 실패
```

## 기준을 바꿔야 할 때

보호 파일은 사람이 직접 고칩니다. 코덱스에게 맡겨야 한다면 `harness.json`의 `mode`를 잠시 `observe`로 바꾸면 막지 않고 기록만 합니다. 이 파일도 보호 대상이라 사람이 바꿔야 합니다. 끝나면 `enforce`로 되돌리세요.

## 규칙과 화면 확인을 키우는 법

코덱스가 같은 실수를 두 번 하면 `harness.json`의 `lint.rules`에 규칙을 하나 더하세요. `why`에는 왜 안 되는지, `fix`에는 어떻게 고치면 되는지를 코덱스가 읽고 바로 고칠 수 있게 씁니다. 화면을 새로 만들면 `see.pages`에 경로를 더합니다. 이 설정 파일도 보호 대상이라 사람이 고칩니다.

## 한계

- 훅은 신뢰해야 동작합니다. `hooks.json`이나 훅 명령을 바꾸면 다시 신뢰해야 합니다. 신뢰하지 않은 프로젝트 훅은 경고 없이 건너뜁니다.
- 가드는 명령 문자열과 패치를 해석하는 검사입니다. 난독화한 명령, 실행 중에 경로를 만들어 내는 스크립트, MCP 도구를 통한 쓰기는 막지 못할 수 있습니다.
- `see`는 로컬 서버를 띄웁니다. 코덱스 기본 샌드박스 안에서 코덱스가 직접 실행하면 포트를 열 수 없지만(EPERM), Stop 훅은 샌드박스 밖에서 돌기 때문에 검증 명령에 넣으면 동작합니다. 결과는 `.codex` 밖(`artifacts/`)에 저장합니다. 코덱스 샌드박스는 `.codex` 쓰기를 막습니다.
- 훅은 내 컴퓨터에서만 동작합니다. 여러 사람이 쓰는 저장소라면 CI와 브랜치 보호로 같은 검사를 한 번 더 하세요.
