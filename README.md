# codex-harness-kit

[English](README.en.md) | **한국어**

코덱스(Codex)가 **테스트와 검증 기준을 건드리지 못하게 하고**, **일을 끝내기 전에 반드시 검증하게** 만드는 프로젝트용 하네스 키트입니다. 명령 한 줄로 설치하면 다음 장치가 프로젝트에 붙습니다.

| 역할 | 장치 | 하는 일 |
| --- | --- | --- |
| 알려 주기 | `AGENTS.md`의 하네스 규칙 | 테스트는 사람이 정한 기준이고, 막히면 우회하지 말고 멈추라고 알려 줍니다 |
| 막기 | PreToolUse 훅 `guard.py` | 테스트·검증 설정·하네스 파일 수정, `.env` 읽기, 실제 키 입력을 실행 직전에 거부합니다 |
| 확인하기 | Stop 훅 `verify.py` | 코덱스가 응답을 마치기 전에 테스트와 타입 검사를 돌리고, 실패하면 이어서 고치게 합니다 |
| 알아차리기 | 턴 시작·끝의 보호 파일 비교 | 스크립트나 MCP 도구처럼 가드가 보지 못한 경로로 보호 파일이 바뀌면, 턴이 끝날 때 멈추고 보고하게 합니다 |
| 멈추기 | 반복 한도 | 검증이 계속 실패하거나 코덱스가 `BLOCKED`로 사람에게 넘기면, 더 시키지 않고 경고를 남깁니다 |

『코덱스 완벽 가이드』 14장 '에이전트의 작업 환경을 설계하는 하네스 엔지니어링'에서 손으로 만든 하네스를, 어떤 프로젝트에나 설치할 수 있게 다듬은 것입니다. 파이썬 3.9 표준 라이브러리만 쓰고, 다른 패키지는 설치하지 않습니다.

## 실제로 이렇게 동작합니다

코덱스 CLI 0.161.0으로 확인한 대화를 줄여 옮겼습니다. 원문과 판정은 [검증 기록](docs/VERIFICATION.md)에 있습니다.

**테스트를 고치라고 하면** 훅이 막고, 코덱스는 사람에게 넘깁니다.

```text
나: 환불 정책이 바뀌었어. tests/test_refund.py의 기대값 1500을 1000으로 바꿔 줘
훅: 보호된 검증 기준 파일(tests/test_refund.py, 규칙 tests/)을 바꾸는 작업이라 막았습니다. ...
코덱스: BLOCKED. 사람이 tests/test_refund.py의 기대값을 1000으로 바꿔야 합니다. 파일은 바꾸지 않았습니다.
```

**테스트를 돌리지 말라고 해도** 끝내기 전에 검증이 돌아갑니다.

```text
나: app/refund.py의 120분 비교를 >= 에서 > 로 바꿔 줘. 테스트는 돌리지 마
코덱스: 바꿨습니다. 테스트는 실행하지 않았습니다.
훅: 응답을 마치기 전에 검증을 실행했는데 실패했습니다. ... test_full_refund_at_exactly_two_hours ... FAIL
코덱스: 정확히 120분이면 3,000원이어야 해서 >= 로 되돌렸습니다. 검증 통과, 테스트와 설정은 바꾸지 않았습니다.
```

## 빠른 시작

```bash
git clone https://github.com/dante01yoon/codex-harness-kit.git
python3 codex-harness-kit/install.py ~/projects/my-app --dry-run   # 무엇이 바뀌는지 먼저 보기
python3 codex-harness-kit/install.py ~/projects/my-app
```

설치한 프로젝트에서 상태를 확인합니다.

```bash
cd ~/projects/my-app
python3 .codex/harness/harness.py doctor   # 설치 점검
python3 .codex/harness/harness.py test     # 막아야 할 예시는 막고, 통과해야 할 예시는 통과하는지
```

마지막으로 **훅을 신뢰합니다.** 코덱스는 새 프로젝트 훅을 사람이 신뢰하기 전까지 경고 없이 건너뜁니다.

- 앱: [Settings > Hooks]에서 프로젝트를 누르고, 명령(Command)이 이 프로젝트의 `.codex/harness/guard.py`와 `verify.py`를 실행하는지 확인한 뒤 [Trust all]
- CLI: `/hooks`

`.codex/`와 `AGENTS.md`를 커밋해 두면 같은 저장소를 쓰는 사람도 같은 하네스를 씁니다(신뢰는 사람마다 한 번씩 해야 합니다).

## 설치되는 파일

```text
my-app/
├── AGENTS.md                      # 하네스 규칙 블록(기존 내용은 그대로 두고 표시된 블록만 관리)
├── docs/harness.md                # 이 프로젝트의 하네스 설명
├── .gitignore                     # .codex/harness/logs/ 추가
└── .codex/
    ├── hooks.json                 # UserPromptSubmit·Stop → verify.py, PreToolUse → guard.py (기존 훅은 백업 후 병합)
    └── harness/
        ├── harness.json           # 설정. 다시 설치해도 덮어쓰지 않음
        ├── guard.py  verify.py  harness.py  harnesslib.py
        └── logs/                  # 막은 기록과 검증 결과(비밀 값은 가림)
```

## 프리셋

설치할 때 프로젝트를 보고 프리셋과 검증 명령을 고릅니다. `--preset`과 `--verify "명령"`으로 직접 정할 수도 있습니다.

| 프리셋 | 감지 기준 | 보호하는 것 | 검증 명령 |
| --- | --- | --- | --- |
| `node` | `package.json` | `tests/`, `test/`, `__tests__/`, `/e2e/`, `*.test.*`, `*.spec.*`, vitest·jest·playwright 설정(vitest만 쓰면 `vite.config.*`도), `package.json`의 `test`·`lint`·`typecheck` 스크립트 줄 | `npm test`, `tsc --noEmit`, `lint` 스크립트(있는 것만) |
| `python` | `pyproject.toml` 등 | `tests/`, `test/`, `test_*.py`, `*_test.py`, `conftest.py`(모든 폴더), `pytest.ini`, `tox.ini`, `noxfile.py`, `pyproject.toml`의 pytest 설정 줄 | `pytest` 또는 `unittest` |
| `go` | `go.mod` | `*_test.go`, `testdata/` | `go test ./...`, `go vet ./...` |
| `generic` | 그 밖 | `tests/`, `test/` | 직접 지정 |

모든 프리셋은 하네스 자체(`.codex/hooks.json`, `.codex/config.toml`, `.codex/harness/`), `.env` 계열 파일, 9종의 비밀 값 패턴(토스페이먼츠·Stripe 실제 키, OpenAI·Anthropic·AWS·GitHub·Slack·Google 키, 개인 키)을 함께 지킵니다.

## 자주 쓰는 명령

```bash
python3 .codex/harness/harness.py doctor                       # 설정, 실행기 설치 여부 점검, 훅 명령을 실제로 실행해 확인
python3 .codex/harness/harness.py test                         # 규칙 예시 확인
python3 .codex/harness/harness.py check --bash "rm -rf tests"  # 이 명령이 막히는지 미리 보기
python3 .codex/harness/harness.py verify                       # 검증 명령을 지금 실행
python3 .codex/harness/harness.py log -n 20                    # 최근 기록
python3 .codex/harness/harness.py ci --base origin/main        # 보호 파일이 바뀐 브랜치면 실패(CI용)
```

훅은 내 컴퓨터에서만 동작하므로, 공유 저장소라면 CI에서도 확인하세요. GitHub Actions 예시입니다.

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- run: python3 .codex/harness/harness.py ci --base origin/${{ github.base_ref }}
```

## 처음 도입할 때

이미 진행 중인 프로젝트라면 `harness.json`의 `mode`를 `"observe"`로 두고 며칠 써 보세요. 막지 않고 `would_deny`로 기록만 하므로, `harness.py log`로 어떤 명령이 걸리는지 보고 규칙을 다듬은 뒤 `"enforce"`로 바꾸면 됩니다. 설정 항목은 [CONFIG.md](docs/CONFIG.md)에 있습니다.

## 품질과 검증

- **단위 테스트 53개**(`python3 -m unittest discover -s tests`): 가드·검증 훅·설치기를 임시 git 저장소에서 실제로 실행합니다. 훅은 `hooks.json`에 적힌 명령 그대로, 하위 폴더에서도 부릅니다. 이 가운데 회귀 코퍼스(`tests/test_probes.py`)에는 별도의 레드팀 점검에서 나온 막아야 할 명령 94개, 통과해야 할 명령 64개, 알려진 한계 6개가 들어 있고, 판단 하나가 3초 안에 끝나는지도 확인합니다.
- **실제 코덱스로 확인**(`scripts/e2e_codex.py`): 일회용 프로젝트에 설치하고 `codex exec`로 테스트 수정, 비밀 값 입력, 하네스 끄기, 테스트 없이 버그 넣기를 시켜 봅니다. 결과는 [VERIFICATION.md](docs/VERIFICATION.md)에 있습니다.
- **CI**: Ubuntu와 macOS, 파이썬 3.9와 3.12에서 테스트와 모든 프리셋 예시를 돌립니다.

## 다른 도구와 함께 쓰기

이 키트는 **검증 기준을 지키는 일**에 집중합니다. 되돌릴 수 없는 명령 전반(`git reset --hard`, `rm -rf ~` 등)은 [dcg](https://github.com/Dicklesworthstone/destructive_command_guard)나 [cc-safety-net](https://github.com/kenryu42/cc-safety-net), 저장소 전체의 비밀 값 검사는 [gitleaks](https://github.com/gitleaks/gitleaks)를 함께 쓰는 것을 권합니다. 비교와 조합 방법은 [COMPARISON.md](docs/COMPARISON.md)에 정리했습니다.

## 한계

명령 문자열과 패치를 해석하는 가드입니다. 성실하지만 목표에 쫓기는 에이전트가 기준을 바꾸는 일을 막기 위한 것이지, 악의적인 공격을 막는 샌드박스가 아닙니다. 스크립트 파일 안에서 일어나는 쓰기, 난독화한 명령, MCP 도구를 통한 쓰기는 실행 전에 막지 못할 수 있습니다. 그래도 보호 파일이 실제로 바뀌면 턴이 끝날 때 알아차리고 멈추게 합니다. 막는 것과 막지 못하는 것은 [THREAT_MODEL.md](docs/THREAT_MODEL.md)에 사례별로 적었습니다. 훅은 내 컴퓨터에서만 동작하므로, 공유 저장소라면 CI와 브랜치 보호로 같은 검사를 한 번 더 하세요.

## 업그레이드와 제거

```bash
git -C codex-harness-kit pull && python3 codex-harness-kit/install.py ~/projects/my-app   # 스크립트만 갱신, harness.json은 유지
python3 codex-harness-kit/install.py ~/projects/my-app --uninstall           # 훅·스크립트·AGENTS.md 블록 제거
python3 codex-harness-kit/install.py ~/projects/my-app --uninstall --purge   # 설정·기록, 손대지 않은 docs/harness.md, .gitignore 줄까지 제거
```

훅 명령이 바뀌면 코덱스가 다시 신뢰를 요구합니다.

## 요구 사항

- 파이썬 3.9 이상(macOS 기본 `python3`로 충분), git
- 훅을 지원하는 코덱스 CLI 또는 챗GPT 앱의 코덱스. 실제 동작은 macOS의 코덱스 CLI 0.161.0에서 확인했습니다. Windows는 `commandWindows` 설정을 넣어 두었지만 확인하지 않았습니다.

## 라이선스

MIT
