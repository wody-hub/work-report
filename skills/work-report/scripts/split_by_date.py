#!/usr/bin/env python3
"""collect_sessions.py 의 digest 를 날짜별 폴더로 분류한다.

사용법:
  WORK_TARGETS="/path/a:/path/b" split_by_date.py <수집경로> <출력경로>

출력 구조 (계층 없음 — 날짜 폴더가 최상위):
  <OUT>/index.csv                 날짜별 한 줄 요약
  <OUT>/YYYY-MM-DD/
      instructions.md             그날 사람이 준 업무 지시 전문 (시간순)
      instructions.jsonl
      agent-tasks.md|.jsonl       AI 가 만든 지시 — agent-task, generated (있는 날만)
      commits.csv                 그날 내 git 커밋 (있는 날만)
      sessions.csv                그날 지시가 있었던 세션 인덱스
  <OUT>/_reference/               날짜 폴더로 재현되지 않는 것만
      sessions-all.csv            전체 세션 (지시 없는 세션 포함)
      daily-activity.csv          일별 세션·지시·툴호출
"""
import csv
import glob
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=float(os.environ.get("WORK_TZ_OFFSET", "9"))))
WEEKDAYS = "월화수목금토일"


def load_targets():
    raw = os.environ.get("WORK_TARGETS", "").strip()
    if not raw:
        sys.exit("WORK_TARGETS 가 비어 있습니다. ~/.config/work-report/config.env 를 확인하세요.")
    out, seen = [], Counter()
    for p in raw.split(":"):
        p = os.path.expanduser(p.strip().rstrip("/"))
        if not p:
            continue
        base = re.sub(r"[^\w.-]", "_", os.path.basename(p)) or "root"
        seen[base] += 1
        out.append((base if seen[base] == 1 else f"{base}{seen[base]}", p))
    return out


TARGETS = load_targets()
POS = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(POS) < 2:
    sys.exit("사용법: split_by_date.py <수집경로> <출력경로> [--only=YYYY-MM-DD,…]")
SRC = os.path.abspath(os.path.expanduser(POS[0]))
OUT = os.path.abspath(os.path.expanduser(POS[1]))

# 특정 날짜만 갱신 (당일 운영용). 지정하면 다른 날짜 폴더는 건드리지 않는다.
ONLY = set()
for a in sys.argv[1:]:
    if a.startswith("--only="):
        ONLY |= {d.strip() for d in a[len("--only="):].split(",") if d.strip()}
if not ONLY and os.environ.get("WORK_ONLY_DATES", "").strip():
    ONLY |= {d.strip() for d in os.environ["WORK_ONLY_DATES"].split(",") if d.strip()}


def short(cwd):
    for label, path in sorted(TARGETS, key=lambda t: -len(t[1])):
        if cwd == path or cwd.startswith(path + "/"):
            return cwd.replace(path, f"~{label}", 1)
    return cwd


def kst_dt(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None


# ---------------------------------------------------------------- 입력
dig = os.path.join(SRC, "digest")
if not os.path.isdir(dig):
    sys.exit(f"digest 폴더가 없습니다: {dig}\n  먼저 collect_sessions.py 를 실행하세요.")

instructions = [json.loads(l) for l in
                open(os.path.join(dig, "instructions-all.jsonl"), encoding="utf-8")]

# 커밋은 있을 때만 (collect_commits.py 를 돌렸거나 git 저장소가 있는 경우)
commits_by_date = defaultdict(list)
_cf = os.path.join(dig, "commits.jsonl")
if os.path.exists(_cf):
    for l in open(_cf, encoding="utf-8"):
        c = json.loads(l)
        commits_by_date[c["date"]].append(c)

COMMIT_COLS = ["시각", "저장소", "브랜치", "커밋", "타입", "영역", "제목",
               "파일수", "추가", "삭제", "머지"]


def write_commits(path, rows):
    rows = sorted(rows, key=lambda c: c["timestamp"])
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(COMMIT_COLS)
        for c in rows:
            w.writerow([c["time"], c["repo"], c["branch"], c["hash"], c["type"],
                        c["conv_scope"], c["subject"], c["files"],
                        c["insertions"], c["deletions"],
                        "Y" if c["is_merge"] else ""])

with open(os.path.join(dig, "sessions-all.csv"), encoding="utf-8-sig") as fh:
    rd = csv.reader(fh)
    sess_header = next(rd)
    sess_rows = list(rd)
SID = sess_header.index("세션ID")
sess_by_id = {}
for r in sess_rows:
    sess_by_id.setdefault(r[SID], r)

# ---------------------------------------------------------------- 날짜별 분류
by_date = defaultdict(list)
undated = []
for p in instructions:
    dt = kst_dt(p.get("timestamp"))
    (undated if dt is None else by_date[dt.strftime("%Y-%m-%d")]).append(
        p if dt is None else (dt, p))

if ONLY:
    os.makedirs(OUT, exist_ok=True)      # 증분: 기존 날짜 폴더를 보존한다
else:
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)


def write_day_md(path, day, items, title, note=""):
    tools = Counter(p["tool"] for _d, p in items)
    cwds = Counter(short(p["cwd"]) for _d, p in items)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {day} ({WEEKDAYS[items[0][0].weekday()]}) {title} {len(items)}건\n\n")
        if note:
            fh.write(f"> {note}\n\n")
        fh.write(f"- 시간대: {items[0][0]:%H:%M} ~ {items[-1][0]:%H:%M}\n")
        fh.write("- 도구: " + ", ".join(f"{k} {v}건" for k, v in tools.most_common()) + "\n")
        fh.write("- 작업 대상: "
                 + ", ".join(f"`{k}` {v}건" for k, v in cwds.most_common(8)) + "\n\n---\n\n")
        for dt, p in items:
            head = f"{dt:%H:%M} · {p['tool']} · `{short(p['cwd'])}`"
            if p.get("branch"):
                head += f" · `{p['branch']}`"
            if p.get("bucket") not in (None, "human"):
                head += f" · [{p['bucket']}]"
            body = p["text"].strip()
            if p.get("orig_chars"):
                body += f"\n\n… (총 {p['orig_chars']:,}자 중 앞부분만. 나머지 생략)"
            fh.write(f"## {head}\n\n```\n{body}\n```\n\n")


index_rows = []
# 지시가 있는 날 + 커밋이 있는 날의 합집합.
# AI 없이 작업한 날도 실적이므로 커밋만 있는 날에도 폴더를 만든다.
for day in sorted(set(by_date) | set(commits_by_date)):
    if ONLY and day not in ONLY:
        continue
    allitems = sorted(by_date.get(day, []), key=lambda x: x[0])
    daycommits = commits_by_date.get(day, [])
    if not allitems and not daycommits:
        continue
    # 사람이 직접 준 지시 vs AI 가 하위 에이전트에 넘긴 작업지시
    items = [x for x in allitems if x[1].get("bucket", "human") == "human"]
    agent = [x for x in allitems if x[1].get("bucket", "human") != "human"]
    ddir = os.path.join(OUT, day)
    os.makedirs(ddir, exist_ok=True)
    wd = WEEKDAYS[datetime.strptime(day, "%Y-%m-%d").weekday()]

    # 사람 지시가 0건인 날도 폴더는 만든다 (AI 없이 커밋만 한 날도 실적이다).
    # 이때 non-human 을 instructions 로 옮기지 않는다 — 집계가 오염된다.
    with open(os.path.join(ddir, "instructions.jsonl"), "w", encoding="utf-8") as fh:
        for _dt, p in items:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    if items:
        write_day_md(os.path.join(ddir, "instructions.md"), day, items, "업무 지시")
    else:
        note = ("이 날 AI 도구에 준 지시는 없습니다. "
                + ("`commits.csv` 에 이 날 작업 결과가 있습니다."
                   if daycommits else "`agent-tasks.md` 를 참고하세요."))
        with open(os.path.join(ddir, "instructions.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# {day} ({wd}) 업무 지시 0건\n\n> {note}\n")

    if daycommits:
        write_commits(os.path.join(ddir, "commits.csv"), daycommits)

    if agent:
        with open(os.path.join(ddir, "agent-tasks.jsonl"), "w", encoding="utf-8") as fh:
            for _dt, p in agent:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        write_day_md(os.path.join(ddir, "agent-tasks.md"), day, agent, "에이전트 작업지시",
                     "사람이 쓴 지시가 아닙니다. AI 가 하위 에이전트에 넘긴 작업 지시 "
                     "(agent-task) 이거나, 다른 AI·도구가 생성해 넣은 프롬프트 "
                     "(generated) 입니다. 실적 집계에서는 제외하세요.")

    sids = []
    for _d, p in allitems:
        if p["session_id"] not in sids:
            sids.append(p["session_id"])
    with open(os.path.join(ddir, "sessions.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(sess_header)
        for s in sids:
            if s in sess_by_id:
                w.writerow(sess_by_id[s])

    tools = Counter(p["tool"] for _d, p in items)
    cwds = Counter(short(p["cwd"]) for _d, p in items)
    if not cwds:   # 커밋만 있는 날은 저장소를 작업 대상으로 표기
        cwds = Counter(c["repo"] for c in daycommits)
    start = f"{allitems[0][0]:%H:%M}" if allitems else min(
        (c["time"] for c in daycommits), default="")
    end = f"{allitems[-1][0]:%H:%M}" if allitems else max(
        (c["time"] for c in daycommits), default="")
    index_rows.append([
        day, wd, len(items),
        tools.get("claude-code", 0), tools.get("codex", 0), len(agent), len(sids),
        len(daycommits),
        sum(c["insertions"] for c in daycommits),
        sum(c["deletions"] for c in daycommits),
        start, end,
        " / ".join(f"{k}({v})" for k, v in cwds.most_common(5)),
    ])

if undated:
    ddir = os.path.join(OUT, "_no-date")
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, "instructions.jsonl"), "w", encoding="utf-8") as fh:
        for p in undated:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

INDEX_COLS = ["날짜", "요일", "지시수", "claude-code", "codex", "에이전트작업",
              "세션수", "커밋수", "추가줄", "삭제줄", "시작", "종료",
              "작업대상(상위5)"]
index_path = os.path.join(OUT, "index.csv")

if not index_rows:
    if ONLY:
        print(f"대상 날짜에 데이터가 없습니다: {', '.join(sorted(ONLY))}")
        sys.exit(0)
    sys.exit("날짜별로 분류할 지시가 없습니다.")

# 증분 실행이면 기존 index 를 읽어 해당 날짜만 갈아끼운다
merged = {r[0]: r for r in index_rows}
if ONLY and os.path.exists(index_path):
    with open(index_path, encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        next(rd, None)
        for row in rd:
            if row and row[0] not in merged:
                merged[row[0]] = row

with open(index_path, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(INDEX_COLS)
    for k in sorted(merged):
        w.writerow(merged[k])

# 날짜 폴더로 재현되지 않는 것만 (지시문 통합본은 중복이라 제외)
ref = os.path.join(OUT, "_reference")
os.makedirs(ref, exist_ok=True)
for f in ("sessions-all.csv", "daily-activity.csv", "commits.jsonl"):
    src = os.path.join(dig, f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(ref, f))

months = defaultdict(lambda: [0, 0])
for r in index_rows:
    m = months[r[0][:7]]
    m[0] += 1
    m[1] += r[2]

L = ["# 업무 실적 데이터 — 날짜별", "",
     "AI 코딩 도구(Claude Code, Codex) 세션 로그에서 추출한 **업무 지시 전문과 세션 인덱스**입니다.",
     "원본 JSONL 로그(툴 실행 결과·소스코드 포함)는 들어있지 않습니다.", "",
     "## 대상 범위", ""]
L += [f"- `{p}`  (표기: `~{l}`)" for l, p in TARGETS]
L += ["- 이 밖의 경로에서 한 작업은 전부 제외", "", "## 합계", "",
      f"- 기간: {index_rows[0][0]} ~ {index_rows[-1][0]}",
      f"- 활동일: **{len(index_rows)}일** / 사람이 준 업무 지시: **{sum(r[2] for r in index_rows):,}건**",
      f"- Claude Code {sum(r[3] for r in index_rows):,}건 · Codex {sum(r[4] for r in index_rows):,}건",
      f"- 별도 분리: AI 가 하위 에이전트에 넘긴 작업지시 {sum(r[5] for r in index_rows):,}건 "
      "(`agent-tasks.*`, 실적 집계 제외)",
      "", "## 월별", "", "| 월 | 활동일 | 지시 |", "|---|---:|---:|"]
for m in sorted(months):
    L.append(f"| {m} | {months[m][0]} | {months[m][1]:,} |")
L += ["", "## 구조", "", "```",
      "index.csv                    날짜별 한 줄 요약 (이것부터 보세요)",
      f"YYYY-MM-DD/                  날짜 폴더 {len(index_rows)}개 (계층 없음)",
      "  instructions.md            그날 사람이 준 업무 지시 전문, 시간순",
      "  instructions.jsonl         같은 내용 구조화 (tool/cwd/branch/timestamp/text)",
      "  agent-tasks.md|.jsonl      AI 가 만든 지시 — agent-task, generated (있는 날만)",
      "  commits.csv                그날 내 git 커밋 — 시각·저장소·타입·제목·변경량",
      "  sessions.csv               그날 지시가 있었던 세션 인덱스",
      "_reference/",
      "  sessions-all.csv           전체 세션 인덱스 (지시 없는 세션 포함)",
      "  daily-activity.csv         일별 세션·지시·툴호출 집계",
      "```", "", "## 읽는 법", "",
      "- `instructions.md` 의 각 항목 제목은 `시각 · 도구 · 작업디렉토리 · git브랜치` 입니다.",
      "- 세션이 자정을 넘긴 경우, 지시는 실제 입력 시각 기준으로 그날에 들어갑니다.",
      "  같은 세션이 이틀에 걸쳐 양쪽 `sessions.csv` 에 나타날 수 있습니다.",
      "- 날짜 폴더의 `sessions.csv` 는 그날 지시가 있었던 세션만 담습니다.",
      "  지시 없이 실행된 세션은 `_reference/sessions-all.csv` 에만 있습니다.",
      "- Codex 의 토큰 수치는 캐시분을 입력 토큰에 누적 포함해서, Claude Code 수치와",
      "  직접 비교하면 안 됩니다. 세션·지시·툴호출 수는 동일 기준입니다."]
# 증분 실행에서는 합계가 그날치만 반영되므로 README 를 덮지 않는다
if not ONLY or not os.path.exists(os.path.join(OUT, "README.md")):
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

scope_msg = f"대상 {', '.join(sorted(ONLY))}" if ONLY else f"날짜 폴더 {len(index_rows)}개"
print(f"{scope_msg} / 지시 {sum(r[2] for r in index_rows):,}건")
if undated:
    print(f"타임스탬프 없음: {len(undated)}건 → _no-date/")
print(f"→ {OUT}")
