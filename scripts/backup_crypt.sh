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

# 키 후보 — 암호화는 첫째 것으로, 풀기는 **차례로 다 해 본다** (25.1136, 백업 감사 재현). 예전엔 풀기도 첫째 하나만 써서
# `BACKUP_KEY` 를 넣는 순간 그 전(토큰 키로 잠근) 백업 90일치가 워크플로로 안 열렸다
keys=()
srcs=()
if [ -n "${BACKUP_KEY:-}" ]; then
  keys+=("$BACKUP_KEY")
  srcs+=("BACKUP_KEY")
fi
if [ -n "${TURSO_AUTH_TOKEN:-}" ]; then
  keys+=("$(printf 'stock-manager-backup-v1:%s' "$TURSO_AUTH_TOKEN" | sha256sum | cut -d' ' -f1)")
  srcs+=("TURSO_AUTH_TOKEN 에서 끌어낸 키")
fi
if [ ${#keys[@]} -eq 0 ]; then
  echo "백업 암호 키가 없습니다(BACKUP_KEY·TURSO_AUTH_TOKEN 둘 다 비어 있음) — 평문으로 올리지 않고 멈춥니다" >&2
  exit 1
fi
# Actions 로그에 끌어낸 값이 섞이지 않게 가린다(밖에서는 아무 뜻 없는 줄)
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  for k in "${keys[@]}"; do echo "::add-mask::$k"; done
fi
BK_PASS="${keys[0]}"
src="${srcs[0]}"
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
      done_one=""
      for i in "${!keys[@]}"; do
        export BK_PASS="${keys[$i]}"
        # 틀린 키도 드물게(약 1/256) 채움 검사를 지나 쓰레기를 낸다 — gzip 검사까지 지나야 맞는 키로 본다
        if openssl enc -d "${opts[@]}" -in "$f" -out "${f%.enc}" 2>/dev/null && gzip -t "${f%.enc}" 2>/dev/null; then
          echo "복호화: $(basename "${f%.enc}") (키: ${srcs[$i]})"
          done_one=1
          break
        fi
        rm -f "${f%.enc}"
      done
      if [ -z "$done_one" ]; then
        echo "복호화 실패: $(basename "$f") — 가진 키(${srcs[*]}) 어느 것으로도 열리지 않습니다. 백업 당시의 키(옛 Turso 토큰 등)가 필요합니다" >&2
        exit 1
      fi
    done
    ;;
  *)
    echo "모드는 encrypt 또는 decrypt: $mode" >&2
    exit 2
    ;;
esac
