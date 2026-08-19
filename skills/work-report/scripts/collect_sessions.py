#!/usr/bin/env python3
"""AI 코딩 세션 로그에서 업무 지시와 세션 지표를 추출한다.

지원 도구:
  Claude Code  ~/.claude/projects/<encoded-cwd>/**.jsonl
  Codex        ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

지정한 경로(WORK_TARGETS) 하위에서 실행된 세션만 모은다. 그 밖의 작업은 제외한다.

사용법:
  WORK_TARGETS="/path/a:/path/b" collect_sessions.py <출력경로> [--with-raw]

기본은 digest 만 생성한다 (수 초). --with-raw 를 주면 원본 JSONL 까지 복사한다
(수 GB, 수 분). 원본에는 툴 실행 결과로 읽힌 소스코드·설정파일·시크릿이 그대로
들어있으므로 외부 공유용으로 쓰지 않는다.
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

from mask import mask_text          # 같은 디렉토리

HOME = os.path.expanduser("~")
CLAUDE_ROOT = os.path.join(HOME, ".claude", "projects")
CODEX_SESS = os.path.join(HOME, ".codex", "sessions")
CODEX_HIST = os.path.join(HOME, ".codex", "history.jsonl")

TZ_OFFSET = float(os.environ.get("WORK_TZ_OFFSET", "9"))
TZ = timezone(timedelta(hours=TZ_OFFSET))

# 지시문 길이 상한. 초과분은 잘라낸다 (0 = 무제한).
# 붙여넣은 소스코드·계획서 전문이 결과물에 실려 나가는 것을 막는 장치다.
# 실적 판단에 필요한 '무엇을 지시했는가'는 앞부분에 담긴다.
MAX_CHARS = int(os.environ.get("WORK_MAX_CHARS", "10000"))

# 자격증명 마스킹. 기본 켜짐.
MASK = os.environ.get("WORK_MASK", "1") not in ("0", "", "false", "no")
MASK_HITS = Counter()


def redact(text):
    if not MASK:
        return text
    out, hits = mask_text(text)
    MASK_HITS.update(hits)
    return out

# 사람이 아니라 다른 AI·도구가 만들어 넣은 프롬프트.
# 로그상 user 턴으로 들어오지만 사람의 실적이 아니므로 집계에서 뺀다.
GENERATED_RE = re.compile(
    r"^\s*(?:"
    r"#{1,3}\s*Cross-AI\b"                       # 교차 검토 요청 템플릿
    r"|You are (?:an?|reviewing)\b"              # "You are an external reviewer" 류
    r"|<objective>"                              # 리서치 에이전트 지시
    r"|##\s*Task\s*$"
    r")", re.IGNORECASE)


def classify(text, is_subagent=False):
    """지시문 분류: human / agent-task / generated."""
    if is_subagent:
        return "agent-task"
    if GENERATED_RE.match(text):
        return "generated"
    return "human"


def clip(text):
    """길이 상한 적용. (본문, 원본길이 또는 None) 반환."""
    if MAX_CHARS and len(text) > MAX_CHARS:
        return text[:MAX_CHARS], len(text)
    return text, None


# ---------------------------------------------------------------- 설정
def load_targets():
    """WORK_TARGETS -> [(label, path), ...]. label 은 파일명·표기에 쓴다."""
    raw = os.environ.get("WORK_TARGETS", "").strip()
    if not raw:
        sys.exit("WORK_TARGETS 가 비어 있습니다.\n"
                 '  예: WORK_TARGETS="$HOME/Projects/foo:$HOME/Notes"\n'
                 "  설정 파일: ~/.config/work-report/config.env")
    out, seen = [], Counter()
    for p in raw.split(":"):
        p = os.path.expanduser(p.strip().rstrip("/"))
        if not p:
            continue
        if not os.path.isdir(p):
            print(f"경고: 대상 경로가 없습니다 — {p}", file=sys.stderr)
        base = re.sub(r"[^\w.-]", "_", os.path.basename(p)) or "root"
        seen[base] += 1
        label = base if seen[base] == 1 else f"{base}{seen[base]}"
        out.append((label, p))
    if not out:
        sys.exit("WORK_TARGETS 에서 유효한 경로를 찾지 못했습니다.")
    return out


TARGETS = load_targets()
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
WITH_RAW = "--with-raw" in sys.argv
if not ARGS:
    sys.exit("사용법: collect_sessions.py <출력경로> [--with-raw]")
DEST = os.path.abspath(os.path.expanduser(ARGS[0]))


def under(cwd, target):
    return cwd == target or cwd.startswith(target + "/")


def match_target(cwd):
    """cwd 가 속한 타깃을 반환. 가장 긴 경로가 이긴다 (중첩 대상 대응)."""
    best = None
    for label, path in TARGETS:
        if under(cwd, path) and (best is None or len(path) > len(best[1])):
            best = (label, path)
    return best


def short(cwd):
    """리포트 표기용 축약: 타깃 경로를 ~<label> 로 바꾼다."""
    for label, path in sorted(TARGETS, key=lambda t: -len(t[1])):
        if under(cwd, path):
            return cwd.replace(path, f"~{label}", 1)
    return cwd


def iso_local(ts):
    """로그의 UTC 타임스탬프를 설정 시간대의 ISO 8601(오프셋 포함)로 바꾼다.

    md 는 변환해 쓰는데 jsonl 이 UTC 원문을 담고 있으면, 같은 산출물 안에서
    지시(UTC)와 커밋(로컬)이 9시간 어긋난다. 수집 시점에 통일한다.
    """
    if not ts:
        return ts
    try:
        return (datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                .astimezone(TZ).isoformat())
    except Exception:
        return ts


def kst(ts):
    if not ts:
        return ""
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            return datetime.fromtimestamp(int(ts), TZ).strftime("%Y-%m-%d %H:%M")
        return (datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                .astimezone(TZ).strftime("%Y-%m-%d %H:%M"))
    except Exception:
        return str(ts)


def jlines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict):
                yield o


def new_session(**kw):
    r = dict(tool="", scope="", session_id="", kind="main", project_dir="", path="",
             size=0, cwd="", first=None, last=None, prompts=0, replies=0,
             tool_calls=0, in_tok=0, out_tok=0, cache_r=0, cache_w=0,
             originator="", title="")
    r["branches"] = set()
    r["models"] = set()
    r.update(kw)
    return r


# ================================================================ Claude Code
NOISE_PREFIX = ("<command-name>", "<command-message>", "<local-command-stdout>",
                "Caveat: The messages below", "[Request interrupted")

# 사람이 아니라 하네스가 user 턴에 끼워 넣는 것들.
# <image …> 는 제외 — 사람이 이미지를 붙여넣은 지시라서 본문이 사람 것이다.
SYSTEM_TAG_RE = re.compile(
    r"\s*<(?:task-notification|revision_context|planning_context"
    r"|pattern_mapping_context|user_action|user_shell_command"
    r"|bash-input|bash-stdout|bash-stderr|system-reminder)[\s>]")


def text_of(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


def claude_projects(label, target):
    """target 하위에서 실행된 Claude 프로젝트 디렉토리 목록.

    디렉토리명은 cwd 를 인코딩한 것인데 '/' 와 '-' 를 구분하지 못해 신뢰할 수
    없다. 그래서 로그 안의 cwd 필드로 판정한다.
    """
    if not os.path.isdir(CLAUDE_ROOT):
        return []
    hits = []
    for d in sorted(os.listdir(CLAUDE_ROOT)):
        ppath = os.path.join(CLAUDE_ROOT, d)
        if not os.path.isdir(ppath):
            continue
        files = glob.glob(os.path.join(ppath, "**", "*.jsonl"), recursive=True)
        if not files:
            continue
        cwd = None
        for f in sorted(files):
            for i, o in enumerate(jlines(f)):
                if i > 50:
                    break
                if o.get("cwd"):
                    cwd = o["cwd"]
                    break
            if cwd:
                break
        if cwd and under(cwd, target):
            hits.append((d, ppath, cwd, files))
    return hits


def scan_claude(label, target):
    sessions, prompts = [], []
    tools, models = Counter(), Counter()
    projects = claude_projects(label, target)
    for pdir, ppath, _cwd, files in projects:
        for f in files:
            sid = os.path.splitext(os.path.basename(f))[0]
            is_sub = os.path.relpath(f, ppath).count(os.sep) > 0
            r = new_session(tool="claude-code", scope=label, session_id=sid,
                            kind="subagent" if is_sub else "main",
                            project_dir=pdir, path=f, size=os.path.getsize(f))
            for o in jlines(f):
                if o.get("cwd") and not r["cwd"]:
                    r["cwd"] = o["cwd"]
                ts = o.get("timestamp")
                if ts:
                    if not r["first"] or ts < r["first"]:
                        r["first"] = ts
                    if not r["last"] or ts > r["last"]:
                        r["last"] = ts
                if o.get("gitBranch"):
                    r["branches"].add(o["gitBranch"])
                msg = o.get("message") or {}
                if o.get("type") == "user" and not o.get("isMeta"):
                    c = msg.get("content")
                    # tool_result 만 담긴 턴은 툴 실행 결과이지 사람의 지시가 아니다
                    has_tr = isinstance(c, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
                    t = text_of(c).strip()
                    if (not has_tr and t and not t.startswith(NOISE_PREFIX)
                            and not SYSTEM_TAG_RE.match(t)
                            and not (t.startswith("<") and "<system-reminder>" in t[:200])):
                        bucket = classify(t, is_sub)
                        if bucket == "human":
                            r["prompts"] += 1
                            if not r["title"]:
                                r["title"] = re.sub(r"\s+", " ", t)[:120]
                        body, orig = clip(redact(t))
                        prompts.append(dict(tool="claude-code", scope=label,
                                            session_id=sid, kind=r["kind"],
                                            cwd=o.get("cwd", ""),
                                            timestamp=iso_local(ts),
                                            branch=o.get("gitBranch") or "",
                                            bucket=bucket, text=body,
                                            **({"orig_chars": orig} if orig else {})))
                elif o.get("type") == "assistant":
                    r["replies"] += 1
                    if msg.get("model"):
                        r["models"].add(msg["model"])
                        models[msg["model"]] += 1
                    u = msg.get("usage") or {}
                    r["in_tok"] += u.get("input_tokens") or 0
                    r["out_tok"] += u.get("output_tokens") or 0
                    r["cache_r"] += u.get("cache_read_input_tokens") or 0
                    r["cache_w"] += u.get("cache_creation_input_tokens") or 0
                    for b in msg.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            r["tool_calls"] += 1
                            tools[b.get("name", "?")] += 1
            sessions.append(r)
    return sessions, prompts, tools, models, projects


# ======================================================================= Codex
CODEX_PLUMBING = ("<environment_context>", "<permissions instructions>",
                  "<user_instructions>", "<AGENTS.md", "## My request for Codex:",
                  "<system_reminder>")
# 사람이 쓴 게 아니라 도구가 주입한 것
CODEX_INJECTED = ("<skill>", "<recommended_plugins>", "<turn_aborted>",
                  "<subagent_notification>", "<security_context>",
                  "<verification_context>", "<plugin", "<available_plugins>")
# 오케스트레이터가 하위 에이전트에게 준 작업 지시
CODEX_AGENT_TASK = ("<task>",)


# 구형 Codex(2025년경)는 session_meta 가 없고 첫 줄에 id/timestamp/git 이 있으며
# cwd 는 첫 user 메시지의 <environment_context> 안에 들어 있다.
CWD_TAG_RE = re.compile(r"<cwd>([^<]+)</cwd>")


def codex_meta(path):
    """세션 메타를 찾는다. 신형은 session_meta, 구형은 헤더+환경블록에서 복원."""
    head = None
    for i, o in enumerate(jlines(path)):
        if o.get("type") == "session_meta":
            return o.get("payload") or {}
        if head is None and o.get("id") and o.get("timestamp"):
            head = o          # 구형 헤더
        if i > 25:
            break
    if head is None:
        return None
    # 구형: cwd 를 환경 컨텍스트에서 뽑는다
    cwd = ""
    for i, o in enumerate(jlines(path)):
        m = CWD_TAG_RE.search(json.dumps(o, ensure_ascii=False))
        if m:
            cwd = m.group(1)
            break
        if i > 60:
            break
    git = head.get("git")
    if isinstance(git, str):
        git = None            # 구형은 문자열로 직렬화된 경우가 있다
    return {"id": head.get("id"), "session_id": head.get("id"),
            "timestamp": head.get("timestamp"), "cwd": cwd,
            "originator": head.get("originator", ""), "git": git}


def codex_input_text(payload):
    return "\n".join(b.get("text", "") for b in payload.get("content") or []
                     if isinstance(b, dict) and b.get("type") in ("input_text", "text")).strip()


def scan_codex():
    """지시문 출처가 Codex 버전에 따라 두 가지다.

      구형: event_msg / user_message            (payload.message)
      신형: response_item / message role=user   (payload.content[].input_text)

    둘 다 수집하고 (세션, 텍스트) 로 중복 제거한다. 한쪽만 보면 특정 시기
    데이터가 통째로 빠진다.
    """
    sessions, prompts = [], []
    tools, models = Counter(), Counter()
    id_map = {}
    if not os.path.isdir(CODEX_SESS):
        return sessions, prompts, tools, models, id_map
    for f in sorted(glob.glob(CODEX_SESS + "/**/*.jsonl", recursive=True)):
        meta = codex_meta(f)
        if not meta or not meta.get("cwd"):
            continue
        cwd = meta.get("cwd") or ""
        hit = match_target(cwd)
        if not hit:
            continue
        sid = meta.get("id") or os.path.splitext(os.path.basename(f))[0]
        root = meta.get("session_id") or sid
        parent = meta.get("parent_thread_id")
        r = new_session(tool="codex", scope=hit[0], session_id=sid,
                        kind="sub" if parent and parent not in (sid, root) else "main",
                        project_dir=os.path.relpath(f, CODEX_SESS), path=f,
                        size=os.path.getsize(f), cwd=cwd,
                        first=meta.get("timestamp"),
                        originator=meta.get("originator", ""))
        g = meta.get("git")
        sess_branch = g.get("branch") if isinstance(g, dict) else None
        if sess_branch:
            r["branches"].add(sess_branch)
        seen_text = set()
        for o in jlines(f):
            ts = o.get("timestamp")
            if ts:
                if not r["first"] or ts < r["first"]:
                    r["first"] = ts
                if not r["last"] or ts > r["last"]:
                    r["last"] = ts
            # 신형은 payload 안에, 구형은 최상위에 레코드가 있다
            p = o.get("payload")
            if not isinstance(p, dict):
                p = o
            pt = p.get("type")
            t, src = None, ""
            if pt == "user_message":
                t, src = (p.get("message") or "").strip(), "event_msg"
            elif pt == "message" and p.get("role") == "user":
                t, src = codex_input_text(p), "response_item"
            if t is not None:
                # 정규화한 '전문' 으로 중복 판정한다. 앞 400자만 보면 서두가
                # 같고 뒤가 다른 지시(계획서 재검토 요청 등)가 유실된다.
                key = re.sub(r"\s+", " ", t)
                if t and not t.startswith(CODEX_PLUMBING) and key not in seen_text:
                    seen_text.add(key)
                    lead = t.lstrip()
                    if lead.startswith(CODEX_INJECTED) or SYSTEM_TAG_RE.match(t):
                        bucket = "injected"
                    elif lead.startswith(CODEX_AGENT_TASK):
                        bucket = "agent-task"
                    else:
                        bucket = classify(t)
                        if bucket == "human":
                            r["prompts"] += 1
                            if not r["title"]:
                                r["title"] = re.sub(r"\s+", " ", t)[:120]
                    body, orig = clip(redact(t))
                    prompts.append(dict(tool="codex", scope=hit[0], session_id=sid,
                                        kind=r["kind"], cwd=cwd,
                                        timestamp=iso_local(ts),
                                        branch=sess_branch or "",
                                        source=src, bucket=bucket,
                                        text=body,
                                        **({"orig_chars": orig} if orig else {})))
                continue
            if pt == "agent_message":
                r["replies"] += 1
            elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
                r["tool_calls"] += 1
                tools[p.get("name") or pt] += 1
            elif pt == "token_count":
                tu = (p.get("info") or {}).get("total_token_usage") or {}
                if tu:
                    r["in_tok"] = max(r["in_tok"], tu.get("input_tokens") or 0)
                    r["out_tok"] = max(r["out_tok"], tu.get("output_tokens") or 0)
                    r["cache_r"] = max(r["cache_r"], tu.get("cached_input_tokens") or 0)
            elif pt == "turn_context" and p.get("model"):
                r["models"].add(p["model"])
                models[p["model"]] += 1
        sessions.append(r)
        id_map[sid] = r
        id_map[root] = r
    return sessions, prompts, tools, models, id_map


# ====================================================================== 출력
SESSION_COLS = ["도구", "범위", "시작", "종료", "구분", "실행주체", "cwd",
                "git branch", "세션ID", "지시수", "응답수", "툴호출", "입력토큰",
                "출력토큰", "캐시읽기", "캐시생성", "용량KB", "모델",
                "원본상대경로", "첫 지시"]


def write_sessions(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(SESSION_COLS)
        for r in sorted(rows, key=lambda r: r["first"] or ""):
            w.writerow([r["tool"], r["scope"], kst(r["first"]), kst(r["last"]),
                        r["kind"], r["originator"], r["cwd"],
                        ",".join(sorted(r["branches"])), r["session_id"],
                        r["prompts"], r["replies"], r["tool_calls"], r["in_tok"],
                        r["out_tok"], r["cache_r"], r["cache_w"],
                        round(r["size"] / 1024, 1), ",".join(sorted(r["models"])),
                        r["project_dir"], r["title"]])


def write_prompts(base, prompts, title):
    prompts = sorted(prompts, key=lambda p: str(p["timestamp"] or ""))
    with open(base + ".jsonl", "w", encoding="utf-8") as fh:
        for p in prompts:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(base + ".md", "w", encoding="utf-8") as fh:
        fh.write(f"# {title} ({len(prompts)}건)\n\n")
        cur = None
        for p in prompts:
            day = kst(p["timestamp"])[:10]
            if day != cur:
                cur = day
                fh.write(f"\n## {day}\n\n")
            head = f"{kst(p['timestamp'])} · {short(p['cwd'])}"
            if p.get("branch"):
                head += f" · `{p['branch']}`"
            if p.get("kind") not in ("main",):
                head += f" · ({p['kind']})"
            if p.get("bucket") not in (None, "human"):
                head += f" · [{p['bucket']}]"
            body = p["text"].strip()
            if p.get("orig_chars"):
                body += (f"\n\n… (총 {p['orig_chars']:,}자 중 앞 {MAX_CHARS:,}자. "
                         "나머지 생략)")
            fh.write(f"### {head}\n\n```\n{body}\n```\n\n")


def stat_block(name, rows, prompts, tools, models):
    L = [f"\n## {name}\n"]
    if not rows:
        L.append("(데이터 없음)")
        return L
    fs = [r["first"] for r in rows if r["first"]]
    ls = [r["last"] for r in rows if r["last"]]
    if fs and ls:
        L.append(f"- 기간: {kst(min(fs))} ~ {kst(max(ls))}")
    L.append(f"- 세션: {len(rows)}개 "
             f"(main {sum(1 for r in rows if r['kind'] == 'main')}, "
             f"그 외 {sum(1 for r in rows if r['kind'] != 'main')})")
    L.append(f"- 업무 지시: **{sum(r['prompts'] for r in rows):,}건**")
    L.append(f"- 응답: {sum(r['replies'] for r in rows):,}건 / "
             f"툴호출: {sum(r['tool_calls'] for r in rows):,}회")
    L.append(f"- 원본 용량: {sum(r['size'] for r in rows) / 1024 / 1024:.1f} MB")
    by = defaultdict(Counter)
    for r in rows:
        c = by[short(r["cwd"])]
        c["s"] += 1
        c["p"] += r["prompts"]
        c["t"] += r["tool_calls"]
    L += ["", "| cwd | 세션 | 지시 | 툴호출 |", "|---|---:|---:|---:|"]
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]["p"])[:25]:
        L.append(f"| `{k}` | {v['s']} | {v['p']} | {v['t']} |")
    mon = Counter(kst(p["timestamp"])[:7] for p in prompts if p["timestamp"])
    if mon:
        L += ["", "| 월 | 지시 |", "|---|---:|"]
        for m, n in sorted(mon.items()):
            L.append(f"| {m} | {n} |")
    if tools:
        L += ["", "| 툴 | 호출 |", "|---|---:|"]
        for t, n in tools.most_common(15):
            L.append(f"| {t} | {n:,} |")
    if models:
        L += ["", "| 모델 | 사용 |", "|---|---:|"]
        for m, n in models.most_common(12):
            L.append(f"| {m} | {n:,} |")
    orig = Counter(r["originator"] for r in rows if r["originator"])
    if orig:
        pc = Counter()
        for r in rows:
            pc[r["originator"]] += r["prompts"]
        L += ["", "| 실행주체 | 세션 | 지시 |", "|---|---:|---:|"]
        for o, n in orig.most_common():
            L.append(f"| {o} | {n} | {pc[o]} |")
    return L


def main():
    dig = os.path.join(DEST, "digest")
    raw = os.path.join(DEST, "raw")
    os.makedirs(dig, exist_ok=True)

    claude = []          # (label, rows, prompts, tools, models, projects)
    for label, path in TARGETS:
        print(f"스캔: Claude Code / {label} …")
        res = scan_claude(label, path)
        print(f"  세션 {len(res[0])} / 지시 {len(res[1])}")
        claude.append((label,) + res)

    print("스캔: Codex …")
    x_rows, x_all, x_tools, x_models, id_map = scan_codex()
    x_pr = [p for p in x_all if p.get("bucket") == "human"]
    x_other = [p for p in x_all if p.get("bucket") != "human"]
    print(f"  세션 {len(x_rows)} / 사람 지시 {len(x_pr)} / 도구주입·에이전트작업 {len(x_other)}")

    all_rows = [r for c in claude for r in c[1]] + x_rows
    # 통합본에는 사람 지시 + AI 가 만든 지시(agent-task, generated)를 넣는다.
    # 날짜별 정리에서 bucket 으로 갈라 agent-tasks.* 로 분리된다.
    # injected(도구가 주입한 스킬 본문 등)는 순수 노이즈라 제외한다.
    all_pr = ([p for c in claude for p in c[2]]
              + [p for p in x_all if p.get("bucket") != "injected"])
    if not all_rows:
        sys.exit("대상 경로에서 실행된 세션을 찾지 못했습니다. WORK_TARGETS 를 확인하세요.")

    # ---- 원본 복사 (옵션)
    if WITH_RAW:
        print("원본 복사 …")
        for label, rows, _pr, _t, _m, projects in claude:
            for pdir, ppath, _cwd, _files in projects:
                dst = os.path.join(raw, f"claude-code-{label}", pdir)
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(ppath, dst)
        for r in x_rows:
            dst = os.path.join(raw, "codex", "sessions", r["project_dir"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(r["path"], dst)
        if os.path.exists(CODEX_HIST):
            os.makedirs(os.path.join(raw, "codex"), exist_ok=True)
            kept = 0
            with open(os.path.join(raw, "codex", "history-filtered.jsonl"), "w",
                      encoding="utf-8") as out:
                for o in jlines(CODEX_HIST):
                    if o.get("session_id") in id_map:
                        o["_cwd"] = id_map[o["session_id"]]["cwd"]
                        out.write(json.dumps(o, ensure_ascii=False) + "\n")
                        kept += 1
            print(f"  codex history: 대상 매칭 {kept}건")

    if MASK_HITS:
        print("  마스킹: " + ", ".join(f"{k} {v}" for k, v in MASK_HITS.most_common()))

    # ---- digest
    print("digest 생성 …")
    write_sessions(os.path.join(dig, "sessions-all.csv"), all_rows)
    write_sessions(os.path.join(dig, "sessions-codex.csv"), x_rows)
    write_prompts(os.path.join(dig, "instructions-all"), all_pr, "전체 업무 지시")
    write_prompts(os.path.join(dig, "instructions-codex"), x_pr, "Codex 업무 지시")
    write_prompts(os.path.join(dig, "instructions-codex-nonhuman"), x_other,
                  "Codex · 도구 주입 및 에이전트 작업지시 (사람 지시 아님)")
    for label, rows, pr, _t, _m, _p in claude:
        write_sessions(os.path.join(dig, f"sessions-claude-{label}.csv"), rows)
        write_prompts(os.path.join(dig, f"instructions-claude-{label}"), pr,
                      f"Claude Code · {label} 업무 지시")

    # 일별 활동 (툴호출 포함 — 날짜별 폴더에는 없는 지표)
    daily = defaultdict(Counter)
    for r in all_rows:
        d = kst(r["first"])[:10]
        if d:
            daily[d][r["tool"] + ":sessions"] += 1
            daily[d][r["tool"] + ":tools"] += r["tool_calls"]
    for p in all_pr:
        d = kst(p["timestamp"])[:10]
        if d:
            daily[d][p["tool"] + ":instructions"] += 1
    cols = ["날짜", "claude-code:sessions", "claude-code:instructions",
            "claude-code:tools", "codex:sessions", "codex:instructions", "codex:tools"]
    with open(os.path.join(dig, "daily-activity.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for d in sorted(daily):
            w.writerow([d] + [daily[d].get(c, 0) for c in cols[1:]])

    # ---- 리포트
    fs = [r["first"] for r in all_rows if r["first"]]
    ls = [r["last"] for r in all_rows if r["last"]]
    L = ["# 업무 실적 데이터", "",
         "AI 코딩 도구(Claude Code, Codex) 세션 로그에서 아래 경로에서 수행한 작업만 추출한 것입니다.", ""]
    L += [f"- `{p}`  (표기: `~{l}`)" for l, p in TARGETS]
    L += ["- 이 밖의 경로에서 한 작업은 전부 제외", "", "## 전체 합계", "",
          f"- 세션 **{len(all_rows)}개** · 업무 지시 **{len(all_pr):,}건** · "
          f"툴 호출 **{sum(r['tool_calls'] for r in all_rows):,}회**"]
    if fs and ls:
        L.append(f"- 기간 {kst(min(fs))} ~ {kst(max(ls))}")
    for label, rows, pr, tools, models, _p in claude:
        L += stat_block(f"Claude Code · {label}", rows, pr, tools, models)
    L += stat_block("Codex", x_rows, x_pr, x_tools, x_models)
    L += ["", "## 주의", "",
          "지시문에는 작성 당시 직접 입력한 서버 주소·계정·토큰이 그대로 남아 있을 수 있습니다.",
          "외부 공유 전에 확인하세요.", ""]
    if WITH_RAW:
        L += ["`raw/` 의 원본 로그에는 툴 실행 결과로 읽힌 소스코드·설정파일·시크릿이",
              "포함됩니다. 외부 공유용으로 쓰지 마세요.", ""]
    with open(os.path.join(DEST, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"→ {DEST}")


main()
