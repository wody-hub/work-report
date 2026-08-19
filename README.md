# work-report

AI 코딩 세션 로그에서 **내가 실제로 지시한 내용**만 뽑아 날짜별로 정리하는 스킬.

Claude Code 와 Codex 는 모든 세션을 로컬에 JSONL 로 남긴다. 그 안에는 업무 지시, AI 응답, 툴 실행 결과가 뒤섞여 있고 용량 대부분은 툴 결과(읽은 파일 전문, 명령 출력, 코드 diff)다. 이 도구는 그중 **사람이 입력한 지시문과 세션 지표만** 추출해서 실적 보고용 데이터로 만든다.

```
2026-08-13/
├── instructions.md      # 07:57 · claude-code · ~acme/backend · feature/login
├── instructions.jsonl   #   "모바일 알림함 목록에 TBM 항목 노출해야해"
├── commits.csv          # 08:22 feat(notification): 모바일 알림함에 TBM 항목 노출
├── agent-tasks.md       # AI 가 하위 에이전트에 넘긴 지시 (집계 제외)
└── sessions.csv         # 그날 세션 34개: 시각·경로·브랜치·토큰·툴호출
```

지시(원인)와 커밋(결과)이 같은 날짜 폴더에 시각과 함께 들어가므로, 둘을 짝지어 볼 수 있다.

---

## 목차

1. [무엇에 쓰는가](#무엇에-쓰는가)
2. [동작 원리](#동작-원리)
3. [사전 요구사항](#사전-요구사항)
4. [설치](#설치)
5. [설정](#설정)
6. [첫 실행](#첫-실행)
7. [일상 워크플로](#일상-워크플로)
8. [출력물 읽는 법](#출력물-읽는-법)
9. [git 커밋 수집](#git-커밋-수집)
10. [집계 기준](#집계-기준)
11. [민감정보 점검](#민감정보-점검)
12. [업로드](#업로드)
13. [자동화](#자동화)
14. [업데이트와 제거](#업데이트와-제거)
15. [트러블슈팅](#트러블슈팅)
16. [FAQ](#faq)

---

## 무엇에 쓰는가

- **실적 근거** — 언제 무엇을 지시했고 그 결과로 무엇을 커밋했는지 날짜별로 남는다. 타임시트, 주간보고, 인사평가 자료의 원자료가 된다.
- **회고** — 특정 기능을 언제 어떤 순서로 진행했는지 지시문 그대로 되짚을 수 있다.
- **용량과 안전성** — 원본 수 GB 가 지시문 기준 수십 MB 로 줄어든다. 툴 결과에 섞인 소스코드·설정파일·시크릿이 빠진다.

### 지원 대상

| 도구 | 로그 위치 |
|---|---|
| Claude Code | `~/.claude/projects/<encoded-cwd>/**.jsonl` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |

지정한 경로(`WORK_TARGETS`) 하위에서 실행된 세션만 모은다. 그 밖의 작업은 제외된다. 회사 프로젝트만 뽑고 개인 작업은 빼는 식으로 쓸 수 있다.

---

## 동작 원리

세션 로그는 대화 한 턴이 JSON 한 줄인 구조다. 여기서 "사람이 준 지시"를 골라내는 게 이 도구의 핵심이고, 생각보다 까다롭다.

### 1. 어느 경로에서 실행된 세션인지 판정

Claude Code 의 프로젝트 디렉토리명은 cwd 를 인코딩한 것이지만 **`/` 와 `-` 를 구분하지 못한다.**

```
~/.claude/projects/-Users-me-Project-acme-api
   → /Users/me/Project/acme/api  인가  /Users/me/Project/acme-api  인가?
```

그래서 디렉토리명을 믿지 않고 **로그 안의 `cwd` 필드**로 판정한다. Codex 는 `session_meta` 의 `cwd` 를 쓴다.

### 2. user 역할이라고 다 사람이 쓴 게 아니다

프로토콜상 user 턴에는 사람 입력 외에도 여러 가지가 들어온다. 실제 어떤 프로젝트에서 user 턴 13,875개를 분류한 결과:

| 종류 | 비율 | 정체 |
|---|---:|---|
| `tool_result` 전용 턴 | 90.3% | Bash/Read 실행 **결과** |
| **실제 사람의 지시** | **7.1%** | 진짜 타이핑한 내용 |
| `isMeta` | 1.8% | 스킬 로딩 시 주입되는 스킬 본문 |
| 슬래시 커맨드 | 0.5% | `<command-name>/clear</command-name>` |
| 중단 표시 | 0.2% | `[Request interrupted by user]` |
| 로컬 커맨드 출력 | 0.1% | `<local-command-stdout>…` |

앞의 것들을 빼지 않으면 지시 건수가 14배 부풀려진다.

### 3. Codex 는 지시문 저장 위치가 버전마다 다르다

```
구형:  event_msg      / user_message           → payload.message
신형:  response_item  / message role=user      → payload.content[].input_text
```

양쪽 다 읽고 `(세션, 텍스트)` 로 중복 제거한다. 한쪽만 보면 특정 시기 데이터가 통째로 빠진다. 실제로 신형 포맷을 놓쳤을 때 어떤 달의 지시가 359건에서 56건으로 집계된 사례가 있다.

### 4. AI 가 만든 지시는 분리한다

멀티 에이전트로 작업하면 AI 가 하위 에이전트에게 작업 지시를 쓴다. 이것도 로그상 user 턴에 들어오지만 **사람의 실적이 아니다.** `agent-tasks.*` 로 분리하고 집계에서 뺀다.

---

## 사전 요구사항

- Claude Code 또는 Codex (둘 다 있으면 양쪽에 등록된다)
- Python 3.8+
- bash, `shasum`, `comm`, `find` — macOS·Linux 기본 포함

Windows 는 WSL 에서 동작한다. 네이티브 지원은 없다.

---

## 설치

```bash
git clone https://github.com/wody-hub/work-report.git ~/work-report
cd ~/work-report && ./setup
```

`setup` 이 하는 일:

1. 설치된 host 감지 (`~/.claude`, `~/.codex`) — 둘 다 있으면 둘 다 등록
2. `~/.claude/skills/work-report` → 저장소의 `skills/work-report` 로 **심링크**
3. 설정 템플릿을 `~/.config/work-report/config.env` 로 복사 (이미 있으면 건드리지 않음)

host 를 명시하려면:

```bash
./setup --host claude
./setup --host codex
```

저장소는 어디에 clone 해도 된다. 심링크 방식이라 `git pull` 하면 스킬이 바로 갱신된다.

### 설치 확인

새 세션을 열고 스킬 목록에 `work-report` 가 보이는지 확인한다. 또는:

```bash
~/.claude/skills/work-report/scripts/run.sh --help
```

---

## 설정

`~/.config/work-report/config.env` 를 열어 두 값을 채운다.

```bash
# 수집 대상 경로. 콜론(:) 구분. 마지막 폴더명이 리포트의 범위 라벨이 된다.
WORK_TARGETS="$HOME/Project/acme:$HOME/Notes/acme-vault"

# 결과를 둘 폴더
WORK_DIR="$HOME/work-report-out"

# 시간대 오프셋 (시간 단위). 9 = KST, 0 = UTC, -8 = PST
WORK_TZ_OFFSET="9"
```

설정 파일은 **저장소 밖에** 있다. `git pull` 로 덮이지 않고, 개인 경로가 커밋될 경로 자체가 없다.

### WORK_TARGETS 정하기

수집 대상 경로 하위에서 실행된 세션만 모인다. 지금 어떤 경로에 세션이 있는지 확인하려면:

```bash
# Claude Code
grep -ho '"cwd":"[^"]*"' ~/.claude/projects/*/*.jsonl 2>/dev/null \
  | sort -u | head -40

# Codex
grep -ho '"cwd":"[^"]*"' ~/.codex/sessions/*/*/*/*.jsonl 2>/dev/null \
  | sort -u | head -40
```

여기서 실적에 넣을 상위 경로들을 골라 콜론으로 이어 붙인다. 경로는 여러 개 지정할 수 있고, 중첩된 경로도 안전하다 (가장 긴 경로가 이긴다).

마지막 폴더명이 라벨이 된다:

```
$HOME/Project/acme          → 라벨 acme,        표기 ~acme
$HOME/Notes/acme-vault      → 라벨 acme-vault,  표기 ~acme-vault
```

### WORK_DIR 정할 때 주의

> **macOS 에서 `~/Documents` 나 `~/Desktop` 을 쓰지 마라.** iCloud Drive 동기화 대상이다. 대용량 폴더를 넣으면 폴더를 재생성할 때마다 ` 2` 가 붙은 충돌 폴더가 생기고, 데이터가 의도 없이 애플 클라우드로 올라간다. 홈 루트 직하나 `~/Project/...` 처럼 동기화 대상이 아닌 곳을 쓴다.

---

## 첫 실행

```bash
~/.claude/skills/work-report/scripts/run.sh
```

또는 세션에서 `/work-report`, 혹은 "업무정리 해줘".

```
[1/4] 세션 로그 수집
스캔: Claude Code / acme …
  세션 346 / 지시 1524
스캔: Codex …
  세션 532 / 사람 지시 4759 / 도구주입·에이전트작업 330

[2/4] 날짜별 정리
날짜 폴더 142개 / 지시 6,102건

[3/4] 올릴 대상
  (업로드 기록 없음 → 전체가 대상)
  전체 날짜 142개 / 올릴 대상 142개
    2025-11-03  지시 22건
    …

[4/4] 민감정보 점검 (올릴 대상만)
    2026-08-11
      AuthKey_XXXXXXXXXX.p8
  1개 날짜에서 일치. 외부 공유 가능한지 확인하세요.

결과
  폴더:  /Users/me/work-report-out/by-date
  로그:  /Users/me/work-report-out/logs/2026-08-18_183722.log

  위 142개 날짜 폴더를 올리세요.
  올린 뒤:  run.sh --mark-uploaded
```

보통 10초 안에 끝난다. 수집은 원본을 읽기만 하고 복사하지 않는다.

### 명령 정리

| 명령 | 동작 |
|---|---|
| `run.sh` | 세션 수집 → 커밋 수집 → 날짜별 정리 → 올릴 대상·민감정보 보고 |
| `run.sh --open` | 위 + 파일 탐색기로 결과 폴더 열기 |
| `run.sh --mark-uploaded` | 올린 뒤 실행. 다음부터 변경분만 보고 |
| `run.sh --with-raw` | 원본 JSONL 까지 복사 (수 GB, **외부 공유 금지**) |
| `run.sh --help` | 사용법 |

---

## 일상 워크플로

하루 작업이 끝날 때 한 번 돌리는 흐름이다.

```
① run.sh
     → "올릴 대상 3개: 08-16, 08-17, 08-18"
     → "민감정보: 08-17 에서 ya29.… 일치"

② 민감정보 확인 (걸린 게 있으면)

③ 해당 날짜 폴더를 드라이브·공유폴더에 업로드

④ run.sh --mark-uploaded
```

`--mark-uploaded` 가 핵심이다. 실행하면 현재 상태를 날짜별 해시로 기록하고, 다음부터는 **새로 생긴 날짜와 내용이 바뀐 날짜만** 알려준다. 매번 전체를 다시 확인할 필요가 없다.

> 오늘 날짜 폴더는 실행할 때마다 바뀐다. 진행 중인 대화가 수집 대상 경로에 있으면 지시가 계속 쌓이기 때문이다. 그래서 **하루 끝에 한 번** 돌리는 편이 깔끔하다.

---

## 출력물 읽는 법

```
$WORK_DIR/
├── export/                 수집 원본 (digest)
│   ├── README.md           도구별·경로별·월별 집계 리포트
│   └── digest/
│       ├── instructions-all.md|jsonl        전체 지시문 (검색용)
│       ├── instructions-claude-<라벨>.*     범위별
│       ├── instructions-codex.*
│       ├── instructions-codex-nonhuman.*    도구 주입분 (집계 제외)
│       ├── sessions-all.csv
│       └── daily-activity.csv
├── by-date/                ← 업로드용
│   ├── index.csv           날짜별 한 줄 요약
│   ├── README.md
│   ├── YYYY-MM-DD/         날짜 폴더 (계층 없음, 이름순 = 시간순)
│   └── _reference/
│       ├── sessions-all.csv      전체 세션 (지시 없는 세션 포함)
│       └── daily-activity.csv    일별 세션·지시·툴호출
└── logs/
```

### index.csv

여기부터 보면 된다. 활동일이 한 줄씩 있다.

| 열 | 내용 |
|---|---|
| 날짜, 요일 | `2026-08-13`, `목` |
| 지시수 | 그날 사람이 준 지시 건수 |
| claude-code, codex | 도구별 분해 |
| 에이전트작업 | AI→하위에이전트 지시 (집계 제외분) |
| 세션수 | 그날 지시가 있었던 세션 수 |
| 커밋수, 추가줄, 삭제줄 | 그날 내 커밋과 변경량 |
| 시작, 종료 | 그날 첫·마지막 활동 시각 |
| 작업대상(상위5) | `~acme/backend(35) / ~acme/front(18) …` |

### 날짜 폴더

| 파일 | 내용 |
|---|---|
| `instructions.md` | 그날 지시 전문, 시간순. 제목이 `시각 · 도구 · 작업디렉토리 · git브랜치` |
| `instructions.jsonl` | 같은 내용 구조화 (`tool`, `scope`, `cwd`, `branch`, `timestamp`, `bucket`, `text`) |
| `agent-tasks.md\|.jsonl` | AI 가 만든 지시 — `agent-task`, `generated` (있는 날만) |
| `commits.csv` | 그날 내 git 커밋 (있는 날만) |
| `sessions.csv` | 그날 지시가 있었던 세션 인덱스 |

### sessions.csv 열

`도구 · 범위 · 시작 · 종료 · 구분 · 실행주체 · cwd · git branch · 세션ID · 지시수 · 응답수 · 툴호출 · 입력토큰 · 출력토큰 · 캐시읽기 · 캐시생성 · 용량KB · 모델 · 원본상대경로 · 첫 지시`

- **구분** — `main`(사람이 직접 대화한 세션) / `subagent`·`sub`(AI 가 띄운 세션)
- **실행주체** — Codex 만 기록됨. `codex-tui`, `codex_exec`, `Claude Code`(다른 AI 가 호출) 등

### 주의할 지표

- **날짜 폴더의 `sessions.csv` 는 그날 지시가 있었던 세션만 담는다.** 사람 지시 없이 실행된 세션(headless 실행, 재개 세션, 다른 AI 가 호출한 세션)은 `_reference/sessions-all.csv` 에만 있다. 툴호출은 상당한데 지시가 0인 세션들이다.
- **Codex 토큰 수치는 Claude 와 직접 비교할 수 없다.** Codex 는 캐시분을 입력 토큰에 누적 포함시킨다. 세션·지시·툴호출 수는 동일 기준이라 비교 가능하다.
- **세션이 자정을 넘긴 경우** 지시는 실제 입력 시각 기준으로 그날에 들어간다. 같은 세션이 이틀에 걸쳐 양쪽 `sessions.csv` 에 나타날 수 있다.

---

## git 커밋 수집

지시문이 "무엇을 시켰는가"라면 커밋은 "무엇이 나왔는가"다. 실적 근거로는 둘을 시각으로 짝지을 수 있을 때 가장 강하다.

`WORK_TARGETS` 하위의 git 저장소를 찾아 **내 커밋만** 뽑는다. worktree 는 본체와 객체를 공유하므로 중복 수집하지 않는다.

```
07:57  지시   "모바일 알림함 목록에 TBM 항목 노출해야해"
08:22  커밋   feat(notification): 모바일 알림함에 TBM 항목 노출   19파일 +355 -1
```

### commits.csv 열

`시각 · 저장소 · 브랜치 · 커밋 · 타입 · 영역 · 제목 · 파일수 · 추가 · 삭제 · 머지`

`타입`·`영역` 은 conventional commit(`feat(notification): …`)에서 파싱한다. 규칙을 안 쓰는 저장소면 비어 있고 `제목` 만 채워진다.

### 설정

```bash
# 내 이메일. 콜론 구분. 비우면 git config --global user.email 사용
WORK_GIT_AUTHORS="me@company.com:me@personal.com"

# 이 날짜 이후만. 비우면 전체 이력
WORK_GIT_SINCE="2025-01-01"

# 저장소 탐색 깊이 (기본 4)
WORK_GIT_DEPTH="4"
```

여러 이메일을 쓴 이력이 있으면 전부 나열한다. 확인:

```bash
git -C <저장소> log --all --pretty='%ae' | sort | uniq -c | sort -rn
```

### 날짜 폴더가 늘어난다

커밋은 AI 도구를 쓰기 전부터 있으므로, 날짜 폴더가 지시 기준보다 훨씬 넓어진다. **AI 없이 커밋만 한 날도 실적이므로 폴더를 만든다.** 그 날 `instructions.md` 는 "업무 지시 0건" 으로 남는다.

기간을 AI 사용 시점 이후로 맞추려면 `WORK_GIT_SINCE` 를 쓴다.

---

## 집계 기준

**사람이 준 지시만 센다.** 아래는 제외하거나 분리한다.

| 제외 대상 | 처리 | 이유 |
|---|---|---|
| 툴 실행 결과 (`tool_result` 전용 턴) | 제외 | 실행 결과이지 지시가 아님 |
| 스킬 본문 주입 (`isMeta`) | 제외 | 시스템이 넣은 문서 |
| 슬래시 커맨드, 중단 표시, 로컬 커맨드 출력 | 제외 | 배관 |
| Codex 환경 컨텍스트 (`<environment_context>` 등) | 제외 | 배관 |
| Codex 도구 주입 (`<skill>`, `<recommended_plugins>` 등) | `instructions-codex-nonhuman.*` | 순수 노이즈 |
| AI → 하위 에이전트 작업지시 | `agent-tasks.*` | 사람 실적 아님 |
| 다른 AI·도구가 생성한 프롬프트 | `agent-tasks.*` | 사람이 안 씀 |

`instructions.jsonl` 의 `bucket` 필드가 `human` 인 것만 집계에 들어간다.

| bucket | 뜻 | 어디에 |
|---|---|---|
| `human` | 사람이 직접 입력 | `instructions.*` |
| `agent-task` | AI 가 하위 에이전트에 넘긴 지시 | `agent-tasks.*` |
| `generated` | 다른 AI·도구가 만들어 넣은 프롬프트 (`# Cross-AI …`, `You are an …`) | `agent-tasks.*` |
| `injected` | 도구가 주입한 스킬 본문 등 | 날짜 폴더에 없음 |

### 길이 상한

프롬프트에 소스코드나 계획서 전문을 붙여넣으면 그게 결과물에 그대로 실린다. 어떤 실측에서 지시문 6,131건 중 코드블록을 포함한 118건(1.9%)이 **전체 용량의 66%** 를 차지했다.

`WORK_MAX_CHARS`(기본 10000)를 넘는 지시문은 잘라내고 `… (총 N자 중 앞부분만)` 을 붙인다. 무엇을 지시했는지는 앞부분에 담기므로 실적 판단에는 지장이 없고, 코드 유출 면적이 줄어든다. `0` 으로 두면 무제한.

---

## 민감정보 점검

올릴 날짜의 지시문에서 아래 패턴을 찾아 보여준다.

`ssh-rsa` · `BEGIN … PRIVATE KEY` · `AuthKey_*.p8` · `ya29.…`(Google OAuth) · `1//…`(refresh token) · `sk-…` · `gh[pousr]_…` · `AKIA…`(AWS) · `xox[abprs]-…`(Slack) · `refresh_token` · `password=` · `secret=` · `api_key=` · `Bearer …` · `sshpass` · `sudo su` · `ssh user@…` · IP 주소

한글 문맥도 잡는다 — `비밀번호` · `패스워드` · `비번` · `암호는` · `접속정보` · `계정정보`. 영어 패턴만 쓰면 "sudo 비밀번호는 xxx 야" 같은 지시를 통째로 놓친다.

IP 는 버전번호·좌표와도 겹쳐 오탐이 섞인다. 토큰·키 패턴은 오탐이 드물다.

### 걸렸을 때

**파일을 지우는 건 답이 아니다.** 원본 세션 로그에 영구히 남아 있어서 재생성할 때마다 다시 나온다.

1. **해당 자격증명을 폐기한다** — 토큰 회수, 키 로테이션, 비밀번호 변경. 그러면 그 문자열은 의미 없는 텍스트가 된다.
2. 그다음 올린다.

채팅창에 토큰이나 비밀번호를 붙여넣으면 그게 세션 로그에 남고, 이 도구가 그걸 다시 꺼내온다. 애초에 붙여넣지 않는 게 최선이다.

---

## 업로드

**업로드 기능은 의도적으로 없다.** 검토 없이 외부로 나가면 되돌릴 수 없기 때문이다.

`by-date/` 의 해당 날짜 폴더를 원하는 곳에 직접 올린다. 날짜 폴더가 계층 없이 최상위에 이름순으로 있어서 그대로 드래그하면 된다.

| 방법 | 비고 |
|---|---|
| 웹 브라우저 드래그 | 가장 단순. 폴더 수가 많으면 번거로움 |
| Google Drive for Desktop 등 | 마운트된 폴더에 `cp`. 실제 업로드는 앱이 백그라운드로 하므로 성공 여부를 스크립트가 알 수 없음 |
| `rclone copy` | 증분·체크섬·종료코드 확인 가능. 별도 설치와 OAuth 필요 |

올린 뒤 `run.sh --mark-uploaded` 를 잊지 말 것.

---

## 자동화

**클라우드 예약 작업으로는 안 된다.** 세션 로그가 로컬에만 있어서, 클라우드에서 도는 예약 작업(ChatGPT/Codex 예약 등)은 `~/.codex/sessions` 에 접근할 수 없다.

로컬 스케줄러를 쓴다. macOS launchd 예시 — 평일 오후 6시에 데이터만 생성:

`~/Library/LaunchAgents/com.local.work-report.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.local.work-report</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>$HOME/.claude/skills/work-report/scripts/run.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>18</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>18</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>18</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>18</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer></dict>
  </array>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.local.work-report.plist
```

데이터 생성까지만 자동화하고, **민감정보 확인과 업로드는 사람이 하는 구조를 유지하는 게 안전하다.**

Linux 는 cron 으로:

```
0 18 * * 1-5 $HOME/.claude/skills/work-report/scripts/run.sh
```

---

## 업데이트와 제거

```bash
cd ~/work-report && git pull && ./setup
```

심링크 방식이라 `git pull` 만으로 반영되지만, 스킬 구성이 바뀐 경우가 있어 `./setup` 재실행이 안전하다. 설정 파일은 덮어쓰지 않는다.

제거:

```bash
rm ~/.claude/skills/work-report ~/.codex/skills/work-report   # 심링크만 삭제
rm -rf ~/work-report                                          # 저장소
rm -rf ~/.config/work-report                                  # 설정
```

산출물(`WORK_DIR`)은 직접 지운다.

---

## 트러블슈팅

**`설정 파일이 없습니다`**
`./setup` 을 실행하지 않았거나 경로가 다르다. `~/.config/work-report/config.env` 존재를 확인한다. 다른 위치를 쓰려면 `WORK_REPORT_CONFIG=/path/to/config.env run.sh`.

**`대상 경로에서 실행된 세션을 찾지 못했습니다`**
`WORK_TARGETS` 경로가 실제 세션의 cwd 와 다르다. [WORK_TARGETS 정하기](#work_targets-정하기) 의 `grep` 으로 실제 cwd 목록을 확인한다. 경로 끝 `/` 는 있어도 없어도 된다.

**지시 건수가 예상보다 많다**
`agent-tasks.*` 를 섞어 보고 있을 수 있다. `index.csv` 의 `지시수` 열이 사람 지시이고 `에이전트작업` 은 별도다.

**Codex 특정 기간 지시가 0으로 나온다**
Codex 버전에 따라 저장 위치가 달라서 생기는 문제인데, 이 도구는 양쪽을 다 읽는다. 그래도 0 이면 해당 세션이 headless(`codex_exec`) 실행이라 사람 입력이 없는 경우다. `_reference/sessions-all.csv` 의 `실행주체` 열로 확인한다.

**날짜 폴더에 ` 2` 가 붙은 빈 폴더가 생긴다**
`WORK_DIR` 가 iCloud 동기화 폴더(`~/Documents`, `~/Desktop`)다. 동기화되지 않는 경로로 옮긴다.

**변경된 날짜를 못 잡는다**
`--mark-uploaded` 를 실행하지 않아 기준 상태가 없거나, 반대로 실제로 안 올렸는데 기록해버린 경우다. `$WORK_DIR/.last-upload.sha256` 을 지우면 전체가 다시 대상이 된다.

**한글 폴더명 경로가 깨진다**
설정 파일을 UTF-8 로 저장했는지 확인한다. 경로에 공백이 있으면 따옴표로 감싼다.

---

## FAQ

**원본 로그를 지워도 되나?**
이 도구는 원본을 읽기만 한다. 하지만 원본을 지우면 과거 데이터를 다시 만들 수 없다. 산출물만 백업하고 원본을 지우는 건 되돌릴 수 없으니 신중하게.

**`--with-raw` 는 언제 쓰나?**
"그날 실제로 어떤 코드가 어떻게 바뀌었나"까지 봐야 할 때만. 원본에는 툴 실행 결과로 읽힌 소스코드 전문, 설정파일, 터미널에 출력된 시크릿이 그대로 들어있다. **외부 공유용으로 쓰지 마라.**

**팀원 여러 명의 실적을 합칠 수 있나?**
각자 자기 맥에서 실행해 산출물을 모으는 방식만 가능하다. 세션 로그는 각 개인 머신에만 있다.

**여러 회사·프로젝트를 분리하고 싶다**
`WORK_TARGETS` 에 회사 경로만 넣으면 개인 작업은 제외된다. 완전히 분리하려면 설정 파일을 따로 만들어 `WORK_REPORT_CONFIG` 로 지정한다.

**Cursor, Copilot 등 다른 도구도 되나?**
현재는 Claude Code 와 Codex 만. 로그 포맷이 도구마다 달라서 각각 파서가 필요하다.

**지시문을 수정해서 올리고 싶다**
`by-date/` 는 매 실행마다 재생성되므로 직접 고치면 사라진다. 별도 폴더에 복사해서 편집한다.

---

## 라이선스

MIT
