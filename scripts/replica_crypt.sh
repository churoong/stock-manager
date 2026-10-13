#!/usr/bin/env bash
# 시세 사본 암호화·복호화 (docs/infra.md 25.1249, docs/public-repo.md).
#
# **공개 저장소의 Actions 캐시에 KRX 시세를 평문으로 두지 않는다.** 시세 사본(`.price-replica/prices.db`, 25.888)은 국내 전 종목의
# 종가·수정종가·거래량을 담는다. 포크 PR 워크플로는 기본 가지의 캐시를 꺼낼 수 있어(승인된 뒤) 평문이면 KRX 제3자 제공 금지에 걸린다.
# 그래서 캐시에는 암호화본(`.price-replica-enc/prices.db.enc`)만 넣는다.
#
#   bash scripts/replica_crypt.sh encrypt   # .price-replica/prices.db → .price-replica-enc/prices.db.enc
#   bash scripts/replica_crypt.sh decrypt   # 반대. 없거나 못 풀면 사본 없이 간다(배치는 Turso 에서 읽는다)
#
# 키: `BACKUP_KEY` 가 있으면 그것, 없으면 `TURSO_AUTH_TOKEN` 에서 끌어낸 값(백업과 다른 접두어). 풀기는 차례로 다 해 본다.
# 키가 없으면 암호화하지 않고 **넣지도 않는다**(ready=false) — 평문을 캐시에 넣느니 사본을 버린다. 키 값은 찍지 않는다.
set -euo pipefail

mode="${1:-}"
PLAIN=".price-replica/prices.db"
ENC_DIR=".price-replica-enc"
ENC="$ENC_DIR/prices.db.enc"

out() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; fi
}

keys=()
srcs=()
if [ -n "${BACKUP_KEY:-}" ]; then
  keys+=("$(printf 'stock-manager-replica-v1:%s' "$BACKUP_KEY" | sha256sum | cut -d' ' -f1)")
  srcs+=("BACKUP_KEY")
fi
if [ -n "${TURSO_AUTH_TOKEN:-}" ]; then
  keys+=("$(printf 'stock-manager-replica-v1:%s' "$TURSO_AUTH_TOKEN" | sha256sum | cut -d' ' -f1)")
  srcs+=("TURSO_AUTH_TOKEN 에서 끌어낸 키")
fi
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  for k in "${keys[@]+"${keys[@]}"}"; do echo "::add-mask::$k"; done
fi
opts=(-aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -pass env:RC_PASS)

case "$mode" in
  encrypt)
    out "ready=false"
    if [ ! -f "$PLAIN" ]; then
      echo "[시세 사본] 넣을 사본이 없습니다"
      exit 0
    fi
    if [ ${#keys[@]} -eq 0 ]; then
      echo "[시세 사본] 암호 키가 없어(BACKUP_KEY·TURSO_AUTH_TOKEN 둘 다 비어 있음) 캐시에 넣지 않습니다" >&2
      exit 0
    fi
    mkdir -p "$ENC_DIR"
    RC_PASS="${keys[0]}" openssl enc -e -salt "${opts[@]}" -in "$PLAIN" -out "$ENC"
    echo "[시세 사본] 암호화했습니다 (키: ${srcs[0]})"
    out "ready=true"
    ;;
  decrypt)
    if [ ! -f "$ENC" ]; then
      echo "[시세 사본] 암호화본이 없습니다 — 옛 평문 캐시가 있으면 그것을, 없으면 사본 없이 갑니다"
      exit 0
    fi
    mkdir -p "$(dirname "$PLAIN")"
    for i in "${!keys[@]}"; do
      if RC_PASS="${keys[$i]}" openssl enc -d "${opts[@]}" -in "$ENC" -out "$PLAIN.tmp" 2>/dev/null \
        && python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); sys.exit(0 if c.execute('PRAGMA quick_check').fetchone()[0]=='ok' else 1)" "$PLAIN.tmp" 2>/dev/null; then
        mv "$PLAIN.tmp" "$PLAIN"
        echo "[시세 사본] 풀었습니다 (키: ${srcs[$i]})"
        exit 0
      fi
      rm -f "$PLAIN.tmp"
    done
    echo "[시세 사본] 가진 키로 풀지 못했습니다 — 사본 없이 갑니다(배치는 Turso 에서 읽는다)"
    ;;
  *)
    echo "쓰는 법: replica_crypt.sh encrypt|decrypt" >&2
    exit 2
    ;;
esac
