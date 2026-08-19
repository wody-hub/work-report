#!/usr/bin/env python3
"""지시문에서 자격증명을 찾아 마스킹한다.

두 갈래로 접근한다.

1. 구조화된 토큰 — 형태 자체가 고유하다. 패턴으로 값까지 정확히 잡는다.
   OAuth 토큰, JWT, API 키, 개인키 블록, AWS/Slack 키 등.

2. 자유형 비밀번호 — 값에 형태가 없다. `!unecom1231!` 은 어떤 일반 패턴에도
   안 걸린다. 그래서 둘을 쓴다.
   a) 형태 판정: 키워드('비밀번호','password') 60자 이내면 문자+기호만으로도
      인정하고, 키워드가 없으면 문자+숫자+'!' 를 모두 요구한다. 한국어는 값이
      키워드보다 먼저 오는 경우가 많아 양방향으로 본다.
   b) 사용자 지정 목록(secrets.txt): 실제 비밀번호를 한 번 적어두면 전 구간에서
      정확히 치환된다. 가장 확실한 방법이다.

CLI 로 단독 점검도 된다:
  mask.py --test <파일.jsonl>     마스킹 결과와 유형별 건수를 출력
"""
import os
import re
import sys

MASK_IP = os.environ.get("WORK_MASK_IP", "0") not in ("0", "", "false", "no")
SECRETS_FILE = os.environ.get(
    "WORK_MASK_FILE", os.path.expanduser("~/.config/work-report/secrets.txt"))


def load_denylist():
    """사용자가 지정한 비밀 문자열. 긴 것부터 치환한다 (부분 치환 방지)."""
    out = []
    if os.path.exists(SECRETS_FILE):
        for line in open(SECRETS_FILE, encoding="utf-8", errors="replace"):
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return sorted(set(out), key=len, reverse=True)


DENY = load_denylist()

# ── 1. 구조화된 토큰 ──────────────────────────────────────────────────────
STRUCTURED = [
    ("PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S)),
    ("SSH_PUBKEY", re.compile(r"ssh-(?:rsa|ed25519|dss) [A-Za-z0-9+/=]{40,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("GOOGLE_OAUTH", re.compile(r"ya29\.[A-Za-z0-9._\-]{20,}")),
    ("GOOGLE_REFRESH", re.compile(r"\b1//[A-Za-z0-9_\-]{20,}")),
    ("OPENAI_KEY", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("BEARER", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    # key=value 형태. 값만 지우고 키는 남긴다 (문맥이 필요하므로)
    ("KV_SECRET", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
        r"client[_-]?secret|db[_-]?pass)\s*[:=]\s*"
        r"(?P<v>\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s,;'\"()]{3,})")),
    ("SSHPASS", re.compile(r"(?i)sshpass\s+-p\s*(?P<v>\"[^\"]+\"|'[^']+'|\S+)")),
]

# ── 2. 자유형 비밀번호 ────────────────────────────────────────────────────
# 이 키워드가 레코드에 있으면 비밀번호가 평문으로 적혀 있을 가능성이 크다
CRED_KEYWORD = re.compile(
    r"(?i)비밀번호|패스워드|비번|암호|접속정보|계정정보|자격증명|password|passwd|credential")

# 비밀번호 후보 토큰. 넓게 뽑은 뒤 looks_like_password() 로 거른다.
PW_CANDIDATE = re.compile(
    r"(?<![\w/@.\-])(?P<v>[A-Za-z0-9!@#$%^&*_+.\-]{6,40})(?![\w@.\-])")
SPECIAL = set("!@#$%^&*")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
# 코드·문서에서 흔한 모양들 — 비밀번호가 아니다
NOT_PW = re.compile(
    r"^@"                       # @SpringBootTest 같은 어노테이션·멘션
    r"|\*\*"                    # **볼드** 마크다운
    r"|\$\$"                    # $SpringCGLIB$$0
    r"|^-{1,2}[A-Za-z]"         # --flag, -v
    r"|^\d+(\.\d+)+$"           # 1.2.3 버전
    r"|^[A-Z_]{4,}$"            # ENV_VAR_NAME
    r"|^[a-z]+([._-][a-z]+)+$"  # snake.case.name
    r"|@[\w.\-]+\.\w{2,}$"      # 이메일 / user@host — 비밀번호가 아니다
    r"|\$\d+$"                  # doFilterInternal$3 같은 컴파일러 생성 심볼
    r"|^#[0-9a-fA-F]{3,8}$"     # #34d399 CSS 색상
    r"|\$Proxy\d"              # com.sun.proxy.$Proxy110
    r"|@[0-9a-f]{6,}$"          # AppClassLoader@18b4aac2 객체 해시
    r"|%[0-9A-F]{2}"            # URL 인코딩된 문자열
    r"|\*"                      # 05-*-PLAN.md, V5__*.sql 같은 글롭 패턴
)


def looks_like_password(v, near_keyword=False):
    """비밀번호처럼 보이는가.

    두 단계로 나눈다. 실측 결과 이 경계에서 오탐이 사라진다.

    키워드 근처 (느슨) : 문자 + 기호(!@#$%^&*). 숫자는 없어도 된다.
                         'riskzero!!!!' 같은 실제 사례가 있다.
    키워드 없음 (엄격) : 문자 + 숫자 + '!' 를 모두 포함해야 한다.
                         '!' 를 요구하지 않으면 CSS 색상(#34d399),
                         Java 프록시($Proxy110), URL 파라미터(1&size)가
                         전부 걸린다. 실제 비밀번호는 모두 '!' 를 가졌다.
    """
    if len(v) < 6 or len(v) > 40 or NOT_PW.search(v) or UUID_RE.match(v):
        return False
    if not any(c.isalpha() for c in v):
        return False
    has_digit = any(c.isdigit() for c in v)
    if near_keyword:
        return any(c in SPECIAL for c in v)
    return has_digit and "!" in v

IP_RE = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?::\d+)?(?![\w.])")
# 오탐 제거: 버전번호처럼 보이거나 명백한 로컬 주소는 마스킹하지 않는다
IP_KEEP = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "1.1.1.1", "8.8.8.8"}


def _tag(kind):
    return f"[REDACTED:{kind}]"


def mask_text(text):
    """(마스킹된 텍스트, {유형: 건수}) 반환."""
    if not text:
        return text, {}
    hits = {}

    def bump(kind, n=1):
        hits[kind] = hits.get(kind, 0) + n

    # 사용자 지정 목록이 가장 확실하다 — 먼저 적용
    for s in DENY:
        if s in text:
            bump("DENYLIST", text.count(s))
            text = text.replace(s, _tag("SECRET"))

    for kind, rx in STRUCTURED:
        if kind in ("KV_SECRET", "SSHPASS"):
            def sub(m, kind=kind):
                bump(kind)
                v = m.group("v")
                return m.group(0).replace(v, _tag("SECRET"), 1)
            text, n = rx.subn(sub, text)
        else:
            def sub(m, kind=kind):
                bump(kind)
                return _tag(kind)
            text, n = rx.subn(sub, text)

    # 자유형 비밀번호. 키워드가 있으면 느슨하게, 없으면 엄격하게 본다.
    kw = [m.start() for m in CRED_KEYWORD.finditer(text)]

    def sub_pw(m):
        v = m.group("v")
        if "REDACTED" in v:
            return m.group(0)
        # 키워드에서 60자 이내면 판정을 느슨하게 한다
        near = any(abs(m.start() - k) <= 60 for k in kw)
        if not looks_like_password(v, near):
            return m.group(0)
        bump("PASSWORD")
        return _tag("SECRET")

    text = PW_CANDIDATE.sub(sub_pw, text)

    if MASK_IP:
        def sub(m):
            ip = m.group(1)
            if ip in IP_KEEP:
                return m.group(0)
            octets = ip.split(".")
            if any(int(o) > 255 for o in octets):
                return m.group(0)
            bump("IP")
            return m.group(0).replace(ip, _tag("IP"), 1)
        text = IP_RE.sub(sub, text)

    return text, hits


def _test(path):
    import json
    from collections import Counter
    total = Counter()
    changed = 0
    samples = []
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        m, h = mask_text(r.get("text", ""))
        if h:
            changed += 1
            total.update(h)
            if len(samples) < 6:
                samples.append((r.get("timestamp", "")[:16], m))
    print(f"마스킹된 레코드: {changed}건")
    for k, v in total.most_common():
        print(f"  {k:<24} {v}")
    print()
    for ts, m in samples:
        i = m.find("[REDACTED")
        print(f"[{ts}] …{m[max(0, i-70):i+60]}…".replace("\n", " "))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        _test(sys.argv[2])
    else:
        print(__doc__)
