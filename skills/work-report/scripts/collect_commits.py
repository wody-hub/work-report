#!/usr/bin/env python3
"""대상 경로의 git 저장소에서 내 커밋을 수집한다.

지시문이 '무엇을 시켰는가'라면 커밋은 '무엇이 나왔는가'다. 실적 근거로는
둘을 시각으로 짝지을 수 있을 때 가장 강하다.

사용법:
  WORK_TARGETS="/path/a:/path/b" collect_commits.py <출력경로>

출력: <출력경로>/digest/commits.jsonl

설정 (환경변수):
  WORK_GIT_AUTHORS   내 이메일. 콜론 구분. 비우면 git config user.email 사용
  WORK_GIT_SINCE     이 날짜 이후만 (예 2025-01-01). 비우면 전체
  WORK_GIT_DEPTH     저장소 탐색 깊이 (기본 4)
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=float(os.environ.get("WORK_TZ_OFFSET", "9"))))
DEPTH = int(os.environ.get("WORK_GIT_DEPTH", "4"))
SINCE = os.environ.get("WORK_GIT_SINCE", "").strip()
US = "\x1f"          # 필드 구분자 — 커밋 제목에 나올 일이 없다
SKIP_DIRS = {"node_modules", ".venv", "venv", "vendor", "dist", "build", ".gradle"}

# feat(scope): subject  형태에서 타입과 스코프를 뽑는다
CONV_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<rest>.*)$")


def load_targets():
    raw = os.environ.get("WORK_TARGETS", "").strip()
    if not raw:
        sys.exit("WORK_TARGETS 가 비어 있습니다.")
    out, seen = [], Counter()
    for p in raw.split(":"):
        p = os.path.expanduser(p.strip().rstrip("/"))
        if not p or not os.path.isdir(p):
            continue
        base = re.sub(r"[^\w.-]", "_", os.path.basename(p)) or "root"
        seen[base] += 1
        out.append((base if seen[base] == 1 else f"{base}{seen[base]}", p))
    return out


def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, errors="replace", timeout=120)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def find_repos(root):
    """root 하위의 git 저장소. worktree(.git 이 파일)는 본체와 객체를 공유하므로 건너뛴다."""
    repos = []
    root_depth = root.rstrip("/").count(os.sep)
    for dirpath, dirnames, _files in os.walk(root):
        if dirpath.count(os.sep) - root_depth >= DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if ".git" in dirnames:
            repos.append(dirpath)
            dirnames.remove(".git")   # 저장소 안의 중첩 탐색은 계속 (서브모듈 대응)
    return sorted(repos)


def authors():
    raw = os.environ.get("WORK_GIT_AUTHORS", "").strip()
    if raw:
        return [a.strip() for a in raw.split(":") if a.strip()]
    email = subprocess.run(["git", "config", "--global", "user.email"],
                           capture_output=True, text=True).stdout.strip()
    return [email] if email else []


def parse_shortstat(line):
    f = re.search(r"(\d+) files? changed", line)
    i = re.search(r"(\d+) insertions?\(\+\)", line)
    d = re.search(r"(\d+) deletions?\(-\)", line)
    return (int(f.group(1)) if f else 0,
            int(i.group(1)) if i else 0,
            int(d.group(1)) if d else 0)


def collect(repo, label, target, emails):
    fmt = f"C{US}%H{US}%aI{US}%ae{US}%an{US}%S{US}%P{US}%s"
    cmd = ["log", "--all", "--source", f"--pretty=format:{fmt}", "--shortstat"]
    for e in emails:
        cmd.append(f"--author={e}")
    if SINCE:
        cmd.append(f"--since={SINCE}")
    out = git(repo, *cmd)
    if not out:
        return []

    rel = os.path.relpath(repo, target)
    repo_name = f"~{label}" if rel == "." else f"~{label}/{rel}"
    rows, cur, seen = [], None, set()
    for line in out.splitlines():
        if line.startswith("C" + US):
            if cur:
                rows.append(cur)
            p = line.split(US)
            if len(p) < 8:
                cur = None
                continue
            _, h, aiso, ae, an, ref, parents, subj = p[:8]
            if h in seen:
                cur = None
                continue
            seen.add(h)
            try:
                dt = datetime.fromisoformat(aiso).astimezone(TZ)
            except Exception:
                cur = None
                continue
            m = CONV_RE.match(subj)
            cur = {
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "timestamp": dt.isoformat(),
                "repo": repo_name,
                "scope_label": label,
                "branch": re.sub(r"^refs/(heads|remotes)/", "", ref),
                "hash": h[:10],
                "type": (m.group("type") if m else ""),
                "conv_scope": (m.group("scope") or "" if m else ""),
                "subject": subj,
                "is_merge": len(parents.split()) > 1,
                "author": ae or an,
                "files": 0, "insertions": 0, "deletions": 0,
            }
        elif cur and ("file changed" in line or "files changed" in line):
            cur["files"], cur["insertions"], cur["deletions"] = parse_shortstat(line)
    if cur:
        rows.append(cur)
    return rows


def main():
    if len(sys.argv) < 2:
        sys.exit("사용법: collect_commits.py <출력경로>")
    dest = os.path.abspath(os.path.expanduser(sys.argv[1]))
    dig = os.path.join(dest, "digest")
    os.makedirs(dig, exist_ok=True)

    emails = authors()
    if not emails:
        print("  git author 를 알 수 없습니다. WORK_GIT_AUTHORS 를 설정하세요. 건너뜁니다.",
              file=sys.stderr)
        open(os.path.join(dig, "commits.jsonl"), "w").close()
        return
    print(f"  author: {', '.join(emails)}")

    all_rows, nrepo = [], 0
    for label, target in load_targets():
        for repo in find_repos(target):
            rows = collect(repo, label, target, emails)
            if rows:
                nrepo += 1
                all_rows.extend(rows)
    all_rows.sort(key=lambda r: r["timestamp"])

    with open(os.path.join(dig, "commits.jsonl"), "w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    days = len({r["date"] for r in all_rows})
    print(f"  저장소 {nrepo}개 / 커밋 {len(all_rows):,}개 / {days}일")
    if all_rows:
        t = Counter(r["type"] for r in all_rows if r["type"])
        print("  타입:", ", ".join(f"{k} {v}" for k, v in t.most_common(6)))
        print(f"  변경: +{sum(r['insertions'] for r in all_rows):,} "
              f"-{sum(r['deletions'] for r in all_rows):,}")


main()
