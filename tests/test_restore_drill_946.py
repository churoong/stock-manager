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
            # 사람이 넣은 표 표식 (25.1141 — 없으면 리허설이 실패한다)
            "INSERT INTO settings (key, value, updated_at) VALUES ('a', 'b', 't');\n-- rows settings 1\n-- rows trades 0\n"
            "COMMIT;\n"
        )
    r = d.drill(path, work_db=str(tmp_path / "w.db"))
    assert r.ok, r.lines()
    assert r.actual["cron_heartbeats"] == 1 and r.skipped == ["step0_check"]
    assert any("대조 안 함" in 줄 for 줄 in r.lines())


def test_값_안의_CR_은_되살릴_때_그대로다(tmp_path: Path) -> None:
    """`gzip.open(…, "rt")` 의 줄바꿈 자동 변환이 메모의 `\\r\\n`·`\\r` 을 `\\n` 으로 바꿨다 (docs/infra.md 25.1136, 백업 감사 재현).
    행 수는 같아 리허설이 못 잡았다."""
    from scripts import restore_backup as restore

    값 = "1차 매수\r\n손절 -7%\r끝"
    path = tmp_path / "b.sql.gz"
    with gzip.open(path, "wb") as f:
        f.write(f"INSERT INTO trades (memo) VALUES ('{값}');\n".encode())
    (_, sql), = restore.statements(path)
    assert 값 in sql
    (_, sql2), _주석 = list(load()._stream(path))  # 마지막은 주석 묶음
    assert 값 in sql2


def test_매매·설정이_빠졌거나_비면_통과시키지_않는다(tmp_path: Path) -> None:
    """빈 DB 에서 받은 백업·구획을 지운 백업도 `ok` 였다 (docs/infra.md 25.1141, 백업 감사 재현)."""
    d = load()
    assert d.사람표_점검({"trades": 2, "settings": 1}) == []
    assert d.사람표_점검({"trades": 0, "settings": 3}) == []  # 매매를 다 지운 사람도 있다 — 설정이 있으면 통과
    assert d.사람표_점검({"trades": 0, "settings": 0})[0].startswith("매매·설정이 모두 0행")
    assert "trades" in d.사람표_점검({"settings": 1})[0]
    path = tmp_path / "empty.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("BEGIN;\n-- rows trades 0\n-- rows settings 0\nCOMMIT;\n")
    assert not d.drill(path, work_db=str(tmp_path / "w.db")).ok


def test_only_오타는_실패한다(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    from scripts import restore_backup as restore

    path = _backup(tmp_path)
    assert restore.run(path, {"trade"}, None, dry_run=True) == 1
    assert "trade" in capsys.readouterr().out
    assert restore.run(path, {"trades"}, None, dry_run=True) == 0


def test_표_가운데서_잘리면_skip_rows_로_이어_넣는다(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """다음 실행이 늘 같은 앞 N 행만 보내 큰 표가 영영 다 들어가지 않았다 (docs/infra.md 25.1143, 백업 감사 재현)."""
    from scripts import restore_backup as restore

    path = tmp_path / "b.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("BEGIN;\n" + "".join(
            f"INSERT INTO settings (key, value, updated_at) VALUES ('k{i:03d}', 'v', 't');\n" for i in range(250)
        ) + "-- rows settings 250\nCOMMIT;\n")
    assert restore.run(path, {"settings"}, 100, dry_run=True) == 0
    assert "다음 실행: --only settings --skip-rows 100" in capsys.readouterr().out
    assert restore.run(path, {"settings"}, 100, dry_run=True, skip_rows=100) == 0
    assert "--skip-rows 200" in capsys.readouterr().out
    assert restore.run(path, {"settings"}, 100, dry_run=True, skip_rows=200) == 0
    out = capsys.readouterr().out
    assert "넣을 행 50" in out and "다음 실행" not in out
    assert restore.run(path, None, 100, dry_run=True, skip_rows=100) == 1  # 표를 고르지 않으면 쓸 수 없다


def test_D1_예산으로_잘려도_skip_rows_로_이어_넣는다(monkeypatch, capsys) -> None:  # noqa: ANN001
    """예산 길도 오프셋을 말한다 (docs/infra.md 25.1434, 백업 감사) — 예전엔 "--only T" 만 말해 같은 앞 행만 되풀이했다"""
    from batch.core import client as backend
    from batch.core import db as core_db
    from scripts import restore_backup as restore

    monkeypatch.setattr(backend, "resolved_backend", lambda: backend.D1)
    monkeypatch.setattr(core_db, "remaining_d1_daily_writes", lambda c: restore.예산여유 + 80)
    rows = [("news", f"n{i}") for i in range(120)] + [("sentiment_scores", "s")]
    남긴 = restore._예산만큼(None, rows, None, skip_rows=40)
    out = capsys.readouterr().out
    assert len(남긴) == 80
    assert "다음 실행: --only news --skip-rows 120" in out and "그다음: --only sentiment_scores" in out
