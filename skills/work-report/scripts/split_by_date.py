#!/usr/bin/env python3
"""collect_sessions.py 의 digest 를 날짜별 폴더로 분류한다.

사용법:
  WORK_TARGETS="/path/a:/path/b" split_by_date.py <수집경로> <출력경로>

출력 구조 (계층 없음 — 날짜 폴더가 최상위):
  <OUT>/index.csv                 날짜별 한 줄 요약
  <OUT>/YYYY-MM-DD/
      instructions.md             그날 사람이 준 업무 지시 전문 (시간순)
      instructions.jsonl
      agent-tasks.md|.jsonl       AI 가 하위 에이전트에 넘긴 작업지시 (있는 날만)
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
if len(sys.argv) < 3:
    sys.exit("사용법: split_by_date.py <수집경로> <출력경로>")
SRC = os.path.abspath(os.path.expanduser(sys.argv[1]))
OUT = os.path.abspath(os.path.expanduser(sys.argv[2]))


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
            fh.write(f"## {head}\n\n```\n{p['text'].strip()}\n```\n\n")


index_rows = []
for day in sorted(by_date):
    allitems = sorted(by_date[day], key=lambda x: x[0])
    # 사람이 직접 준 지시 vs AI 가 하위 에이전트에 넘긴 작업지시
    items = [x for x in allitems if x[1].get("bucket", "human") == "human"]
    agent = [x for x in allitems if x[1].get("bucket", "human") != "human"]
    if not items:
        items, agent = allitems, []
    ddir = os.path.join(OUT, day)
    os.makedirs(ddir, exist_ok=True)

    with open(os.path.join(ddir, "instructions.jsonl"), "w", encoding="utf-8") as fh:
        for _dt, p in items:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    write_day_md(os.path.join(ddir, "instructions.md"), day, items, "업무 지시")

    if agent:
        with open(os.path.join(ddir, "agent-tasks.jsonl"), "w", encoding="utf-8") as fh:
            for _dt, p in agent:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        write_day_md(os.path.join(ddir, "agent-tasks.md"), day, agent, "에이전트 작업지시",
                     "사람이 쓴 지시가 아니라, AI 가 하위 에이전트에게 넘긴 작업 지시입니다. "
                     "실적 집계에서는 제외하세요.")

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
    index_rows.append([
        day, WEEKDAYS[items[0][0].weekday()], len(items),
        tools.get("claude-code", 0), tools.get("codex", 0), len(agent), len(sids),
        f"{allitems[0][0]:%H:%M}", f"{allitems[-1][0]:%H:%M}",
        " / ".join(f"{k}({v})" for k, v in cwds.most_common(5)),
    ])

if undated:
    ddir = os.path.join(OUT, "_no-date")
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, "instructions.jsonl"), "w", encoding="utf-8") as fh:
        for p in undated:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

if not index_rows:
    sys.exit("날짜별로 분류할 지시가 없습니다.")

with open(os.path.join(OUT, "index.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["날짜", "요일", "지시수", "claude-code", "codex", "에이전트작업",
                "세션수", "시작", "종료", "작업대상(상위5)"])
    w.writerows(index_rows)

# 날짜 폴더로 재현되지 않는 것만 (지시문 통합본은 중복이라 제외)
ref = os.path.join(OUT, "_reference")
os.makedirs(ref, exist_ok=True)
for f in ("sessions-all.csv", "daily-activity.csv"):
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
      "  agent-tasks.md|.jsonl      AI 가 하위 에이전트에 넘긴 작업지시 (있는 날만)",
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
with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")

print(f"날짜 폴더 {len(index_rows)}개 / 지시 {sum(r[2] for r in index_rows):,}건")
if undated:
    print(f"타임스탬프 없음: {len(undated)}건 → _no-date/")
print(f"→ {OUT}")
