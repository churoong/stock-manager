"""백업 복구 리허설 (docs/backup.md 6장, docs/infra.md 25.946) — 백업은 복구해 봐야 백업이다."""

from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    # `dataclass` 는 정의 모듈을 `sys.modules` 에서 찾는다 — 등록하지 않고 두 번 읽으면 두 번째부터 깨진다
    if "restore_drill" in sys.modules:
        return sys.modules["restore_drill"]
    spec = importlib.util.spec_from_file_location("restore_drill", ROOT / "scripts" / "restore_drill.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["restore_drill"] = module
    spec.loader.exec_module(module)
    return module


def _backup(tmp_path: Path, trades_mark: int = 2, broken: bool = False) -> Path:
    """`scripts/backup_db.py` 가 쓰는 모양 그대로의 작은 백업. 값 안의 `;`·줄바꿈·따옴표도 넣는다."""
    줄 = [
        "-- stock-manager 백업 (essential)",
        "-- 되살리기: gunzip -c <파일> | sqlite3 restored.db",
        "-- stock_key 1\t005930\tKOSPI\tKR\tKRW",
        "PRAGMA foreign_keys = OFF;",
        "BEGIN;",
        "-- stocks",
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't');",
        "-- rows stocks 1",
        "-- trades",
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, memo,"
        " created_at, updated_at) VALUES (1, 1, 'buy', '2026-09-01', 70000, 10, 'KRW', 1, 'none', '첫줄; 둘째''줄\n-- rows trades 99',"
        " 't', 't');",
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " created_at, updated_at) VALUES (2, 1, 'sell', '2026-09-02', 71000, 5, 'KRW', 1, 'none', 't', 't');",
        f"-- rows trades {trades_mark}",
        "-- settings",
        "INSERT INTO settings (key, value, updated_at) VALUES ('fees', '{\"kr\": 0.015}', 't');",
        "-- rows settings 1",
    ]
    if broken:
        줄.append("INSERT INTO settings (key, value, updated_at) VALUES ('fees', 'dup', 't');")  # 같은 키 — 백업이 이상하다
    줄.append("COMMIT;")
    path = tmp_path / "stock-manager-essential-2026-10-05.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("\n".join(줄) + "\n")
    return path


def test_온전한_백업은_통과하고_값_안의_표식은_속지_않는다(tmp_path: Path) -> None:
    d = load()
    r = d.drill(_backup(tmp_path), work_db=str(tmp_path / "w.db"))
    assert r.ok, r.lines()
    assert r.expected == {"stocks": 1, "trades": 2, "settings": 1}
    assert r.actual["trades"] == 2  # 메모 속 "-- rows trades 99" 를 표식으로 읽지 않았다
    assert "복구 리허설 통과" in r.lines()[-1]


def test_행_수가_어긋나면_실패하고_어느_표인지_말한다(tmp_path: Path) -> None:
    d = load()
    r = d.drill(_backup(tmp_path, trades_mark=3), work_db=str(tmp_path / "w.db"))
    assert not r.ok
    assert r.mismatches == [("trades", 3, 2)]
    assert any("✗ trades" in 줄 for 줄 in r.lines())


def test_겹치는_행은_백업이_이상한_것이다(tmp_path: Path) -> None:
    d = load()
    r = d.drill(_backup(tmp_path, broken=True), work_db=str(tmp_path / "w.db"))
    assert not r.ok and r.errors and r.errors[0].startswith("settings:")


def test_행_수_표식이_없는_옛_백업은_대조할_수_없다고_말한다(tmp_path: Path) -> None:
    d = load()
    path = tmp_path / "old.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("BEGIN;\nINSERT INTO settings (key, value, updated_at) VALUES ('a', 'b', 't');\nCOMMIT;\n")
    r = d.drill(path, work_db=str(tmp_path / "w.db"))
    assert not r.ok and any("표식" in e for e in r.errors)


def test_운영_DB_에_닿지_않는다(tmp_path: Path, monkeypatch) -> None:
    """리허설은 로컬 SQLite 에만 쓴다 — 운영 백엔드를 열면 안 된다(`--record-run` 때만, 그것도 한 줄)."""
    d = load()
    import batch.core.client as backend

    monkeypatch.setattr(backend, "TursoClient", lambda *a, **k: (_ for _ in ()).throw(AssertionError("운영 DB 를 열었다")))
    assert d.drill(_backup(tmp_path), work_db=str(tmp_path / "w.db")).ok


def test_마이그레이션이_미리_채운_표는_덮어쓰고_마이그레이션_밖_표는_대조하지_않는다(tmp_path: Path) -> None:
    """네 번째 실행(2026-10-04): 0025 의 씨앗 행(health/ALL)과 겹쳐 UNIQUE, `step0_check` 는 마이그레이션이 안 만든다."""
    d = load()
    assert "cron_heartbeats" in d.PREFILLED  # 손으로 적은 목록이 아니라 마이그레이션 SQL 에서 읽는다
    path = tmp_path / "b.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(
            "BEGIN;\n"
            "INSERT INTO cron_heartbeats (job, market, called_at, outcome, detail, calls_today, day)"
            " VALUES ('health', 'ALL', 't', 'ok', '{}', 1, 'd');\n-- rows cron_heartbeats 1\n"
            "INSERT INTO step0_check (id, checked_at, env) VALUES (1, 't', '{}');\n-- rows step0_check 1\n"
            "COMMIT;\n"
        )
    r = d.drill(path, work_db=str(tmp_path / "w.db"))
    assert r.ok, r.lines()
    assert r.actual["cron_heartbeats"] == 1 and r.skipped == ["step0_check"]
    assert any("대조 안 함" in 줄 for 줄 in r.lines())
