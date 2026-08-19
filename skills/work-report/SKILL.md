---
name: work-report
description: Use when the user wants their AI coding session history (Claude Code, Codex) turned into a dated work log for performance reporting or timesheet evidence - triggers include "업무정리", "업무 실적", "실적 데이터", "세션 취합", "일자별 정리", "work report", "session history export". Do not use for reading session logs without producing the dated report.
---

# Work Report

로컬 AI 코딩 세션 로그에서 **사람이 준 업무 지시**만 뽑아 날짜별 폴더로 정리한다. 업로드는 하지 않는다 — 사용자가 직접 올린다.

## 실행

```bash
scripts/run.sh --today           # 오늘만 갱신 — 일상 운영은 이것
scripts/run.sh                   # 전체 기간 재생성 (처음 한 번)
scripts/run.sh --open            # + 파일 탐색기로 결과 폴더 열기
scripts/run.sh --date 2026-08-19 # 특정 날짜만 (콤마로 여러 날)
scripts/run.sh --days 3          # 최근 3일만
scripts/run.sh --open            # + 파일 탐색기로 결과 폴더 열기
scripts/run.sh --mark-uploaded   # 올린 뒤 실행. 다음부터 변경분만 보고
scripts/run.sh --init            # 설정 후보 탐지 (최초 1회)
```

**일상 운영은 `--today` 를 쓴다.** 증분이라 다른 날짜 폴더를 건드리지 않고 `index.csv` 의 해당 행만 갈아끼운다. 전체 재생성은 규칙이 바뀌었을 때만 하면 된다.

5단계: 세션 수집 → git 커밋 수집 → 날짜별 정리 → 변경분 비교 → 민감정보 점검.

스킬 디렉토리 기준 상대 경로다. 사용자가 "업무정리 해줘"라고 하면 그냥 실행하고 결과를 보고하라. 수집은 보통 10초 안에 끝난다.

## 최초 실행 — 설정이 없을 때

`run.sh` 가 "설정 파일이 없습니다" 로 종료하면, **사용자에게 경로를 입력하라고 되묻지 마라.** 사람은 자기 세션이 어느 경로에 얼마나 있는지 모른다. 대신 아래 순서로 진행한다.

**1. 후보를 탐지한다.**

```bash
scripts/run.sh --init
```

세션 로그를 스캔해 작업 경로를 세션 수 순으로 보여준다. git author 이메일 후보와 결과 폴더 제안도 함께 나온다.

**2. 사용자에게 확인받는다.** 탐지 결과를 그대로 보여주고 AskUserQuestion 으로 고르게 한다. 최소 세 가지를 확정해야 한다.

| 항목 | 판단 기준 |
|---|---|
| 수집 대상 경로 | 회사 업무 경로만 고른다. 개인 프로젝트가 섞인 상위 경로(예 `~/Project`)를 그대로 쓰면 개인 작업까지 수집된다. 여러 개면 콜론으로 잇는다 |
| 결과 폴더 | macOS 에서 `~/Documents`·`~/Desktop` 은 iCloud 동기화 대상이라 피한다 |
| git author 이메일 | 여러 이메일로 커밋한 이력이 있으면 전부 나열한다. 팀원 이메일을 고르면 남의 실적이 섞인다 |

후보 목록에서 **회사 경로와 개인 경로가 섞여 있으면 반드시 짚어라.** 예: `~/Project` 아래에 회사(`Riskzero`)와 개인(`ETC`) 이 함께 있으면 상위가 아니라 회사 경로만 골라야 한다.

**3. 설정을 만든다.**

```bash
scripts/run.sh --init --write "경로1:경로2" "결과폴더" "이메일1:이메일2"
```

기존 설정이 있으면 덮어쓰므로, 이미 있는 경우에는 먼저 사용자에게 물어라.

**4. 첫 수집을 돌린다.** 전체 기간이라 시간이 걸린다고 미리 알려라 (수백 일이면 1~2분).

설정 파일은 `~/.config/work-report/config.env` 다. 저장소 밖에 있어서 `git pull` 로 덮이지 않는다. 일회성 변경은 환경변수로 덮어쓸 수 있다 (`WORK_MASK_IP=1 run.sh --today`).

## 단계

| 단계 | 내용 |
|---|---|
| 1 | `collect_sessions.py` — `~/.claude/projects` + `~/.codex/sessions` 스캔, 대상 경로에서 실행된 세션만 추출 |
| 2 | `collect_commits.py` — git 저장소에서 내 커밋 수집 (무엇을 했는가) |
| 3 | `collect_diffs.py` — 그 커밋의 실제 diff 수집 (어떻게 바꿨는가) |
| 4 | `split_by_date.py` — `YYYY-MM-DD/` 폴더로 분류 (계층 없음) |
| 5 | 지난 업로드 기록과 비교 → 신규·변경 날짜 목록 |
| 6 | 민감정보 패턴 점검 (올릴 날짜만) |

## 보고할 내용

- 올릴 날짜 목록과 각 날짜의 지시 건수
- **커버리지 보고** — `누락:` 줄이나 `저장소 못찾음` 이 뜨면 그냥 넘기지 말고 사용자에게 알려라. 이 도구의 가장 위험한 실패는 크래시가 아니라 조용한 누락이다
- 민감정보 점검 결과 — **일치 항목이 있으면 그냥 넘기지 말고 명시적으로 보여줘라.** 올리면 되돌릴 수 없다
- 결과 폴더 경로

사용자가 업로드를 마쳤다고 하면 `--mark-uploaded` 를 실행한다. 그래야 다음 실행에서 변경분만 나온다.

## 출력 구조

```
by-date/
  index.csv                날짜별 요약 (지시수·도구·작업대상)
  YYYY-MM-DD/
    instructions.md        그날 사람이 준 지시 전문, 시간순
    instructions.jsonl
    commits.csv            그날 내 git 커밋 — 무엇을 했는가
    code.patch             그날 실제 코드 변경 — 어떻게 바꿨는가
    agent-tasks.md|.jsonl  AI 가 만든 지시 (집계 제외분)
    sessions.csv
  _reference/              전체 세션 인덱스, 일별 툴호출, coverage*.json
```

## 커버리지

매 실행마다 무엇을 못 담았는지 스스로 보고한다. `_reference/coverage*.json` 에 기계 판독용으로도 남는다.

| 사유 | 조치 |
|---|---|
| `변경 파일이 전부 제외 규칙` · `변경 파일 없음` | 정상 |
| `하루 용량 상한 초과` | 필요시 `WORK_CODE_MAX_DAY_KB` 상향 |
| `저장소 못찾음` · `codex_메타없음` · `claude_cwd_판정실패` | **비정상.** 새 로그 포맷이거나 경로가 어긋났다 |

## 알아둘 것

- **코드 diff 에는 자유형 비밀번호 마스킹을 쓰지 않는다** (`mask_text(..., freeform=False)`). 비밀번호 검증 정규식 같은 코드가 걸려 원본이 훼손된다. 구조화된 토큰과 `password=값` 은 계속 잡는다.
- **`code.patch` 는 `.env`·키 파일·lock·스키마 덤프·바이너리를 제외하고 만든다.** 상한은 파일당 500줄, 하루 2MB.
- **지시(원인)와 커밋(결과)을 시각으로 짝지어 읽어라.** 실적 근거는 "무엇을 지시했다"보다 "지시 → 커밋" 연결이 강하다.
- **집계 대상은 `bucket == human` 뿐이다.** `generated`(다른 AI 가 만든 프롬프트)와 `agent-task` 는 `agent-tasks.*` 로 분리된다. 실적 숫자를 말할 때 섞지 마라.
- **로그 포맷이 버전마다 다르다.** Codex 는 구형(2025년, `session_meta` 없음)과 신형이 공존하고 전환기에는 섞여 있다. 필드 위치와 새 포맷 대응 절차는 이 스킬 디렉토리 기준 `../../docs/log-formats.md` 에 있다. 커버리지에 `codex_메타없음` 이나 `claude_cwd_판정실패` 가 뜨면 그 문서를 읽어라.
- **Codex 지시문 출처가 두 곳이다.** 구형은 `event_msg/user_message`, 신형은 `response_item/message:user`. 둘 다 읽고 중복 제거한다. 한쪽만 보면 특정 시기 데이터가 통째로 빠진다.
- **Claude 프로젝트 디렉토리명을 믿지 마라.** cwd 를 인코딩한 이름이라 `/` 와 `-` 를 구분하지 못한다. 로그 안의 `cwd` 필드로 판정한다.
- **Codex 토큰 수치는 Claude 와 비교 불가.** 캐시분이 입력 토큰에 누적 포함된다. 세션·지시·툴호출 수는 동일 기준.
- **오늘 날짜 폴더는 매 실행마다 바뀐다.** 진행 중인 대화가 수집 대상 경로에 있으면 계속 쌓인다. 하루 끝에 한 번 돌리는 게 깔끔하다.
- **`~/Documents`, `~/Desktop` 을 `WORK_DIR` 로 쓰지 마라.** macOS iCloud 동기화 대상이라 대용량 폴더를 넣으면 ` 2` 충돌 폴더가 생기고 데이터가 클라우드로 나간다.
- **자격증명은 값이 마스킹돼서 나간다** (`[REDACTED:SECRET]`, 기본 켜짐). 다만 마스킹은 값만 지우고 키워드·문맥은 남기므로, 점검 단계가 `비밀번호`·IP 같은 잔여 신호를 계속 보고하는 건 정상이다.
- **채팅에 붙여넣은 자격증명은 그날 `instructions.md` 에 그대로 들어간다.** 대화 자체가 수집 대상 경로에서 진행되면 토큰·비밀번호가 결과물에 실린다. 민감정보 점검이 `ya29.`, `1//`, `sk-`, `gh[pousr]_`, `AKIA`, `xox[abprs]-`, `refresh_token` 을 잡는다. 걸리면 파일을 지우는 게 아니라 **해당 자격증명을 폐기**해야 한다 — 원본 세션 로그에 영구히 남아 재생성 때마다 다시 나온다.

## 클라우드 예약 작업으로는 안 된다

세션 로그가 로컬에만 있다. ChatGPT/Codex 예약 작업은 클라우드에서 실행되므로 `~/.codex/sessions` 에 접근할 수 없다. 자동화가 필요하면 로컬 스케줄러(launchd, cron)를 쓴다.
