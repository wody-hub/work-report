#!/usr/bin/env bash
# AI 코딩 세션 로그 → 날짜별 업무 실적 데이터
# 업로드는 하지 않는다. 결과 폴더에서 직접 올린다.
#
#   run.sh                   전체 기간 재생성 + 올릴 대상·민감정보 보고
#   run.sh --today           오늘 날짜만 갱신 (일상 운영용)
#   run.sh --date 2026-08-19 특정 날짜만 갱신 (콤마로 여러 날 가능)
#   run.sh --days 3          최근 3일만 갱신
#   run.sh --open            위 + 파일 탐색기로 결과 폴더 열기
#   run.sh --mark-uploaded   현재 상태를 '업로드 완료'로 기록 (올린 뒤 실행)
#   run.sh --with-raw        원본 JSONL 까지 복사 (수 GB. 외부 공유 금지)
#   run.sh --init            세션 로그를 스캔해 설정 후보를 제시 (최초 1회)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${WORK_REPORT_CONFIG:-$HOME/.config/work-report/config.env}"

# --init 은 설정이 없어도 동작해야 하므로 설정 검사보다 앞에서 처리한다
for a in "$@"; do
  if [ "$a" = "--init" ]; then
    exec python3 "$HERE/init_config.py" "${@:2}"
  fi
done

if [ ! -f "$CONFIG" ]; then
  cat >&2 <<EOF
설정 파일이 없습니다: $CONFIG

수집할 경로를 정해야 합니다. 세션 로그를 스캔해 후보를 뽑아 드립니다:

  $(basename "${BASH_SOURCE[0]}") --init

후보를 보고 경로를 고른 뒤 아래처럼 설정을 만듭니다:

  $(basename "${BASH_SOURCE[0]}") --init --write "경로1:경로2" "결과폴더" "내이메일"

이미 설정을 손으로 만들 준비가 됐다면 $CONFIG 에
WORK_TARGETS 와 WORK_DIR 만 채워도 됩니다.
EOF
  exit 1
fi
. "$CONFIG"

: "${WORK_TARGETS:?config.env 에 WORK_TARGETS 를 설정하세요 (콜론 구분 경로)}"
: "${WORK_DIR:?config.env 에 WORK_DIR 를 설정하세요 (결과를 둘 폴더)}"
export WORK_TARGETS
export WORK_TZ_OFFSET="${WORK_TZ_OFFSET:-9}"
export WORK_MAX_CHARS="${WORK_MAX_CHARS:-10000}"
export WORK_MASK="${WORK_MASK:-1}"
export WORK_MASK_IP="${WORK_MASK_IP:-0}"
export WORK_MASK_FILE="${WORK_MASK_FILE:-$HOME/.config/work-report/secrets.txt}"
export WORK_GIT_AUTHORS="${WORK_GIT_AUTHORS:-}"
export WORK_GIT_SINCE="${WORK_GIT_SINCE:-}"
export WORK_GIT_DEPTH="${WORK_GIT_DEPTH:-4}"
export WORK_CODE="${WORK_CODE:-1}"
export WORK_CODE_MAX_FILE_LINES="${WORK_CODE_MAX_FILE_LINES:-500}"
export WORK_CODE_MAX_DAY_KB="${WORK_CODE_MAX_DAY_KB:-2048}"
export WORK_CODE_EXT="${WORK_CODE_EXT:-}"

EXPORT_DIR="${EXPORT_DIR:-$WORK_DIR/export}"
BYDATE_DIR="${BYDATE_DIR:-$WORK_DIR/by-date}"
LOG_DIR="${LOG_DIR:-$WORK_DIR/logs}"
STATE="${STATE:-$WORK_DIR/.last-upload.sha256}"

DO_OPEN=0; MARK=0; RAW_FLAG=""; ONLY=""
TZ_H="${WORK_TZ_OFFSET:-9}"
# 설정한 시간대 기준의 날짜 (UTC 에 오프셋을 더해 계산)
today() { date -u -v+"${TZ_H}"H '+%Y-%m-%d' 2>/dev/null \
          || date -u -d "+${TZ_H} hours" '+%Y-%m-%d'; }
daysago() { date -u -v+"${TZ_H}"H -v-"$1"d '+%Y-%m-%d' 2>/dev/null \
            || date -u -d "+${TZ_H} hours -$1 days" '+%Y-%m-%d'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --open) DO_OPEN=1; shift ;;
    --mark-uploaded) MARK=1; shift ;;
    --with-raw) RAW_FLAG="--with-raw"; shift ;;
    --today) ONLY="$(today)"; shift ;;
    --date) [ $# -ge 2 ] || { echo "--date 에 날짜가 필요합니다" >&2; exit 2; }
            ONLY="$2"; shift 2 ;;
    --date=*) ONLY="${1#*=}"; shift ;;
    --days) [ $# -ge 2 ] || { echo "--days 에 숫자가 필요합니다" >&2; exit 2; }
            n="$2"; ONLY=""
            for i in $(seq 0 $((n-1))); do
              ONLY="${ONLY:+$ONLY,}$(daysago "$i")"
            done; shift 2 ;;
    --help|-h) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m실패: %s\033[0m\n' "$*" >&2; exit 1; }

reveal() {  # macOS / Linux 공통
  if command -v open >/dev/null; then open "$1"
  elif command -v xdg-open >/dev/null; then xdg-open "$1"
  else echo "  (폴더를 직접 열어주세요: $1)"; fi
}

# 날짜 폴더별 내용 해시 — 신규 날짜와 내용이 바뀐 날짜를 잡는다
snapshot() {
  while IFS= read -r d; do
    day="$(basename "$d")"
    h="$(find "$d" -type f -exec shasum -a 256 {} + | awk '{print $1}' \
         | sort | shasum -a 256 | awk '{print $1}')"
    printf '%s %s\n' "$day" "$h"
  done < <(find "$BYDATE_DIR" -mindepth 1 -maxdepth 1 -type d -name '2*' | sort)
}

# --mark-uploaded 는 수집을 다시 하지 않고 현재 결과만 기록한다
if [ "$MARK" -eq 1 ]; then
  [ -d "$BYDATE_DIR" ] || die "결과 폴더가 없습니다: $BYDATE_DIR"
  snapshot > "$STATE" || die "상태 기록 실패"
  say "업로드 완료로 기록 — 날짜 $(wc -l < "$STATE" | tr -d ' ')개"
  echo "  다음 실행부터는 이후 변경분만 보고합니다."
  exit 0
fi

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date '+%Y-%m-%d_%H%M%S').log"
exec > >(tee -a "$LOG") 2>&1

say "[1/6] 세션 로그 수집"
python3 "$HERE/collect_sessions.py" "$EXPORT_DIR" $RAW_FLAG || die "수집 단계 실패"

say "[2/6] git 커밋 수집"
python3 "$HERE/collect_commits.py" "$EXPORT_DIR" || echo "  (커밋 수집 실패 — 지시문만 정리합니다)"

export WORK_ONLY_DATES="$ONLY"

say "[3/6] 코드 변경 수집${ONLY:+  (대상: $ONLY)}"
python3 "$HERE/collect_diffs.py" "$EXPORT_DIR" || echo "  (코드 수집 실패 — 계속합니다)"

say "[4/6] 날짜별 정리${ONLY:+  (대상: $ONLY)}"
python3 "$HERE/split_by_date.py" "$EXPORT_DIR" "$BYDATE_DIR" \
  ${ONLY:+--only="$ONLY"} || die "날짜별 정리 실패"
find "$BYDATE_DIR" -name '.DS_Store' -delete 2>/dev/null

say "[5/6] 올릴 대상"
NEW_STATE="$(mktemp)"; CHANGED="$(mktemp)"
trap 'rm -f "$NEW_STATE" "$CHANGED"' EXIT
snapshot > "$NEW_STATE"

if [ -f "$STATE" ]; then
  # comm 은 줄 전체를 비교한다 → 신규 날짜 + 내용이 바뀐 날짜 둘 다 잡힌다.
  # join 을 쓰면 첫 필드(날짜)만 비교해서 내용 변경을 놓친다.
  comm -23 <(sort "$NEW_STATE") <(sort "$STATE") | awk '{print $1}' | sort -u > "$CHANGED"
else
  awk '{print $1}' "$NEW_STATE" > "$CHANGED"
  echo "  (업로드 기록 없음 → 전체가 대상)"
fi

N_CHANGED="$(wc -l < "$CHANGED" | tr -d ' ')"
N_TOTAL="$(wc -l < "$NEW_STATE" | tr -d ' ')"
echo "  전체 날짜 ${N_TOTAL}개 / 올릴 대상 ${N_CHANGED}개"
while IFS= read -r day; do
  n=$(grep -c '^## ' "$BYDATE_DIR/$day/instructions.md" 2>/dev/null || echo 0)
  printf '    %s  지시 %s건\n' "$day" "$n"
done < "$CHANGED"

say "[6/6] 민감정보 점검 (올릴 대상만)"
PATTERNS='(ssh-rsa|BEGIN [A-Z ]*PRIVATE KEY|AuthKey_[A-Za-z0-9]+\.p8|ya29\.[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|refresh_token|password[[:space:]]*[:=]|passwd[[:space:]]*[:=]|secret[[:space:]]*[:=]|api[_-]?key[[:space:]]*[:=]|Bearer [A-Za-z0-9._-]{20,}|비밀번호|패스워드|비번|암호는|접속정보|계정정보|sshpass|sudo su|ssh [a-z_][a-z0-9_-]*@|[0-9]{1,3}(\.[0-9]{1,3}){3})'
HITS=0
while IFS= read -r day; do
  c=$(grep -aEoh "$PATTERNS" "$BYDATE_DIR/$day/instructions.md" 2>/dev/null \
      | cut -c1-60 | sort -u | head -8)
  if [ -n "$c" ]; then
    HITS=$((HITS+1))
    printf '    \033[33m%s\033[0m\n' "$day"
    printf '      %s\n' $c
  fi
done < "$CHANGED"
if [ "$HITS" -eq 0 ]; then
  echo "  패턴 일치 없음"
else
  printf '  \033[33m%s개 날짜에서 일치. 외부 공유 가능한지 확인하세요.\033[0m\n' "$HITS"
  echo "  (IP 는 버전번호·좌표와도 겹쳐 오탐이 섞입니다. 토큰·키는 오탐이 드뭅니다)"
fi

say "결과"
echo "  폴더:  $BYDATE_DIR"
echo "  로그:  $LOG"
if [ "$N_CHANGED" -gt 0 ]; then
  echo
  echo "  위 ${N_CHANGED}개 날짜 폴더를 올리세요."
  echo "  올린 뒤:  run.sh --mark-uploaded"
fi

[ "$DO_OPEN" -eq 1 ] && reveal "$BYDATE_DIR"
exit 0
