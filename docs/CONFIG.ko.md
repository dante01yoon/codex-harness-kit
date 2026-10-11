# 설정 레퍼런스 (`.codex/harness/harness.json`)

[English](CONFIG.md) | **한국어**

설치하면 프리셋으로 만든 `harness.json`이 생깁니다. 다시 설치해도 이 파일은 덮어쓰지 않습니다(`--force`를 주면 다시 만듭니다). 파일 자체가 보호 대상이라 코덱스는 고칠 수 없고, 사람이 직접 고칩니다. 고친 뒤에는 `python3 .codex/harness/harness.py doctor`로 확인하세요.

| 키 | 기본값 | 설명 |
| --- | --- | --- |
| `version` | `1` | 설정 형식 버전 |
| `lang` | `"ko"` | 코덱스에게 돌려줄 메시지 언어(`ko`, `en`) |
| `mode` | `"enforce"` | `observe`로 바꾸면 막지 않고 기록만 합니다. 처음 도입할 때나 기준을 잠시 바꿀 때 씁니다. 환경 변수 `CODEX_HARNESS_MODE`가 있으면 그 값이 우선합니다 |
| `on_error` | `"block"` | 가드(PreToolUse)가 실패했을 때(설정 오류 등) 막을지(`block`) 통과시킬지(`allow`). 코덱스는 훅이 죽으면 그냥 통과시키므로 기본값은 막는 쪽입니다. 검증 훅(Stop)은 실패해도 막지 않고 경고만 남깁니다 |
| `protect.paths` | 프리셋 | 코덱스가 만들거나 고치거나 옮기거나 지우면 안 되는 경로 |
| `protect.allow_commands` | 프리셋 | 보호 경로를 인자로 받아도 되는 명령의 앞부분. 테스트 실행기 등. `verify.commands`는 자동으로 포함됩니다 |
| `protect.update_flags` | `-u`, `--update` 등 | 허용된 테스트 실행기라도 이 플래그가 붙으면 막습니다(스냅숏 갱신은 기대값을 바꾸는 일이니까요) |
| `deny_read.paths` | `.env` 계열, `*.pem` 등 | 읽지도 쓰지도 못하는 경로 |
| `secrets.patterns` | 9종 | `{"id", "regex"}` 목록. 명령 문자열과 패치에 새로 들어가는 줄에서 찾습니다 |
| `verify.commands` | 감지 결과 | `{"name", "run", "timeout", "cwd", "stop_on_failure"}` 목록. 순서대로 실행합니다 |
| `verify.skip_when_unchanged` | `true` | 마지막 통과 뒤로 바뀐 파일이 없으면 검증을 건너뜁니다. 질문만 한 턴이 느려지지 않습니다. git 저장소에서만 동작합니다 |
| `verify.total_timeout` | `570` | 검증 명령 전체에 쓰는 최대 시간(초). 명령마다의 `timeout`은 남은 시간 안에서만 씁니다. Stop 훅의 제한 시간은 설치할 때 이 값 + 30초(최대 600초)로 정합니다 |
| `verify.check_protected_changes` | `true` | 턴을 시작할 때(UserPromptSubmit) 보호 파일의 해시를 기억해 두고, 턴이 끝날 때 바뀐 파일이 있으면 알립니다. 가드가 보지 못한 경로(스크립트, MCP 도구)로 바뀐 경우를 잡습니다 |
| `verify.max_continuations_per_turn` | `2` | 검증 실패로 코덱스를 다시 일하게 하는 최대 횟수. 넘으면 멈추고 경고만 남깁니다 |
| `verify.respect_blocked` | `true` | 코덱스의 마지막 메시지에 `BLOCKED`가 있으면(사람에게 넘기고 멈춘 경우) 검증이 실패해도 다시 일하게 하지 않고 경고만 남깁니다 |
| `verify.output_tail_lines` | `40` | 실패한 명령의 출력 중 코덱스에게 보여 줄 마지막 줄 수 |
| `log.enabled`, `log.path`, `log.max_bytes` | 켜짐 | 막은 기록과 검증 결과를 JSON Lines로 남깁니다. 막은 명령은 앞부분 240자와 해시를 남기며, `secrets.patterns`에 걸리는 값만 `[REDACTED:id]`로 가립니다. 패턴에 없는 비밀은 그대로 남을 수 있으니 기록 폴더는 커밋하지 마세요(설치기가 `.gitignore`에 넣습니다) |
| `examples.block`, `examples.allow` | 프리셋 | `harness.py test`가 확인하는 예시. 규칙을 바꾸면 예시도 함께 고치세요 |

## 경로 패턴

`.gitignore`와 비슷한 규칙을 씁니다. 경로는 프로젝트 폴더 기준입니다.

| 패턴 | 뜻 |
| --- | --- |
| `tests/` | `tests` 폴더와 그 아래 모든 것 |
| `vitest.config.*` | 어느 폴더에 있든 이 이름의 파일(`/`가 없으면 이름으로 찾습니다) |
| `**/*.test.*` | 모든 깊이의 `*.test.*` 파일 |
| `src/legacy/*.ts` | `src/legacy` 바로 아래의 `.ts` 파일 |
| `!.env.example` | 앞의 규칙에서 다시 제외. 뒤에 있는 규칙이 이깁니다 |

macOS와 Windows에서는 파일 시스템이 대개 대소문자를 구분하지 않으므로, 패턴도 대소문자를 구분하지 않고 맞춥니다.

## 예: 린트와 타입 검사 설정도 지키기

에이전트는 테스트 대신 검사 설정을 느슨하게 바꿔서 통과할 수도 있습니다. 이런 일이 걱정되면 보호 경로를 늘리세요. 대신 패키지를 추가하는 등 정당한 수정도 막히므로, 그때는 사람이 직접 고칩니다.

```json
"protect": {
  "paths": ["tests/", "**/*.test.*", "vitest.config.*", "tsconfig.json", "eslint.config.*", ".eslintrc*", ".codex/hooks.json", ".codex/config.toml", ".codex/harness/"]
}
```

## 예: 검증 명령

```json
"verify": {
  "commands": [
    {"name": "test", "run": "npm test --silent", "timeout": 300},
    {"name": "typecheck", "run": "npx --no-install tsc --noEmit", "timeout": 300},
    {"name": "lint", "run": "npm run lint --silent", "timeout": 300, "stop_on_failure": false}
  ]
}
```

검증 명령이 오래 걸리면 `verify.total_timeout`을 늘리고 설치를 다시 실행하세요. Stop 훅의 제한 시간이 바뀌므로 다시 신뢰해야 합니다. 명령을 찾지 못하거나(종료 코드 127, `command not found`) 파이썬 모듈이 없으면(`No module named`) 구현 문제가 아니라 환경 문제로 보고, 코덱스에게 다시 시키지 않고 경고만 남깁니다.

## 예: 프로젝트 규칙 (`lint`, 0.2)

```json
"lint": {
  "rules": [
    {
      "id": "ui-no-money-math",
      "files": ["ui/**/*.ts", "src/components/**/*.tsx"],
      "exclude": ["**/*.test.*"],
      "forbid": "\\b(refund|fee|price)\\w*\\s*[-+*/]",
      "why": "금액은 src/rules에서 정한다. 화면에서 계산하면 규칙과 어긋난다.",
      "fix": "src/rules의 함수를 불러 결과만 화면에 보여 준다."
    }
  ]
}
```

- `files`·`exclude`는 경로 패턴, `forbid`는 파일 안에 나오면 안 되는 정규식입니다.
- `why`와 `fix`는 코덱스가 읽고 바로 고칠 수 있게 씁니다. 위반하면 `파일:줄:열 [규칙] 이유`와 `고치는 법`이 출력됩니다.
- 한 줄만 예외로 두려면 그 줄에 `harness-lint-ignore: <규칙 id>`와 이유를 주석으로 적습니다.
- 이미 위반이 많은 프로젝트라면 사람이 `harness.py lint --baseline`을 실행해 지금 있는 위반을 `.codex/harness/lint-baseline.json`에 기록합니다. 이후에는 새 위반만 실패합니다. 코덱스는 이 명령을 실행할 수 없습니다.
- 검증 명령에 넣으려면 설치할 때 `--lint`를 붙이거나 `verify.commands`에 `python3 .codex/harness/harness.py lint`를 더합니다.

## 예: 화면 확인 (`see`, 0.2)

```json
"see": {
  "url": "http://127.0.0.1:5173",
  "start": "npm run dev -- --port 5173",
  "pages": [{"name": "home", "path": "/"}, {"name": "bookings", "path": "/bookings"}],
  "viewports": ["390x844", "1280x900"],
  "out": "artifacts/harness/see",
  "wait_ms": 2500,
  "log": ""
}
```

| 항목 | 뜻 |
| --- | --- |
| `url` | 앱의 주소. 이미 떠 있으면 그대로 씁니다 |
| `start` | 앱이 떠 있지 않을 때 띄울 명령. 확인이 끝나면 다시 끕니다 |
| `pages` | 확인할 화면. `name`은 스크린샷 파일 이름이 됩니다 |
| `viewports` | 화면 크기. 기본은 휴대폰(390x844)과 데스크톱(1280x900) |
| `out` | 스크린샷·`summary.txt`·서버 로그를 둘 폴더. `.codex` 밖이어야 합니다 |
| `wait_ms` | 스크립트가 화면을 그릴 시간 |
| `log` | 앱이 따로 로그 파일을 쓴다면 그 경로(비우면 `out/server.log`) |
| `chrome` | Chrome·Chromium 경로(비우면 자동으로 찾음) |

페이지가 응답하지 않거나(HTTP 4xx·5xx), 스크립트 예외, 실패한 리소스 로드, 콘솔 오류가 있으면 실패합니다. 스크린샷이 정상인지는 코덱스가 직접 열어 보고 판단합니다.
