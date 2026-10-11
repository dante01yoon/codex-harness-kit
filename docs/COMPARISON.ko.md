# 다른 도구와 함께 쓰기

[English](COMPARISON.md) | **한국어**

codex-harness-kit는 **검증 기준을 지키는 일**에 집중합니다. 위험한 명령 전반이나 비밀 값 전체를 다루는 도구는 이미 좋은 것이 많으니, 필요하면 함께 쓰세요. 코덱스는 같은 이벤트에 걸린 훅을 모두 실행하고, 하나라도 막으면 막힙니다.

| 도구 | 집중하는 일 | 함께 쓸 때 |
| --- | --- | --- |
| codex-harness-kit | 테스트·검증 설정·하네스 자체를 에이전트가 바꾸지 못하게 하기, 끝내기 전에 검증 실행하기, 막혔을 때의 행동 규칙(AGENTS.md) | 프로젝트 단위로 설치 |
| [destructive_command_guard (dcg)](https://github.com/Dicklesworthstone/destructive_command_guard) | `git reset --hard`, `rm -rf`, 클라우드·DB 명령 등 되돌릴 수 없는 명령 차단. 규칙 팩이 많음 | 사용자 전역 훅으로 설치 |
| [cc-safety-net](https://github.com/kenryu42/cc-safety-net) | 파괴적인 git·파일 명령과 비밀 파일 접근 차단. 여러 코딩 CLI 지원 | 사용자 전역 훅으로 설치 |
| [gitleaks](https://github.com/gitleaks/gitleaks) | 저장소와 커밋의 비밀 값 검사. 패턴이 가장 넓음 | 커밋 전 훅과 CI |
| CI와 브랜치 보호 | 내 컴퓨터 밖에서 같은 테스트와 검사를 강제 | 공유 저장소라면 꼭 |

## 이 키트를 만들 때 참고한 기준

비슷한 도구들이 공통으로 갖춘 것을 기준으로 삼았습니다.

| 기준 | 이 키트 |
| --- | --- |
| 설치 미리 보기와 제거 | `install.py --dry-run`, `--uninstall`, `--purge`. 기존 `hooks.json`은 백업 후 병합 |
| 점검 명령 | `harness.py doctor`: 설정 검증, 실행기 설치 여부, `hooks.json`에 적힌 훅 명령을 실제로 실행해 응답 확인 |
| 규칙마다 예시를 두고 자동 확인 | `harness.json`의 `examples`, `harness.py test` |
| 관찰 모드로 먼저 도입 | `mode: observe`, `CODEX_HARNESS_MODE` |
| 기록과 비밀 값 가림 | `.codex/harness/logs/events.jsonl`, 알려진 패턴은 `[REDACTED:id]`, 명령 해시 |
| 실패 정책을 명시 | `on_error`, [THREAT_MODEL.md](THREAT_MODEL.ko.md) |
| 래퍼와 간접 실행 해석 | `bash -c`, `eval`, heredoc, `$(...)`, `cd`, `env`/`sudo`/`timeout`, `npx`, `pnpm exec`, `python -m` |
| 실제 에이전트로 검증 | `scripts/e2e_codex.py`, [VERIFICATION.md](VERIFICATION.ko.md) |
| 문자열 검사가 놓친 것 잡기 | 턴 시작과 끝의 보호 파일 해시 비교(Stop 훅) |
| CI에서 같은 검사 | `harness.py ci --base origin/main` |
| 설치 실패 시 원래대로 | 모든 입력을 먼저 검사하고, 문제가 있으면 아무것도 쓰지 않음 |
| 의존성 없음 | Python 3.9 표준 라이브러리만 사용 |
