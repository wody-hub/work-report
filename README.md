# work-report

AI 코딩 세션 로그에서 **내가 실제로 지시한 내용**만 뽑아 날짜별로 정리하는 스킬.

Claude Code 와 Codex 는 모든 세션을 로컬에 JSONL 로 남긴다. 이 안에는 업무 지시, AI 응답, 툴 실행 결과가 뒤섞여 있고, 용량 대부분은 툴 결과(읽은 파일 전문, 명령 출력)다. 이 도구는 그중 **사람이 입력한 지시문과 세션 지표만** 추출해서 실적 보고용 데이터로 만든다.

```
2026-08-13/
├── instructions.md      # 07:57 · claude-code · ~acme/backend · feature/login
├── instructions.jsonl   #   "모바일 알림함 목록에 TBM 항목 노출해야해"
├── agent-tasks.md       # AI 가 하위 에이전트에 넘긴 지시 (집계 제외)
└── sessions.csv         # 그날 세션 34개: 시각·경로·브랜치·토큰·툴호출
```

## 왜 필요한가

- **실적 근거** — 언제 무엇을 지시했는지 날짜별로 남는다. 타임시트나 주간보고의 원자료가 된다.
- **회고** — 특정 기능을 언제 어떤 순서로 진행했는지 지시문 그대로 되짚을 수 있다.
- **용량** — 원본 1.6GB 가 지시문 기준 20MB 로 줄어든다. 툴 결과에 섞인 소스코드·시크릿이 빠진다.

## 지원 대상

| 도구 | 로그 위치 |
|---|---|
| Claude Code | `~/.claude/projects/<encoded-cwd>/**.jsonl` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |

지정한 경로(`WORK_TARGETS`) 하위에서 실행된 세션만 모은다. 그 밖의 작업은 제외된다.

## 사전 요구사항

- Claude Code 또는 Codex
- Python 3.8+ / bash / `shasum`

## 설치

```bash
git clone https://github.com/wody-hub/work-report.git ~/work-report
cd ~/work-report && ./setup
```

`setup` 은 설치된 host 를 감지해 `~/.claude/skills/work-report` (또는 `~/.codex/skills/`) 로 심링크하고, 설정 템플릿을 `~/.config/work-report/config.env` 에 복사한다. host 를 명시하려면 `./setup --host claude` 또는 `./setup --host codex`.

### 설정

`~/.config/work-report/config.env` 를 열어 두 값을 채운다.

```bash
# 수집 대상 경로. 콜론 구분. 마지막 폴더명이 범위 라벨이 된다.
WORK_TARGETS="$HOME/Projects/acme:$HOME/Notes/acme-vault"

# 결과를 둘 폴더
WORK_DIR="$HOME/work-report-out"
```

> macOS 에서 `WORK_DIR` 를 `~/Documents` 나 `~/Desktop` 으로 잡지 마라. iCloud 동기화 대상이라 대용량 폴더를 넣으면 ` 2` 가 붙은 충돌 폴더가 생기고, 데이터가 의도 없이 클라우드로 나간다.

설정 파일은 저장소 밖에 있고 `.gitignore` 에도 올라가 있어서 개인 경로가 커밋되지 않는다.

## 사용법

새 세션에서:

```
/work-report
```

또는 직접:

```bash
~/.claude/skills/work-report/scripts/run.sh
```

| 명령 | 동작 |
|---|---|
| `run.sh` | 수집 → 날짜별 정리 → 올릴 대상·민감정보 보고 |
| `run.sh --open` | 위 + 파일 탐색기로 결과 폴더 열기 |
| `run.sh --mark-uploaded` | 올린 뒤 실행. 다음부터 변경분만 보고 |
| `run.sh --with-raw` | 원본 JSONL 까지 복사 (수 GB, 외부 공유 금지) |

### 워크플로

```
run.sh              → "올릴 대상 3개: 08-16, 08-17, 08-18"
                      "민감정보: 08-17 에서 ya29.… 일치"
(확인 후 직접 업로드)
run.sh --mark-uploaded
```

`--mark-uploaded` 가 핵심이다. 이걸 실행하면 현재 상태를 해시로 기록하고, 다음부터는 **새로 생긴 날짜와 내용이 바뀐 날짜만** 알려준다.

## 출력

```
$WORK_DIR/
├── export/                 수집 원본 (digest)
│   ├── README.md           도구별·경로별·월별 집계 리포트
│   └── digest/             통합 지시문, 세션 CSV, 일별 활동
├── by-date/                ← 업로드용
│   ├── index.csv           날짜별 한 줄 요약
│   ├── README.md
│   ├── YYYY-MM-DD/         날짜 폴더 (계층 없음)
│   └── _reference/         지시 없는 세션 포함 전체 인덱스
└── logs/
```

`index.csv` 열: `날짜 · 요일 · 지시수 · claude-code · codex · 에이전트작업 · 세션수 · 시작 · 종료 · 작업대상(상위5)`

## 집계 기준

**사람이 준 지시만 센다.** 아래는 별도로 분리하거나 제외한다.

| 제외 대상 | 처리 |
|---|---|
| 툴 실행 결과 (`tool_result` 전용 턴) | 제외 |
| 스킬 본문 주입 (`isMeta`) | 제외 |
| 슬래시 커맨드, 중단 표시, 로컬 커맨드 출력 | 제외 |
| Codex 도구 주입 (`<skill>`, `<recommended_plugins>` 등) | `instructions-codex-nonhuman.*` |
| AI → 하위 에이전트 작업지시 | `agent-tasks.*` (날짜별 폴더 안) |

## 민감정보 점검

올릴 날짜의 지시문에서 아래 패턴을 찾아 보여준다.

`ssh-rsa` · `BEGIN … PRIVATE KEY` · `AuthKey_*.p8` · `ya29.…`(Google OAuth) · `1//…`(refresh token) · `sk-…` · `gh[pousr]_…` · `AKIA…` · `xox[abprs]-…` · `refresh_token` · `password=` · `secret=` · `api_key=` · `Bearer …` · IP 주소

IP 는 버전번호·좌표와도 겹쳐 오탐이 섞인다. 토큰·키 패턴은 오탐이 드물다.

**걸렸을 때는 파일을 지우는 게 답이 아니다.** 원본 세션 로그에 영구히 남아 재생성할 때마다 다시 나온다. 해당 자격증명 자체를 폐기해야 한다.

## 알려진 제약

- **클라우드 예약 작업으로 자동화할 수 없다.** 세션 로그가 로컬에만 있어서, 클라우드에서 도는 예약 작업은 `~/.codex/sessions` 에 접근하지 못한다. 자동화하려면 로컬 스케줄러(launchd, cron)를 쓴다.
- **오늘 날짜 폴더는 매 실행마다 바뀐다.** 진행 중인 대화가 수집 대상 경로에 있으면 계속 쌓이므로, 하루 끝에 한 번 돌리는 편이 낫다.
- **업로드 기능은 없다.** 의도적이다. 검토 없이 외부로 나가면 되돌릴 수 없다.

## 업데이트

```bash
cd ~/work-report && git pull && ./setup
```

심링크 방식이라 `git pull` 만으로도 반영되지만, 스킬 구성이 바뀐 경우가 있어 `./setup` 재실행이 안전하다.

## 라이선스

MIT
