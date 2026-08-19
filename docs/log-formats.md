# 세션 로그 포맷 대응 지시서

이 문서는 **Claude Code 와 Codex 의 세션 로그 포맷이 버전마다 다르다는 사실**과, `work-report` 가 각각을 어떻게 읽는지 정리한다. 다른 사람이 이 도구를 쓰거나 고칠 때, 그리고 새 포맷이 등장했을 때 참고한다.

핵심 교훈부터: **포맷이 바뀌면 도구는 크래시하지 않는다. 조용히 일부만 담는다.** 그래서 커버리지 자가 보고가 있다.

---

## 1. Codex 는 포맷이 두 가지다

실측 (2026-08 기준, 세션 1,068개):

| | 구형 | 신형 |
|---|---|---|
| 세션 수 | 36개 | 1,032개 |
| 관측 기간 | 2025-09-05 ~ 2025-11-17 | 2025-11-03 ~ 현재 |
| 첫 줄 | `{id, timestamp, instructions, git}` | `{type: "session_meta", payload: {...}}` |
| 레코드 위치 | **최상위** | `payload` 안 |
| 레코드 타입 | `message`, `function_call`, `reasoning`, `state` | `response_item/*`, `event_msg/*`, `turn_context` |

전환기(2025-11-03 ~ 11-17)에는 두 포맷이 섞여 있다. 날짜로 판별하면 안 되고 **파일 내용으로 판별해야 한다.**

### 판별 방법

```python
# 앞 26줄 안에 type == "session_meta" 가 있으면 신형, 없으면 구형
```

`collect_sessions.py` 의 `codex_meta()` 가 이 판정을 하고, 구형이면 헤더에서 메타를 복원한다.

---

## 2. 필드가 어디에 있는가

### cwd — 어느 경로에서 실행됐는지

수집 대상을 가르는 가장 중요한 값이다.

| 포맷 | 위치 |
|---|---|
| 신형 | `session_meta.payload.cwd` |
| 구형 | **없다.** 첫 user 메시지의 `<environment_context><cwd>…</cwd>` 에서 추출 |
| Claude Code | 각 레코드의 `cwd` 필드 |

> **Claude Code 의 프로젝트 디렉토리명을 cwd 로 쓰면 안 된다.** `~/.claude/projects/-Users-me-Project-acme-api` 는 `/Users/me/Project/acme/api` 인지 `/Users/me/Project/acme-api` 인지 구분할 수 없다. `/` 와 `-` 가 같은 문자로 인코딩된다. 반드시 로그 안의 `cwd` 필드로 판정한다.

### 사람이 입력한 지시

| 포맷 | 위치 |
|---|---|
| 신형 (최근) | `response_item` → `payload.type == "message"`, `role == "user"`, `content[].input_text` |
| 신형 (초기) | `event_msg` → `payload.type == "user_message"`, `payload.message` |
| 구형 | 최상위 `type == "message"`, `role == "user"`, `content[].input_text` |
| Claude Code | `type == "user"`, `message.content[].text` |

**신형에서도 두 형태가 공존한다.** 한쪽만 읽으면 특정 시기 데이터가 통째로 빠진다. 실제로 신형 형태를 놓쳤을 때 어떤 달의 지시가 359건에서 56건으로 집계된 적이 있다. 양쪽을 다 읽고 **정규화한 전문**으로 중복을 제거한다.

> 접두어(앞 400자)로 중복 판정하면 안 된다. 서두가 같은 템플릿으로 시작하는 긴 지시(계획서 재검토 요청 등)가 서로 다른 내용인데도 하나만 남는다. 실측 698건이 이렇게 사라졌다.

### 시각

| 포맷 | 위치 |
|---|---|
| 신형 | 각 레코드의 `timestamp` (UTC, `Z`) |
| 구형 | **레코드에 없다.** 헤더의 `timestamp` 로 채운다 |
| Claude Code | 각 레코드의 `timestamp` (UTC, `Z`) |

구형은 세션 시작 시각으로 채우고 `ts_source: "session"` 으로 표시한다. 세션이 자정을 넘겼다면 날짜가 하루 어긋날 수 있다는 뜻이다. 채우지 않으면 그 지시들이 어떤 날짜 폴더에도 들어가지 못한다 (실측 183건).

산출물의 모든 시각은 `WORK_TZ_OFFSET` 을 적용한 ISO 8601(오프셋 포함)로 통일한다. 로그 원문(UTC)을 그대로 두면 같은 폴더 안에서 지시와 커밋이 9시간 어긋난다.

### 응답 수

| 포맷 | 위치 |
|---|---|
| 신형 | `payload.type == "message"` + `role == "assistant"` (주) / `payload.type == "agent_message"` (보조) |
| 구형 | 최상위 `type == "message"` + `role == "assistant"` |

**두 형태가 같은 턴을 가리킬 수 있다.** 더하면 중복 계산되므로 각각 세고 **큰 쪽**을 쓴다.

### 모델명

| 포맷 | 위치 |
|---|---|
| 신형 | **최상위** `type == "turn_context"` → `payload.model` |
| 구형 | 기록되지 않는다 |
| Claude Code | `message.model` |

`turn_context` 는 최상위 타입이고 `payload` 에는 `type` 키가 없다. `payload.type == "turn_context"` 로 비교하면 영원히 매칭되지 않는다 — 실제로 이 실수로 567개 세션 전부 모델이 비어 있었다.

### 그 밖

| 항목 | 신형 | 구형 |
|---|---|---|
| git 브랜치 | `session_meta.payload.git.branch` | 헤더 `git.branch` (구형이 오히려 채워짐: 32/35 vs 217/532) |
| 실행 주체 | `session_meta.payload.originator` | 없음 |
| 토큰 사용량 | `payload.type == "token_count"` → `info.total_token_usage` | 기록되지 않는다 |
| 툴 호출 | `payload.type` 이 `function_call`·`custom_tool_call`·`local_shell_call` | 최상위 동일 타입 |

---

## 3. 구형에서 무엇을 잃는가

실측 (구형 35개 vs 신형 532개, 세션당 평균):

| 지표 | 구형 | 신형 | 비고 |
|---|---:|---:|---|
| 지시 수 | 5.2 | 9.3 | 정상 수집 |
| 툴 호출 | 34.1 | 125.4 | 정상 수집 |
| 응답 수 | 수집됨 | 수집됨 | 양쪽 대응 후 |
| 토큰 | **0** | 1,046만 | 구형은 기록 자체가 없음 |
| 모델 | **없음** | 있음 | 구형은 기록 자체가 없음 |
| 실행 주체 | **없음** | 있음 | 구형은 기록 자체가 없음 |
| git 브랜치 | 32/35 | 217/532 | 구형이 더 잘 채워짐 |

**지시문과 코드 변경은 온전히 수집된다.** 잃는 것은 토큰·모델·실행주체 같은 부가 지표이고, 이는 로그에 애초에 없어서 복구할 방법이 없다.

---

## 4. 새 포맷이 나오면

포맷 변경은 조용히 데이터를 깎는다. 순서대로 확인한다.

### ① 커버리지 보고를 본다

```
[1/6] 세션 로그 수집
  누락: codex_메타없음 12, claude_cwd_판정실패_디렉토리 1
```

이 줄이 뜨면 새 포맷이 등장했다는 뜻이다. 0 이면 출력되지 않는다.

`_reference/coverage.json` 에도 기록된다.

### ② 실제 구조를 확인한다

```bash
# 판정 실패한 세션의 레코드 타입 분포
python3 - <<'PY'
import json, glob, os
from collections import Counter
c = Counter()
for f in glob.glob(os.path.expanduser('~/.codex/sessions/**/*.jsonl'), recursive=True):
    rs = []
    with open(f, encoding='utf-8', errors='replace') as fh:
        for line in fh:                       # 깨진 줄이 섞여 있을 수 있다
            line = line.strip()
            if not line:
                continue
            try:
                rs.append(json.loads(line))
            except Exception:
                pass
    if any(isinstance(o, dict) and o.get('type') == 'session_meta' for o in rs[:26]):
        continue                              # 알려진 신형
    for o in rs:
        if not isinstance(o, dict):
            continue
        p = o.get('payload')
        c[f"{o.get('type')}/{p.get('type') if isinstance(p, dict) else '-'}"] += 1
print(c.most_common(15))
PY
```

### ③ 세 곳을 확인한다

새 포맷 대응은 거의 항상 이 세 가지다.

1. **메타 판정** — `codex_meta()` 에 새 헤더 형태 추가
2. **레코드 위치** — 최상위인가 `payload` 안인가. `p = o.get("payload") or o` 로 양쪽 대응
3. **필드 이름** — 위 표의 각 항목이 어디로 옮겨갔는지

### ④ 지표가 0 인지 확인한다

수집은 됐는데 특정 열이 전부 비어 있으면 필드 위치가 바뀐 것이다.

```bash
python3 - <<'PY'
import csv, io, os
from collections import Counter
p = os.path.expanduser('~/work-report-out/by-date/_reference/sessions-all.csv')
rows = list(csv.DictReader(io.StringIO(open(p, encoding='utf-8-sig').read())))
for col in ['응답수', '툴호출', '입력토큰', '모델', 'git branch', '실행주체']:
    empty = sum(1 for r in rows if not r[col] or r[col] == '0')
    print(f"{col:<12} 비어있음 {empty}/{len(rows)}")
PY
```

전부 비어 있으면 버그, 일부만 비어 있으면 로그에 원래 없는 것이다.

---

## 5. Claude Code 포맷

Codex 보다 안정적이었다. 관측 기간 내 포맷 변경 없음.

```
~/.claude/projects/<인코딩된-cwd>/<session-uuid>.jsonl
~/.claude/projects/<인코딩된-cwd>/<session-uuid>/<sub-uuid>.jsonl   ← 서브에이전트
```

| 항목 | 위치 |
|---|---|
| cwd | 레코드의 `cwd` |
| 지시 | `type == "user"`, `message.content[].text` |
| 응답 | `type == "assistant"` |
| 툴 호출 | `message.content[].type == "tool_use"` |
| 토큰 | `message.usage.{input,output,cache_read_input,cache_creation_input}_tokens` |
| 모델 | `message.model` |
| git 브랜치 | 레코드의 `gitBranch` |

### 사람이 쓴 게 아닌 user 턴

`type == "user"` 라고 다 사람이 쓴 게 아니다. 실측 13,875개 중 **사람의 지시는 7.1%** 였다.

| 종류 | 비율 | 판별 |
|---|---:|---|
| 툴 실행 결과 | 90.3% | `content[].type == "tool_result"` 만 있는 턴 |
| **사람의 지시** | **7.1%** | 아래 어디에도 해당하지 않는 것 |
| 스킬 본문 주입 | 1.8% | `isMeta == true` |
| 슬래시 커맨드 | 0.5% | `<command-name>` 으로 시작 |
| 중단 표시 | 0.2% | `[Request interrupted` 으로 시작 |
| 로컬 커맨드 출력 | 0.1% | `<local-command-stdout>` 으로 시작 |

하네스가 끼워 넣는 태그도 제외한다: `<task-notification>`, `<planning_context>`, `<revision_context>`, `<pattern_mapping_context>`, `<user_action>`, `<user_shell_command>`, `<bash-input>`, `<bash-stdout>`, `<system-reminder>`.

`<image …>` 는 **제외하지 않는다.** 사람이 이미지를 붙여넣은 지시라서 본문이 사람 것이다.

### 서브에이전트

세션 파일이 하위 디렉토리에 있으면 서브에이전트다 (`os.path.relpath(f, project_dir).count(os.sep) > 0`). 그 세션의 지시는 AI 가 쓴 것이므로 `bucket: "agent-task"` 로 분리하고 실적 집계에서 뺀다.

---

## 6. 요약 — 이 도구가 대응하는 것

| 대응 | 이유 |
|---|---|
| 내용으로 포맷 판별 | 전환기에 두 포맷이 섞인다 |
| `payload` 유무 양쪽 처리 | 구형은 최상위, 신형은 `payload` 안 |
| 지시문 출처 두 곳 다 읽기 | 신형 안에서도 두 형태가 공존 |
| 전문으로 중복 제거 | 접두어 비교는 서로 다른 지시를 지운다 |
| 시각을 로컬 ISO 로 통일 | 지시와 커밋이 어긋나면 상관관계가 깨진다 |
| 없는 시각을 세션 시각으로 채움 | 안 채우면 날짜 폴더에 못 들어간다 |
| 응답 수는 두 형태의 최대값 | 더하면 중복 계산 |
| 못 담은 것을 매번 보고 | 조용한 누락이 가장 위험하다 |
