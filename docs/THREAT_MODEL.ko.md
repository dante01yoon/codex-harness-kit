# 무엇을 막고, 무엇을 막지 못하나

[English](THREAT_MODEL.md) | **한국어**

codex-harness-kit는 **성실하지만 목표에 쫓기는 에이전트**를 상대로 설계했습니다. 테스트를 통과시키라는 요청을 받은 코덱스가 기대값을 바꾸거나, 테스트를 지우거나, 설정을 느슨하게 하거나, 실제 키를 코드에 적는 일입니다. 악의적인 공격자를 막는 샌드박스가 아닙니다.

## 막는 것

- `apply_patch`로 보호 경로를 추가·수정·삭제·이동하는 일. `Move to:`, 절대 경로, `..`, 따옴표·백슬래시·공백이 섞인 헤더, 셸에서 `apply_patch <<EOF`로 부르는 경우도 포함합니다
- `git apply`, `git am`, `patch`로 보호 경로를 바꾸는 일. 패치 파일, 표준 입력(`< fix.patch`), heredoc 모두 봅니다. 다른 명령에서 파이프로 넘어온 패치는 내용을 볼 수 없어 거부합니다
- 셸 명령으로 보호 경로를 바꾸는 일: 리디렉션(`>`, `>>`, `>|`, `&>`, `exec >`), `tee`/`sponge`, `cp`/`mv`/`ln`/`rsync`/`install`의 대상(`-t` 포함), `rm`/`touch`/`truncate`/`chmod`, `sed -i`/`perl -i`/`awk -i inplace`, `ed`/`ex`/`vim -c`, `dd of=`, `find -delete`/`-exec`(`-name` 조건을 따져 봅니다), `git checkout`/`restore`/`rm`/`mv`
- 간접 실행: `cd`/`pushd` 뒤의 상대 경로, 서브셸, `bash -c`/`sh -lc`, `eval`, heredoc으로 넘긴 셸·파이썬 코드, 쓰기 동작이 있는 `python -c`/`node -e`, `$(pwd)`·`$PWD`·`$(git rev-parse --show-toplevel)`로 만든 경로, 보호 경로가 들어간 `$(...)`와 백틱, `xargs`로 넘긴 경로
- 글롭은 실제 파일로 펼쳐서 판단합니다(`rm tests/*.ts`는 막고 `rm lib/*.js`는 통과)
- 보호 경로를 품은 폴더를 통째로 지우거나 옮기는 일(`rm -rf src`에 `src/a.test.ts`가 있는 경우 등)
- 포매터가 보호 경로를 다시 쓰는 일(`prettier --write .`, `black .`, `gofmt -w .` 등)
- 어떤 실행기로 부르든 기대값 갱신 플래그(`vitest -u`, `pnpm exec vitest -u`, `jest --updateSnapshot`, `playwright --update-snapshots` 등)
- 이미 커밋한 테스트를 되돌리는 일(`git checkout HEAD~3 .`, `git checkout <커밋>`, `git switch --detach <커밋>`, `git restore --source=…`, `git reset --hard <rev>`, `git revert`, `git read-tree -u`)과, 사람이 고친 테스트가 남아 있을 때의 `git stash`/`reset --hard`/`checkout .`/`clean`
- 테스트 설정을 바꾸는 일: `apply_patch`로 `package.json`의 `test`·`lint`·`typecheck` 스크립트 줄이나 `pyproject.toml`·`setup.cfg`의 pytest 설정 줄을 바꾸는 일, `npm pkg set scripts.*`
- 하네스 자체를 끄는 일: `.codex/hooks.json`, `.codex/config.toml`, `.codex/harness/`, `~/.codex/` 수정
- `.env` 같은 비밀 파일을 읽거나 쓰는 일. 글롭(`cat .env*`), `grep -r`, `rg --hidden`, `git show HEAD:.env`도 포함합니다
- 실제 비밀 값으로 보이는 문자열이 명령이나 새로 추가되는 줄에 들어가는 일
- macOS와 Windows에서는 대소문자만 바꾼 경로(`rm TESTS/...`)도 같은 경로로 봅니다

## 일부러 막는 것

- `cp .env.example .env`: 비밀 파일에 쓰는 일이라 막습니다. 사람이 직접 만드세요.
- 포매터나 `--fix`가 보호 파일이 들어 있는 폴더 전체를 다루는 경우(`npx eslint --fix src`인데 `src`에 `*.test.ts`가 있는 경우 등): 테스트 파일도 바뀌므로 막습니다. 파일을 지정해 실행하게 하세요.

## 막지 못하는 것

아래 사례는 실행 전에 막지 못합니다. `tests/test_probes.py`의 `KNOWN_GAPS`에 그대로 들어 있고, 테스트가 "여전히 통과한다"를 확인합니다. 가드가 이 중 하나를 막게 되면 테스트가 실패하므로 이 문서도 함께 고칩니다.

다만 **보호 파일이 실제로 바뀌었다면 턴이 끝날 때 알아차립니다.** 턴을 시작할 때 보호 파일의 해시를 기억해 두고(UserPromptSubmit 훅), 끝날 때(Stop 훅) 다시 비교해 바뀐 파일이 있으면 코덱스에게 멈추고 보고하라고 알립니다. 되돌리지는 못하지만 조용히 지나가지는 않습니다. 비밀 파일을 읽는 일은 이 방법으로도 잡지 못합니다.

| 사례 | 이유 |
| --- | --- |
| `python3 scripts/fix.py` | 쓰기가 스크립트 파일 안에서 일어나 명령 문자열에 보이지 않습니다 |
| `tar -xf /tmp/b.tar` | 압축 파일 안의 경로를 열어 보지 않습니다 |
| `xargs rm < /tmp/list` | 경로가 다른 파일에서 옵니다 |
| `node -e "fs.rmSync(['tes','ts'].join(''))"` | 경로를 실행 중에 조립합니다 |
| `ln -s tests t && rm t/rules/helpers.ts` | 같은 명령 안에서 만든 폴더 별칭을 따라가지 않습니다(이름 규칙 `**/*.test.*`에 걸리는 파일은 막힙니다) |
| `node -r dotenv/config -e '...'` | 라이브러리가 비밀 파일을 읽습니다 |

그 밖에:

- **난독화.** 변수 조합(`d=te; rm ${d}sts/a`), 인코딩한 명령(`echo ... | base64 -d | sh`) 등. 문자열 검사의 한계입니다.
- **훅이 보지 못하는 경로.** MCP 도구의 파일 쓰기(기본 matcher는 `Bash`와 `apply_patch`만 봅니다), `write_stdin`으로 이미 실행 중인 셸에 보내는 입력, 훅을 거치지 않는 특수 도구.
- **셸 명령으로 `package.json`이나 `pyproject.toml`의 테스트 설정을 고치는 일.** `apply_patch`로 `test`·`lint`·`typecheck` 스크립트 줄이나 pytest 설정 줄을 바꾸는 일과 `npm pkg set scripts.*`는 막지만, `sed -i`처럼 셸로 고치는 경우는 막지 않습니다. 파일 전체를 보호하면 의존성 추가도 막히기 때문입니다. 필요하면 `protect.paths`에 넣으세요.
- **신뢰하지 않은 훅.** 코덱스는 새로 만들거나 바뀐 프로젝트 훅을 사람이 신뢰하기 전까지 경고 없이 건너뜁니다. `codex exec`처럼 화면 없이 실행할 때는 더 알아차리기 어렵습니다.
- **훅이 죽는 경우.** 파이썬이 없거나 훅이 제한 시간을 넘기면 코덱스는 해당 도구 호출을 그대로 진행합니다. 가드는 자기 오류를 잡아 막는 쪽으로 처리하고(`on_error`), 테스트는 판단 하나가 3초 안에 끝나는지 확인하지만, 프로세스 자체가 시작되지 못하면 어쩔 수 없습니다. `harness.py doctor`로 확인하세요.
- **비밀 값의 모든 형태.** 정해 둔 패턴만 찾습니다. 저장소에 올리기 전에는 gitleaks 같은 전용 도구를 함께 쓰세요.
- **내 컴퓨터 밖.** 훅은 로컬에서만 동작합니다. 공유 저장소에서는 CI와 브랜치 보호가 같은 검사를 한 번 더 해야 합니다.

## 실패했을 때의 기본 동작

| 상황 | 가드(PreToolUse) | 검증(Stop) |
| --- | --- | --- |
| 설정 파일이 없거나 잘못됨 | 막음(`on_error: block`) | 경고만 남기고 끝냄 |
| 검증 명령을 찾지 못함(127, `No module named`) | - | 환경 문제로 보고 경고만 남김 |
| 코덱스가 `BLOCKED`로 시작하는 줄을 남기고 멈춤 | - | 다시 시키지 않고 경고만 남김 |
| 하네스 폴더를 찾지 못함 | `{}`로 답해 통과 | `{}`로 답함 |
| 훅 내부 예외 | 막음(`on_error: block`) | 경고만 남기고 끝냄 |
| 검증 명령 실패 | - | 코덱스가 이어서 고치게 함(최대 `max_continuations_per_turn`번) |
| 검증 명령 시간 초과 | - | 실패로 처리 |
| 훅 프로세스가 시작되지 못함 | 코덱스가 통과시킴 | 코덱스가 턴을 끝냄 |

검증 훅이 실패할 때 막지 않는 이유는, 설정 오류 하나로 모든 응답이 끝나지 않는 상태가 되는 것을 피하기 위해서입니다.
