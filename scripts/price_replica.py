"""시세 사본을 Turso 에 맞춘다 — 워크플로가 캐시를 꺼낸 뒤 부른다 (docs/infra.md 25.888).

    python scripts/price_replica.py sync --path .price-replica/prices.db             # 비었으면 진도 문을 보고 만든다
    python scripts/price_replica.py sync --path .price-replica/prices.db --no-build  # 비었으면 만들지 않는다

처음 만들 때는 시세 전부(2026-10-02 약 730만 행)를 한 번 읽는다 — Turso 월 읽기 진도 문(`turso_read_gate.decide`)이
허락할 때만. 못 만들거나 맞추다 실패해도 **0 으로 끝난다**: 사본은 `usable=0` 이 되고
작업은 Turso 에서 읽는다(예전과 같다).
출력 `changed=true` 면 워크플로가 캐시에 새로 넣는다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import client as backend  # noqa: E402
from batch.core import db  # noqa: E402
from batch.core import price_replica as rep  # noqa: E402

#: 처음 만들 때 진도 문에 내미는 예상 읽기 — 시세 `MAX(rowid)` 약 730만(2026-10-02)에 종목·검증 몫
BUILD_ESTIMATE = 7_600_000


def _gate_allows_build(remote) -> tuple[bool, str]:
    spec = importlib.util.spec_from_file_location("turso_read_gate", Path(__file__).with_name("turso_read_gate.py"))
    gate = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(gate)  # type: ignore[union-attr]
    남음 = db.remaining_read_budget_or_none(remote)
    used = None if 남음 is None else db.TURSO_MONTHLY_READ_LIMIT - 남음
    return gate.decide(used, BUILD_ESTIMATE, datetime.now(UTC))


def _output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="시세 사본 맞추기")
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--path", required=True)
    parser.add_argument("--no-build", action="store_true", help="사본이 비었으면 만들지 않는다")
    args = parser.parse_args()

    try:
        종류 = backend.resolved_backend()
        remote = backend.TursoClient(use_replica=False)
    except Exception as error:  # noqa: BLE001 — 사본은 곁다리다. 못 맞추면 작업이 Turso 에서 읽는다
        print(f"[시세 사본] Turso 에 붙지 못했다 ({error}) — 이번에는 쓰지 않는다")
        return 0
    conn = rep.open_replica(args.path)
    try:
        allow = False
        if not args.no_build and conn.execute("SELECT 1 FROM prices LIMIT 1").fetchone() is None and 종류 == "turso":
            allow, 글 = _gate_allows_build(remote)
            print(f"[시세 사본] 처음 만들기 — {글}")
        읽기_전 = remote.reads.total
        report = rep.sync(remote, conn, backend=종류, allow_build=allow)
        report["rows_read"] = remote.reads.total - 읽기_전
        print("[시세 사본] " + json.dumps(report, ensure_ascii=False))
        _output("changed", "true" if "stocks" in report else "false")  # 맞추기를 실제로 했으면 새로 넣는다
    except Exception as error:  # noqa: BLE001 — 맞추다 깨지면 쓰지 않는다고 적고 넘어간다(작업은 Turso 에서 읽는다)
        print(f"[시세 사본] 맞추지 못했다 ({error}) — 이번에는 쓰지 않는다")
        try:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('usable', '0')")
            conn.commit()
        except Exception as inner:  # noqa: BLE001 — 표시도 못 하면 신선도 시간이 지나 저절로 안 쓴다
            print(f"[시세 사본] 쓰지 않음 표시도 못 했다: {inner}")
        _output("changed", "false")
    finally:
        conn.close()
        remote.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
