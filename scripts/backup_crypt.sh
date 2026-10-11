#!/usr/bin/env bash
# 백업 파일 암호화·복호화 (docs/backup.md 2.1, docs/infra.md 25.1125).
#
# **공개 저장소의 Actions 아티팩트는 GitHub 에 로그인한 누구나 내려받는다.** 2026-10-07 공개 전환 뒤에도 백업은
# 평문(.sql.gz)으로 올라가 매매·배당·설정(=보유·금액)이 그대로 열렸다. 그래서 올리기 전에 암호화하고 평문은 지운다.
#
#   bash scripts/backup_crypt.sh encrypt backup/   # *.sql.gz → *.sql.gz.enc (평문 삭제)
#   bash scripts/backup_crypt.sh decrypt backup/   # *.sql.gz.enc → *.sql.gz (옛 평문 백업만 있으면 아무것도 안 한다)
#
# 키: 시크릿 `BACKUP_KEY` 가 있으면 그것, 없으면 `TURSO_AUTH_TOKEN` 에서 끌어낸 값(sha256). 둘 다 없으면 **멈춘다** —
# 평문을 올리느니 백업이 실패하는 편이 낫다(실패는 신선도 칸이 알린다). 키 값은 찍지 않는다.
# Turso 토큰을 바꾸면 그 전 백업은 옛 토큰으로만 열린다 — 오래 둘 키는 `BACKUP_KEY` 다(handoff).
set -euo pipefail
shopt -s nullglob

mode="${1:-}"
dir="${2:-}"
if [ -z "$mode" ] || [ -z "$dir" ]; then
  echo "쓰는 법: backup_crypt.sh encrypt|decrypt 디렉터리" >&2
  exit 2
fi

if [ -n "${BACKUP_KEY:-}" ]; then
  BK_PASS="$BACKUP_KEY"
  src="BACKUP_KEY"
elif [ -n "${TURSO_AUTH_TOKEN:-}" ]; then
  BK_PASS=$(printf 'stock-manager-backup-v1:%s' "$TURSO_AUTH_TOKEN" | sha256sum | cut -d' ' -f1)
  src="TURSO_AUTH_TOKEN 에서 끌어낸 키"
else
  echo "백업 암호 키가 없습니다(BACKUP_KEY·TURSO_AUTH_TOKEN 둘 다 비어 있음) — 평문으로 올리지 않고 멈춥니다" >&2
  exit 1
fi
# Actions 로그에 끌어낸 값이 섞이지 않게 가린다(밖에서는 아무 뜻 없는 줄)
if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::add-mask::$BK_PASS"; fi
export BK_PASS

opts=(-aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -pass env:BK_PASS)
case "$mode" in
  encrypt)
    files=("$dir"/*.sql.gz)
    if [ ${#files[@]} -eq 0 ]; then
      echo "암호화할 백업(.sql.gz)이 없습니다: $dir" >&2
      exit 1
    fi
    for f in "${files[@]}"; do
      openssl enc -e -salt "${opts[@]}" -in "$f" -out "$f.enc"
      rm -f "$f"
      echo "암호화: $(basename "$f").enc (키: $src)"
    done
    ;;
  decrypt)
    for f in "$dir"/*.sql.gz.enc; do
      if ! openssl enc -d "${opts[@]}" -in "$f" -out "${f%.enc}"; then
        rm -f "${f%.enc}"
        echo "복호화 실패: $(basename "$f") — 키($src)가 백업 때와 다릅니다. Turso 토큰을 바꾼 뒤라면 백업 당시의 키가 필요합니다" >&2
        exit 1
      fi
      echo "복호화: $(basename "${f%.enc}") (키: $src)"
    done
    ;;
  *)
    echo "모드는 encrypt 또는 decrypt: $mode" >&2
    exit 2
    ;;
esac
