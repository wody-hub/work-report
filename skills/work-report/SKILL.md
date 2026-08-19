---
name: work-report
description: Use when the user wants their AI coding session history (Claude Code, Codex) turned into a dated work log for performance reporting or timesheet evidence - triggers include "업무정리", "업무 실적", "실적 데이터", "세션 취합", "일자별 정리", "work report", "session history export". Do not use for reading session logs without producing the dated report.
---

# Work Report

로컬 AI 코딩 세션 로그에서 **사람이 준 업무 지시**만 뽑아 날짜별 폴더로 정리한다. 업로드는 하지 않는다 — 사용자가 직접 올린다.

## 실행

```bash
scripts/run.sh                   # 수집 + 정리 + 보고
scripts/run.sh --open            # + 파일 탐색기로 결과 폴더 열기
scripts/run.sh --mark-uploaded   # 올린 뒤 실행. 다음부터 변경분만 보고
```

5단계: 세션 수집 → git 커밋 수집 → 날짜별 정리 → 변경분 비교 → 민감정보 점검.

스킬 디렉토리 기준 상대 경로다. 사용자가 "업무정리 해줘"라고 하면 그냥 실행하고 결과를 보고하라. 수집은 보통 10초 안에 끝난다.

설정이 없으면 `run.sh` 가 안내와 함께 종료한다. `~/.config/work-report/config.env` 에서 `WORK_TARGETS`(수집 대상 경로, 콜론 구분)와 `WORK_DIR`(결과 폴더)를 채워야 한다.

## 단계

| 단계 | 내용 |
|---|---|
| 1 | `collect_sessions.py` — `~/.claude/projects` + `~/.codex/sessions` 스캔, 대상 경로에서 실행된 세션만 추출 |
| 2 | `collect_commits.py` — 대상 경로의 git 저장소에서 내 커밋 수집 (지시의 결과물) |
| 3 | `split_by_date.py` — `YYYY-MM-DD/` 폴더로 분류 (계층 없음) |
| 4 | 지난 업로드 기록과 비교 → 신규·변경 날짜 목록 |
| 5 | 민감정보 패턴 점검 (올릴 날짜만) |

## 보고할 내용

- 올릴 날짜 목록과 각 날짜의 지시 건수
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
    commits.csv            그날 내 git 커밋 — 지시의 결과물
    agent-tasks.md|.jsonl  AI 가 만든 지시 (집계 제외분)
    sessions.csv
  _reference/              지시 없는 세션 포함 전체 인덱스, 일별 툴호출
```

## 알아둘 것

- **지시(원인)와 커밋(결과)을 시각으로 짝지어 읽어라.** 실적 근거는 "무엇을 지시했다"보다 "지시 → 커밋" 연결이 강하다.
- **집계 대상은 `bucket == human` 뿐이다.** `generated`(다른 AI 가 만든 프롬프트)와 `agent-task` 는 `agent-tasks.*` 로 분리된다. 실적 숫자를 말할 때 섞지 마라.
- **Codex 지시문 출처가 두 곳이다.** 구형은 `event_msg/user_message`, 신형은 `response_item/message:user`. 둘 다 읽고 중복 제거한다. 한쪽만 보면 특정 시기 데이터가 통째로 빠진다.
- **Claude 프로젝트 디렉토리명을 믿지 마라.** cwd 를 인코딩한 이름이라 `/` 와 `-` 를 구분하지 못한다. 로그 안의 `cwd` 필드로 판정한다.
- **Codex 토큰 수치는 Claude 와 비교 불가.** 캐시분이 입력 토큰에 누적 포함된다. 세션·지시·툴호출 수는 동일 기준.
- **오늘 날짜 폴더는 매 실행마다 바뀐다.** 진행 중인 대화가 수집 대상 경로에 있으면 계속 쌓인다. 하루 끝에 한 번 돌리는 게 깔끔하다.
- **`~/Documents`, `~/Desktop` 을 `WORK_DIR` 로 쓰지 마라.** macOS iCloud 동기화 대상이라 대용량 폴더를 넣으면 ` 2` 충돌 폴더가 생기고 데이터가 클라우드로 나간다.
- **채팅에 붙여넣은 자격증명은 그날 `instructions.md` 에 그대로 들어간다.** 대화 자체가 수집 대상 경로에서 진행되면 토큰·비밀번호가 결과물에 실린다. 민감정보 점검이 `ya29.`, `1//`, `sk-`, `gh[pousr]_`, `AKIA`, `xox[abprs]-`, `refresh_token` 을 잡는다. 걸리면 파일을 지우는 게 아니라 **해당 자격증명을 폐기**해야 한다 — 원본 세션 로그에 영구히 남아 재생성 때마다 다시 나온다.

## 클라우드 예약 작업으로는 안 된다

세션 로그가 로컬에만 있다. ChatGPT/Codex 예약 작업은 클라우드에서 실행되므로 `~/.codex/sessions` 에 접근할 수 없다. 자동화가 필요하면 로컬 스케줄러(launchd, cron)를 쓴다.
