#!/usr/bin/env python3
"""내 커밋의 실제 코드 변경(diff)을 날짜별로 수집한다.

commits.jsonl 이 '무엇을 했다'는 목록이라면, 이건 '실제로 이렇게 바꿨다'는
증거다. 용량과 시크릿 위험이 지시문보다 훨씬 크므로 세 겹으로 막는다.

  1. 파일 제외  — .env, 키·인증서, lock 파일, 데이터 덤프, 바이너리
  2. 분량 상한  — 파일당 줄 수, 하루 총량
  3. 마스킹     — 남은 내용에서 자격증명 값을 [REDACTED:…] 로 치환

사용법:
  WORK_TARGETS=… collect_diffs.py <출력경로>

입력: <출력경로>/digest/commits.jsonl  (collect_commits.py 결과)
출력: <출력경로>/digest/diffs/YYYY-MM-DD.patch

설정 (환경변수):
  WORK_CODE=0                 수집 끄기
  WORK_CODE_MAX_FILE_LINES    파일 하나의 최대 diff 줄 수 (기본 500)
  WORK_CODE_MAX_DAY_KB        하루 patch 최대 크기 KB (기본 2048)
  WORK_CODE_EXT               포함할 확장자. 콤마 구분. 비우면 기본 목록
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

from mask import mask_text

MAX_FILE_LINES = int(os.environ.get("WORK_CODE_MAX_FILE_LINES", "500"))
MAX_DAY_BYTES = int(os.environ.get("WORK_CODE_MAX_DAY_KB", "2048")) * 1024
ENABLED = os.environ.get("WORK_CODE", "1") not in ("0", "", "false", "no")
# 특정 날짜만 갱신 (당일 운영용). 나머지 날짜의 .patch 는 그대로 둔다.
ONLY = {d.strip() for d in os.environ.get("WORK_ONLY_DATES", "").split(",") if d.strip()}

DEFAULT_EXT = (
    "java,kt,swift,py,rb,go,rs,php,cs,scala,groovy,"
    "ts,tsx,js,jsx,vue,svelte,css,scss,less,html,"
    "sql,xml,yml,yaml,toml,gradle,properties,conf,sh,bash,zsh,"
    "dockerfile,tf,proto,graphql,md"
)
EXT = {e.strip().lstrip(".").lower()
       for e in (os.environ.get("WORK_CODE_EXT") or DEFAULT_EXT).split(",") if e.strip()}

# 이름만 봐도 절대 내보내면 안 되는 것들
SECRET_PATH = re.compile(
    r"(^|/)\.env" r"|secret" r"|credential" r"|\.p8$" r"|\.pem$" r"|\.key$"
    r"|\.jks$" r"|\.keystore$" r"|id_rsa" r"|google-services\.json$"
    r"|GoogleService-Info\.plist$" r"|\.p12$" r"|\.pfx$", re.I)

# 사람이 쓴 코드가 아니라 생성물·데이터인 것들
NOISE_PATH = re.compile(
    r"(^|/)(node_modules|dist|build|out|target|coverage|\.next|\.nuxt|vendor)/"
    r"|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Gemfile\.lock"
    r"|poetry\.lock|composer\.lock|gradle\.lockfile)$"
    r"|\.min\.(js|css)$|\.map$|\.snap$"
    r"|(^|/)glossary|word.?dictionary|word.?dectionary|full_abbr"
    r"|-ddl-\d{8}\.sql$|(^|/)(origin|dump)\.csv$"
    # 전체 스키마 덤프 — 손으로 쓴 게 아니라 도구가 뽑은 결과다
    r"|(^|/)(postgres|mysql|oracle|mariadb)-?(ddl|schema|sequences|grants)"
    r"|(^|/)schema-dump", re.I)

FILE_HDR = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")


def load_targets():
    raw = os.environ.get("WORK_TARGETS", "").strip()
    if not raw:
        sys.exit("WORK_TARGETS 가 비어 있습니다.")
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
LABEL2PATH = {f"~{l}": p for l, p in TARGETS}


def resolve(repo_token):
    """'~label/sub/dir' -> 실제 절대경로."""
    head = repo_token.split("/", 1)
    base = LABEL2PATH.get(head[0])
    if not base:
        return None
    return os.path.join(base, head[1]) if len(head) > 1 else base


def keep(path):
    """이 파일의 diff 를 포함할지."""
    if SECRET_PATH.search(path) or NOISE_PATH.search(path):
        return False
    name = os.path.basename(path).lower()
    if name in ("dockerfile", "makefile"):
        return True
    ext = os.path.splitext(name)[1].lstrip(".")
    return ext in EXT


def split_files(diff_text):
    """diff 전체를 파일 단위로 쪼갠다. [(경로, 본문줄들), …]"""
    blocks, cur, path = [], [], None
    for line in diff_text.splitlines():
        m = FILE_HDR.match(line)
        if m:
            if path is not None:
                blocks.append((path, cur))
            path, cur = m.group("b"), [line]
        elif path is not None:
            cur.append(line)
    if path is not None:
        blocks.append((path, cur))
    return blocks


def main():
    if len(sys.argv) < 2:
        sys.exit("사용법: collect_diffs.py <출력경로>")
    dest = os.path.abspath(os.path.expanduser(sys.argv[1]))
    dig = os.path.join(dest, "digest")
    cfile = os.path.join(dig, "commits.jsonl")
    outdir = os.path.join(dig, "diffs")

    if not ENABLED:
        print("  코드 수집 꺼짐 (WORK_CODE=0)")
        return
    if not os.path.exists(cfile):
        print("  commits.jsonl 이 없습니다. 코드 수집을 건너뜁니다.")
        return

    by_date = defaultdict(list)
    for line in open(cfile, encoding="utf-8"):
        c = json.loads(line)
        by_date[c["date"]].append(c)

    os.makedirs(outdir, exist_ok=True)
    stat = Counter()
    mask_hits = Counter()
    missing = []          # patch 에 못 담긴 커밋과 사유

    for day in sorted(by_date):
        if ONLY and day not in ONLY:
            continue
        chunks, size, oversized = [], 0, []
        for c in sorted(by_date[day], key=lambda x: x["timestamp"]):
            if c.get("is_merge"):
                stat["머지 건너뜀"] += 1
                continue
            repo = resolve(c["repo"])
            if not repo or not os.path.isdir(repo):
                stat["저장소 못찾음"] += 1
                missing.append((c["hash"], "저장소 못찾음"))
                continue
            raw = subprocess.run(
                ["git", "-C", repo, "show", c["hash"], "--no-color",
                 "--format=", "--unified=3"],
                capture_output=True, text=True, errors="replace", timeout=120).stdout
            if not raw.strip():
                stat["변경 없음"] += 1
                missing.append((c["hash"], "변경 파일 없음"))
                continue

            kept = []
            for path, lines in split_files(raw):
                if not keep(path):
                    stat["파일 제외"] += 1
                    continue
                # 바이너리는 uuencode 덩어리로 들어온다 — 코드가 아니고 용량만 먹는다
                if any(l.startswith("GIT binary patch") or l.startswith("Binary files")
                       for l in lines[:6]):
                    stat["바이너리 제외"] += 1
                    continue
                if len(lines) > MAX_FILE_LINES:
                    lines = lines[:MAX_FILE_LINES] + [
                        f"... [{len(lines) - MAX_FILE_LINES}줄 생략 — "
                        f"파일당 상한 {MAX_FILE_LINES}줄]"]
                    stat["파일 잘림"] += 1
                kept.append("\n".join(lines))
            if not kept:
                stat["전부 제외 규칙"] += 1
                missing.append((c["hash"], "변경 파일이 전부 제외 규칙"))
                continue

            body = "\n".join(kept)
            # 코드에는 자유형 비밀번호 휴리스틱을 쓰지 않는다.
            # 비밀번호 검증 정규식 같은 코드가 걸려 원본이 훼손된다.
            body, hits = mask_text(body, freeform=False)
            mask_hits.update(hits)

            hdr = (f"{'=' * 78}\n"
                   f"commit {c['hash']}  {c['date']} {c['time']}\n"
                   f"repo   {c['repo']}   branch {c['branch']}\n"
                   f"subject {c['subject']}\n"
                   f"{'=' * 78}\n")
            piece = hdr + body + "\n"
            # 큰 커밋 하나 때문에 그날 나머지까지 버리면 안 된다.
            # 이 커밋만 건너뛰고 다음 커밋을 계속 담는다.
            if size + len(piece.encode()) > MAX_DAY_BYTES:
                oversized.append((c["hash"], c["subject"],
                                  len(piece.encode()) // 1024))
                stat["용량 초과로 건너뜀"] += 1
                missing.append((c["hash"], "하루 용량 상한 초과"))
                continue
            chunks.append(piece)
            size += len(piece.encode())
            stat["커밋 포함"] += 1

        if not chunks:
            continue
        if oversized:
            note = [f"\n... [하루 상한 {MAX_DAY_BYTES // 1024}KB 로 아래 커밋의 "
                    "diff 는 생략했습니다. 커밋 자체는 commits.csv 에 있습니다]"]
            for h, subj, kb in oversized:
                note.append(f"...   {h}  {kb}KB  {subj}")
            chunks.append("\n".join(note) + "\n")
        with open(os.path.join(outdir, f"{day}.patch"), "w", encoding="utf-8") as fh:
            fh.write("".join(chunks))
        stat["날짜 파일"] += 1

    total = sum(os.path.getsize(os.path.join(outdir, f))
                for f in os.listdir(outdir) if f.endswith(".patch"))
    scope_msg = f"대상 {len(ONLY)}일" if ONLY else f"{stat['날짜 파일']}일"
    print(f"  {scope_msg} / 커밋 {stat['커밋 포함']:,}개 / "
          f"전체 patch {total / 1024 / 1024:.1f} MB")
    detail = [f"{k} {v:,}" for k, v in stat.most_common()
              if k not in ("날짜 파일", "커밋 포함")]
    if detail:
        print("  " + ", ".join(detail))
    if mask_hits:
        print("  마스킹: " + ", ".join(f"{k} {v}" for k, v in mask_hits.most_common()))

    scope = [(d, rows) for d, rows in by_date.items() if not ONLY or d in ONLY]
    nonmerge = sum(1 for _d, rows in scope for c in rows if not c.get("is_merge"))
    covered = stat["커밋 포함"]
    pct = covered / nonmerge * 100 if nonmerge else 100
    reasons = Counter(r for _h, r in missing)
    unexplained = reasons.get("저장소 못찾음", 0) + reasons.get("하루 용량 상한 초과", 0)
    color = "33" if unexplained else "0"
    print(f"  \033[{color}m커버리지: 머지 아닌 커밋 {nonmerge:,}개 중 "
          f"{covered:,}개 포함 ({pct:.0f}%)\033[0m")
    if reasons:
        print("  미포함 사유: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common()))
    cov = {"nonmerge_commits": nonmerge, "in_patch": covered,
           "coverage_pct": round(pct, 1), "missing_reasons": dict(reasons),
           "missing": [{"hash": h, "reason": r} for h, r in missing]}
    cov["scope"] = sorted(ONLY) if ONLY else "all"
    name = "coverage-diffs.json" if not ONLY else "coverage-diffs-last-run.json"
    with open(os.path.join(dig, name), "w", encoding="utf-8") as fh:
        json.dump(cov, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
