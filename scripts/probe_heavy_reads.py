"""다음 배치가 쓸 무거운 읽기를 **쓰지 않고** 미리 돌려 본다 (docs/infra.md 25.7).

D1 은 질의당 30초, 응답 크기 제한은 확인되지 않았다 `[확인필요]`. Turso 에서 멀쩡하던 "한 번에
수십만 행" 질의가 D1 에서 깨질 수 있다. 하루 쓰기 한도가 막혀 있어도 읽기는 되므로, 실제 배치가
부르는 **같은 함수**를 그대로 불러 행 수·걸린 시간·실패 여부를 찍는다.

쓰기를 하지 않는다. 실패해도 다른 항목은 계속 본다.

실행
  python scripts/probe_heavy_reads.py --market KR
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core.client import TursoClient, backend  # noqa: E402
from batch.jobs import scores, signals, universe  # noqa: E402


def measure(name: str, fn) -> bool:
    started = time.monotonic()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 — 하나가 깨져도 나머지는 본다
        print(f"✗ {name}: {type(exc).__name__}: {str(exc)[:300]}")
        traceback.print_exc(limit=2)
        return False
    seconds = time.monotonic() - started
    size = len(result) if hasattr(result, "__len__") else "?"
    extra = ""
    if isinstance(result, dict) and result:
        first = next(iter(result.values()))
        if isinstance(first, tuple) and first and hasattr(first[0], "__len__"):
            extra = f", 종목당 계열 {len(first[0])}개"
        elif hasattr(first, "__len__"):
            extra = f", 종목당 {len(first)}개"
    print(f"✓ {name}: {size}개 ({seconds:.1f}초{extra})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="무거운 읽기 미리 보기 (쓰지 않는다)")
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--as-of", dest="as_of")
    args = parser.parse_args()
    as_of = args.as_of or datetime.now(UTC).date().isoformat()
    print(f"백엔드 {backend()}, 시장 {args.market}, 기준일 {as_of}")

    client = TursoClient()
    try:
        results = [
            measure("유니버스 20일 거래대금 (창 함수)", lambda: universe._avg_turnover_map(client, args.market, as_of)),
            measure("점수 계열 274일", lambda: scores.load_series(client, args.market, as_of, 274)),
            measure("신호 최근 60일 (창 함수)", lambda: signals.load_recent_prices(client, args.market, as_of)),
            measure("신호 후보", lambda: signals.load_candidates(client, args.market, as_of)),
        ]
    finally:
        client.close()
    print(f"{sum(results)}/{len(results)} 통과")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
