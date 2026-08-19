#!/usr/bin/env python3
"""설정 후보를 실제 로그에서 탐지한다. 첫 실행 안내용.

사람에게 "수집할 경로를 입력하세요" 라고 묻는 것은 답하기 어렵다. 세션 로그에
이미 어디에서 작업했는지 다 들어 있으므로, 그것을 세어 후보를 제시한다.

사용법:
  init_config.py                    후보를 사람이 읽을 형태로 출력
  init_config.py --json             기계 판독용 JSON
  init_config.py --write "경로:경로" "결과폴더" ["이메일:이메일"]
                                    설정 파일 생성
출력 경로는 WORK_REPORT_CONFIG 또는 ~/.config/work-report/config.env
"""
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

HOME = os.path.expanduser("~")
CLAUDE_ROOT = os.path.join(HOME, ".claude", "projects")
CODEX_SESS = os.path.join(HOME, ".codex", "sessions")
CONFIG = os.environ.get("WORK_REPORT_CONFIG",
                        os.path.join(HOME, ".config", "work-report", "config.env"))
# macOS 에서 이 아래는 iCloud 동기화 대상이라 결과 폴더로 부적합하다
ICLOUD = (os.path.join(HOME, "Documents"), os.path.join(HOME, "Desktop"))


def jlines(path, limit=None):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if limit and i > limit:
                return
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if isinstance(o, dict):
                yield o


CWD_TAG = re.compile(r"<cwd>([^<]+)</cwd>")


def collect_cwds():
    """세션 로그에서 실제 작업 경로와 세션 수를 센다."""
    counts = Counter()
    tools = defaultdict(Counter)
    for d in sorted(glob.glob(os.path.join(CLAUDE_ROOT, "*"))):
        if not os.path.isdir(d):
            continue
        files = glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True)
        cwd = None
        for f in sorted(files):
            for o in jlines(f, 50):
                if o.get("cwd"):
                    cwd = o["cwd"]
                    break
            if cwd:
                break
        if cwd:
            counts[cwd] += len(files)
            tools[cwd]["claude-code"] += len(files)
    for f in glob.glob(os.path.join(CODEX_SESS, "**", "*.jsonl"), recursive=True):
        cwd = None
        for o in jlines(f, 25):
            if o.get("type") == "session_meta":
                cwd = (o.get("payload") or {}).get("cwd")
                break
        if not cwd:                      # 구형 포맷: 환경 블록에서 추출
            for o in jlines(f, 60):
                m = CWD_TAG.search(json.dumps(o, ensure_ascii=False))
                if m:
                    cwd = m.group(1)
                    break
        if cwd:
            counts[cwd] += 1
            tools[cwd]["codex"] += 1
    return counts, tools


def candidates(counts, max_depth=4, min_sessions=3):
    """cwd 들의 공통 상위 경로를 후보로 뽑는다.

    개별 cwd 를 그대로 쓰면 수십 개가 되고, 홈 디렉토리를 쓰면 개인 작업까지
    섞인다. 그 사이의 '프로젝트 묶음' 수준을 찾는다.
    """
    score = Counter()
    kids = defaultdict(set)
    home_depth = HOME.rstrip("/").count(os.sep)
    for cwd, n in counts.items():
        if not cwd.startswith(HOME + os.sep):
            continue
        parts = cwd[len(HOME) + 1:].split(os.sep)
        for depth in range(1, min(len(parts), max_depth) + 1):
            anc = os.path.join(HOME, *parts[:depth])
            score[anc] += n
            kids[anc].add(cwd)
    out = []
    for path, n in score.items():
        depth = path.rstrip("/").count(os.sep) - home_depth
        if n < min_sessions or depth < 1:
            continue
        out.append({"path": path, "sessions": n,
                    "cwd_count": len(kids[path]), "depth": depth})
    # 세션이 많고 하위 경로를 많이 묶는 것부터
    out.sort(key=lambda r: (-r["sessions"], r["depth"]))
    return out


def git_authors(paths):
    """대상 경로의 저장소에서 커밋 author 이메일을 센다."""
    emails = Counter()
    for root in paths:
        if not os.path.isdir(root):
            continue
        rd = root.rstrip("/").count(os.sep)
        for dp, dn, _f in os.walk(root):
            if dp.count(os.sep) - rd >= 4:
                dn[:] = []
                continue
            dn[:] = [x for x in dn
                     if x not in {"node_modules", ".venv", "dist", "build", ".gradle"}]
            if ".git" not in dn:
                continue
            dn.remove(".git")
            out = subprocess.run(
                ["git", "-C", dp, "log", "--all", "--since=1 year ago", "--pretty=%ae"],
                capture_output=True, text=True).stdout
            emails.update(e for e in out.split() if "@" in e)
    return emails


def suggest_workdir():
    for name in ("work-report-out", "업무정리"):
        p = os.path.join(HOME, name)
        if not any(p.startswith(i + os.sep) or p == i for i in ICLOUD):
            return p
    return os.path.join(HOME, "work-report-out")


def write_config(targets, workdir, authors):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    if any(workdir.startswith(i + os.sep) or workdir == i for i in ICLOUD):
        print(f"경고: {workdir} 는 iCloud 동기화 대상입니다. "
              "대용량 폴더를 넣으면 ' 2' 충돌 폴더가 생기고 데이터가 "
              "클라우드로 나갑니다.", file=sys.stderr)
    body = f"""# work-report 설정 (개인 파일 — 저장소에 커밋되지 않는다)
# 값을 바꾸면 다음 실행부터 반영된다.

# ── 필수 ────────────────────────────────────────────────────────────────
# 수집 대상 경로. 콜론(:) 구분. 이 경로 하위에서 실행된 세션만 모은다.
# 마지막 폴더명이 리포트의 범위 라벨이 된다.
WORK_TARGETS="{targets}"

# 결과를 둘 폴더. macOS 에서 ~/Documents, ~/Desktop 은 피한다 (iCloud 동기화).
WORK_DIR="{workdir}"

# ── 선택 ────────────────────────────────────────────────────────────────
# 시간대 오프셋(시간). 9 = KST, 0 = UTC, -8 = PST
WORK_TZ_OFFSET="9"

# 지시문 길이 상한(자). 붙여넣은 코드가 결과물에 실리는 것을 줄인다. 0 = 무제한
WORK_MAX_CHARS="10000"

# ── git 커밋 수집 ───────────────────────────────────────────────────────
# 내 이메일. 콜론 구분. 비우면 git config --global user.email 을 쓴다.
WORK_GIT_AUTHORS="{authors}"

# 이 날짜 이후 커밋만. 비우면 전체 이력 (AI 도구 사용 전 기간까지 포함된다)
WORK_GIT_SINCE=""

# 저장소 탐색 깊이
WORK_GIT_DEPTH="4"

# ── 코드 변경(diff) 수집 ────────────────────────────────────────────────
WORK_CODE="1"                      # 0 이면 코드 수집 안 함
WORK_CODE_MAX_FILE_LINES="500"     # 파일 하나의 최대 diff 줄 수
WORK_CODE_MAX_DAY_KB="2048"        # 하루 patch 최대 크기(KB)
WORK_CODE_EXT=""                   # 포함할 확장자. 비우면 기본 목록

# ── 자격증명 마스킹 ─────────────────────────────────────────────────────
WORK_MASK="1"                      # 0 이면 원문 그대로 나간다
WORK_MASK_IP="0"                   # IP 도 마스킹. 오탐이 많아 기본 off
# 확실히 지울 문자열 목록 (한 줄에 하나). 그 파일 자체가 평문 목록이 된다.
WORK_MASK_FILE="$HOME/.config/work-report/secrets.txt"
"""
    with open(CONFIG, "w", encoding="utf-8") as fh:
        fh.write(body)
    return CONFIG


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--write" in sys.argv:
        if len(args) < 2:
            sys.exit('사용법: init_config.py --write "경로:경로" "결과폴더" ["이메일:이메일"]')
        p = write_config(args[0], args[1], args[2] if len(args) > 2 else "")
        print(f"설정 파일 생성: {p}")
        return

    counts, tools = collect_cwds()
    if not counts:
        sys.exit("세션 로그를 찾지 못했습니다. Claude Code 나 Codex 를 먼저 사용하세요.")
    cands = candidates(counts)
    authors = git_authors([c["path"] for c in cands[:6]])
    result = {
        "candidates": cands[:12],
        "authors": authors.most_common(8),
        "suggested_workdir": suggest_workdir(),
        "config_path": CONFIG,
        "config_exists": os.path.exists(CONFIG),
        "total_sessions": sum(counts.values()),
    }
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return

    print(f"세션 로그에서 작업 경로 {len(counts)}종 / 세션 {sum(counts.values())}개를 찾았습니다.\n")
    print("수집 대상 후보 (세션 수 순):")
    print(f"  {'세션':>6}  {'하위경로':>6}  경로")
    for c in cands[:12]:
        print(f"  {c['sessions']:>6}  {c['cwd_count']:>6}  {c['path'].replace(HOME, '~')}")
    print("\ngit 커밋 author 후보:")
    for e, n in authors.most_common(8):
        print(f"  {n:>6}  {e}")
    print(f"\n결과 폴더 제안: {result['suggested_workdir'].replace(HOME, '~')}")
    print(f"설정 파일 위치: {CONFIG.replace(HOME, '~')}"
          + ("  (이미 있음)" if result["config_exists"] else "  (없음)"))


if __name__ == "__main__":
    main()
