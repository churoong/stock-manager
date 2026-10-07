"""백업 스크립트 테스트 (docs/backup.md, Step 18).

백업은 **되살릴 수 있어야** 의미가 있다. 그래서 파일이 만들어지는지가 아니라
**덤프 → 복원 → 값 비교**까지 한 번에 확인한다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import gzip
import importlib.util
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet

ROOT = Path(__file__).resolve().parent.parent


def read_gz(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return fh.read()


def load_script():
    spec = importlib.util.spec_from_file_location("backup_db", ROOT / "scripts" / "backup_db.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load_script()


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.writes = 0

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        if not sql.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH")):
            self.writes += 1
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(backup, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    # 따옴표가 든 메모를 일부러 넣는다. 이스케이프가 깨지면 복원이 통째로 실패한다
    mem.conn.execute(
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " memo, created_at, updated_at) VALUES (1, 1, 'buy', '2026-09-01', 70000, 10, 'KRW', 1, 'none',"
        " '메모에 '' 작은따옴표와 \"큰따옴표\"', 't', 't')"
    )
    mem.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('fees', '{\"kr\": 0.015}', 't')")
    mem.writes = 0
    return mem


def test_필수_백업을_복원하면_값이_같다(client: MemClient, tmp_path: Path) -> None:
    assert backup.run("essential", tmp_path) == 0
    files = list(tmp_path.glob("stock-manager-essential-*.sql.gz"))
    assert len(files) == 1

    restored = sqlite3.connect(":memory:")
    restored.executescript(read_gz(files[0]))

    row = restored.execute("SELECT stock_id, price, quantity, memo FROM trades").fetchone()
    assert row[0] == 1 and row[1] == 70000 and row[2] == 10
    assert row[3] == "메모에 ' 작은따옴표와 \"큰따옴표\""
    assert restored.execute("SELECT value FROM settings WHERE key = 'fees'").fetchone()[0] == '{"kr": 0.015}'


def test_필수_백업은_시세처럼_다시_받을_수_있는_표를_넣지_않는다(client: MemClient, tmp_path: Path) -> None:
    backup.run("essential", tmp_path)
    text = read_gz(next(tmp_path.glob("*.sql.gz")))
    assert "CREATE TABLE trades" in text.replace("IF NOT EXISTS ", "")
    assert "INSERT INTO prices" not in text
    # 사용자 입력과 시점 스냅샷은 반드시 들어간다 (다시 만들 수 없다)
    for table in ("trades", "dividend_receipts", "settings", "financial_snapshots"):
        assert f"-- {table}" in text


def test_전체_백업은_모든_표를_담는다(client: MemClient, tmp_path: Path) -> None:
    backup.run("full", tmp_path)
    text = read_gz(next(tmp_path.glob("*.sql.gz")))
    assert "-- prices" in text
    assert "-- stocks" in text
    restored = sqlite3.connect(":memory:")
    restored.executescript(text)  # 전체도 그대로 되살아난다
    assert restored.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1


def test_백업은_DB_에_아무것도_쓰지_않는다(client: MemClient, tmp_path: Path) -> None:
    backup.run("full", tmp_path)
    assert client.writes == 0


def test_값_따옴표_처리(client: MemClient) -> None:
    assert backup.quote(None) == "NULL"
    assert backup.quote(12) == "12"
    assert backup.quote("a'b") == "'a''b'"
    assert backup.quote(b"\x01\x02") == "X'0102'"


# ----------------------------------------------------------------------
# 두 목록이 "사람이 넣은 것" 을 다르게 알고 있었다 (2026-09-22, docs/infra.md 25.118)
# ----------------------------------------------------------------------


class Test옮기는_표는_전부_백업한다:
    """`scripts/move_user_data.py` 는 Turso ↔ D1 사이에서 **사람 손이 닿은 표**를 옮긴다.
    `scripts/backup_db.py` 는 **잃으면 안 되는 표**를 담는다. 같은 뜻인데 목록이 둘이다.

    `screener_presets` 가 그쪽에만 있었다 — 옮길 만큼 중요한데 **백업에는 없었다.**
    마이그레이션 0033 은 그 표를 "사용자 입력이라 배치가 건드리지 않는다" 고 적어 두었다.
    아티팩트가 없으면 되돌릴 수 없는 종류다.
    """

    @staticmethod
    def _옮기는_표() -> set[str]:
        """`move_user_data.py` 가 **INSERT 하는** 표 이름."""
        import re
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        본문 = (뿌리 / "scripts" / "move_user_data.py").read_text(encoding="utf-8")
        return {m.group(1) for m in re.finditer(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", 본문, re.I)}

    #: 옮기지만 백업하지 않아도 되는 표와 **사유**. 사유 없는 예외는 두지 않는다
    예외 = {
        "stocks": "종목 마스터는 수집으로 다시 만든다. 옮기는 이유는 번호를 맞추기 위해서다",
    }

    def test_읽어_냈다(self) -> None:
        assert len(self._옮기는_표()) >= 5, "move_user_data 가 INSERT 하는 표를 못 읽었다"

    def test_예외에_사유가_있다(self) -> None:
        assert all(len(v) > 15 for v in self.예외.values())

    def test_옮기는_표가_필수_백업에_다_있다(self) -> None:
        from scripts.backup_db import ESSENTIAL_TABLES

        빠진것 = sorted(self._옮기는_표() - set(ESSENTIAL_TABLES) - set(self.예외))
        assert not 빠진것, (
            "다른 DB 로 옮길 만큼 사람 손이 닿은 표인데 백업에 없다.\n"
            "  아티팩트가 없으면 되돌릴 수 없다:\n  " + "\n  ".join(빠진것)
        )

    def test_문서도_같은_목록을_말한다(self) -> None:
        """계산식이 아니어도 목록은 문서가 함께 말한다 (CLAUDE.md 기록 규칙)."""
        from pathlib import Path

        문서 = (Path(__file__).resolve().parent.parent / "docs" / "backup.md").read_text(encoding="utf-8")
        assert "`screener_presets`" in 문서


# ----------------------------------------------------------------------
# 되살리기가 **진짜 되는가** (2026-09-22, docs/infra.md 25.119)
# ----------------------------------------------------------------------


class Test여러_줄_값도_되살아난다:
    """**여태 아무도 되살리기 스크립트를 지나 보지 않았다.**

    위의 `test_필수_백업을_복원하면_값이_같다` 는 `sqlite3.executescript()` 로 되살린다.
    그건 여러 줄 SQL 을 알아서 읽지만 **운영에서 쓰는 길이 아니다.**
    `scripts/restore_backup.py` 는 "`INSERT` 로 시작하고 `;` 로 끝나는 **한 줄**" 만 주웠다.

    백업은 값을 그대로 쓰고 따옴표만 겹친다(`backup_db.quote`). 값에 줄바꿈이 있으면
    `INSERT` 한 문장이 여러 줄이 되고, 그 행은 **오류 없이 사라졌다.**
    매매 메모와 무응답 알림 문구가 실제로 여러 줄이다.
    """

    @staticmethod
    def _백업(mem, tmp_path: Path) -> Path:
        assert backup.run("essential", tmp_path) == 0
        return next(tmp_path.glob("*.sql.gz"))

    def test_줄바꿈_따옴표_세미콜론이_든_값을_읽어_낸다(self, client: MemClient, tmp_path: Path) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        메모 = "첫 줄\n둘째 줄; 세미콜론\n'작은따옴표'와 -- 주석처럼 보이는 것"
        client.conn.execute(
            "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency,"
            " fx_rate, fx_rate_source, memo, created_at, updated_at)"
            " VALUES (2, 1, 'buy', '2026-09-02', 71000, 5, 'KRW', 1, 'none', ?, 't', 't')",
            [메모],
        )

        rows = restore.statements(self._백업(client, tmp_path))

        메모들 = [sql for table, sql in rows if table == "trades"]
        assert len(메모들) == 2, "여러 줄짜리 행이 사라졌다"
        assert any("둘째 줄; 세미콜론" in sql for sql in 메모들)

    def test_값_안의_줄머리_주석표시도_그대로_살린다(self, client: MemClient, tmp_path: Path) -> None:
        """줄 머리의 `--` 를 주석으로 걷어 메모가 바뀌거나 문장이 잘렸다 (docs/infra.md 25.692, 교차검증 재현)."""
        import sqlite3
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        메모들 = {3: "첫줄\n-- 손절 5%\n끝", 4: "첫줄\n-- 손절 5%"}
        for tid, 메모 in 메모들.items():
            client.conn.execute(
                "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency,"
                " fx_rate, fx_rate_source, memo, created_at, updated_at)"
                " VALUES (?, 1, 'buy', '2026-09-02', 71000, 5, 'KRW', 1, 'none', ?, 't', 't')",
                [tid, 메모],
            )
        rows = restore.statements(self._백업(client, tmp_path))
        mem = sqlite3.connect(":memory:")
        열 = [r[1] for r in client.conn.execute("PRAGMA table_info(trades)")]
        mem.execute(f"CREATE TABLE trades ({', '.join(열)})")
        for table, sql in rows:
            if table == "trades":
                mem.execute(sql)
        되살림 = dict(mem.execute("SELECT id, memo FROM trades WHERE id IN (3, 4)").fetchall())
        assert 되살림 == 메모들

    def test_값_안의_표식_줄은_대응표와_행수를_덮지_않는다(self, client: MemClient, tmp_path: Path) -> None:
        """메모 속 "-- stock_key 1 AAPL…" 이 머리의 진짜 줄을 덮어썼다 (docs/infra.md 25.695, 교차검증 재현)."""
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        client.conn.execute(
            "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency,"
            " fx_rate, fx_rate_source, memo, created_at, updated_at)"
            " VALUES (5, 1, 'buy', '2026-09-02', 71000, 5, 'KRW', 1, 'none', ?, 't', 't')",
            ["첫줄\n-- stock_key 1\tAAPL\tNASDAQ\tUS\tUSD\n-- rows trades 7"],
        )
        backup.run("full", tmp_path)
        길 = next(tmp_path.glob("*full*.sql.gz"))
        assert restore.종목_대응표(길)[1][:2] == ("005930", "KOSPI")
        assert restore.행수_대조(길, restore.statements(길)) == []

    def test_따옴표_셈이_어긋나면_넣지_않는다(self, tmp_path: Path) -> None:
        """셈이 어긋나면 뒤의 행과 행 수 표식이 함께 사라져 대조가 통과했다 (docs/infra.md 25.697, 교차검증 재현)."""
        import gzip
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        글 = (
            "-- rows a 1\nINSERT INTO a (x) VALUES (1);\n"
            "CREATE TABLE b (x /* b's note */);\n-- rows b 1\nINSERT INTO b (x) VALUES (2);\n"
        )
        길 = tmp_path / "odd.sql.gz"
        with gzip.open(길, "wt", encoding="utf-8") as h:
            h.write(글)
        with pytest.raises(ValueError, match="따옴표가 닫히지 않은"):
            restore.statements(길)
        assert restore.run(길, None, None, dry_run=True) == 1

    def test_한_줄이라도_빠지면_넣지_않는다(self, client: MemClient, tmp_path: Path) -> None:
        """**반쪽만 되살리면 더 나쁘다.** 백업이 적어 둔 행 수와 대 본다."""
        import gzip
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        길 = self._백업(client, tmp_path)
        with gzip.open(길, "rt", encoding="utf-8") as handle:
            글 = handle.read()
        # 행 하나를 지워 "읽어 낸 수가 모자란" 상태를 만든다
        깨진것 = tmp_path / "broken.sql.gz"
        with gzip.open(깨진것, "wt", encoding="utf-8") as out:
            out.write("\n".join(줄 for 줄 in 글.splitlines() if not 줄.startswith("INSERT INTO settings")))

        어긋남 = restore.행수_대조(깨진것, restore.statements(깨진것))

        assert any("settings" in 줄 for 줄 in 어긋남)
        assert restore.run(깨진것, None, None, dry_run=True) == 1, "모자란 채로 넣으려 했다"

    def test_온전하면_대조를_통과한다(self, client: MemClient, tmp_path: Path) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        길 = self._백업(client, tmp_path)
        assert restore.행수_대조(길, restore.statements(길)) == []
        assert restore.run(길, None, None, dry_run=True) == 0

    def test_행수_표시가_없는_옛_백업은_막지_않는다(self, tmp_path: Path) -> None:
        """**없는 것을 틀렸다고 하지 않는다.** 옛 아티팩트도 되살릴 수 있어야 한다."""
        import gzip
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import restore_backup as restore

        옛것 = tmp_path / "old.sql.gz"
        with gzip.open(옛것, "wt", encoding="utf-8") as out:
            out.write("INSERT INTO settings (key, value) VALUES ('a', 'b');\n")

        assert restore.행수_대조(옛것, restore.statements(옛것)) == []
        assert restore.run(옛것, None, None, dry_run=True) == 0


class Test예산과_이어_하기:
    """`--max-rows` 로 나눠 넣고 다음 날 이어서 하는 길 (2026-09-22, docs/infra.md 25.120)."""

    @staticmethod
    def _자르기():
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts.restore_backup import 표_경계로_자르기

        return 표_경계로_자르기

    행들 = [("a", "1")] * 3 + [("b", "2")] * 5 + [("c", "3")] * 2

    def test_한도_안이면_그대로_다_넣는다(self) -> None:
        넣을것, 남은표, 쪼갠표 = self._자르기()(self.행들, 100)
        assert len(넣을것) == 10 and 남은표 == [] and 쪼갠표 is None

    def test_표_가운데서_끊지_않는다(self) -> None:
        """예전에는 `rows[:max_rows]` 였다. 표가 반쯤 들어가면 그 상태를 `--only` 로 말할 수 없다."""
        넣을것, 남은표, 쪼갠표 = self._자르기()(self.행들, 6)

        assert [t for t, _ in 넣을것] == ["a"] * 3, "한도 6 인데 b 를 반쯤 넣었다"
        assert 남은표 == ["b", "c"], "다음에 무엇을 넣을지 알려 주지 않는다"
        assert 쪼갠표 is None

    def test_남은_표_순서를_지킨다(self) -> None:
        """외래키 순서가 파일 순서다. `--only` 도 그 순서로 알려 줘야 한다."""
        _, 남은표, _ = self._자르기()(self.행들, 9)
        assert 남은표 == ["c"]

    def test_표_하나가_한도보다_크면_자르되_말한다(self) -> None:
        """**말없이 반쪽을 남기지 않는다.**"""
        넣을것, 남은표, 쪼갠표 = self._자르기()(self.행들, 2)

        assert len(넣을것) == 2
        assert 쪼갠표 == "a", "가운데서 잘랐다는 사실을 안 알린다"
        assert 남은표[0] == "a", "잘린 표를 다음에 다시 보내야 한다"


class Test외래키_순서:
    """**D1 은 외래키를 실제로 강제한다** (Turso 에서는 안 걸렸다 — restore_backup 설명문).

    그래서 되살리기는 참조되는 표를 **먼저** 넣어야 하는데, 2026-09-22 까지
    그 순서를 지키는 장치가 없었다 (docs/infra.md 25.121).

    * `ESSENTIAL_TABLES` 는 **우연히** 맞았다 — `news` 가 `article_sentiments` 앞이었다.
      오늘 그 목록에 한 줄을 끼워 넣었으니(25.118), 순서가 흐트러질 수 있다는 뜻이다
    * `full` 백업은 `sorted(available)` — **이름 순**이라 거의 반드시 어긋난다
      (`alerts` 가 `stocks` 보다, `article_sentiments` 가 `news` 보다 먼저 온다)
    """

    def test_읽어_냈다(self) -> None:
        의존 = backup.외래키_의존()
        assert 의존.get("article_sentiments") == {"news"}, "마이그레이션에서 외래키를 못 읽었다"
        assert len(의존) > 20

    def test_필수_목록이_외래키_순서를_지킨다(self) -> None:
        """목록에 표를 끼워 넣을 때 여기가 물어 준다."""
        assert backup.순서_어긋남(backup.ESSENTIAL_TABLES) == []

    def test_어긋나면_말한다(self) -> None:
        """검사가 늘 빈 목록만 돌려주면 그물이 아니다."""
        assert backup.순서_어긋남(["article_sentiments", "news"]) == [
            "article_sentiments 가 news 보다 먼저 온다"
        ]

    def test_의존_순서로_세우면_어긋남이_없다(self) -> None:
        뒤섞인것 = sorted(backup.ESSENTIAL_TABLES + ["article_sentiments"])
        assert backup.순서_어긋남(backup.의존_순서(뒤섞인것)) == []

    def test_같은_층은_넘겨받은_순서를_지킨다(self) -> None:
        """뜻이 있는 순서를 흩지 않는다 — `settings` 를 `trades` 앞으로 옮길 이유가 없다."""
        assert backup.의존_순서(["settings", "trades", "watchlist"]) == ["settings", "trades", "watchlist"]

    def test_전체_백업도_의존_순서로_쓴다(self, client: MemClient, tmp_path: Path) -> None:
        backup.run("full", tmp_path)
        text = read_gz(next(tmp_path.glob("*.sql.gz")))
        순서 = [줄[3:].strip() for 줄 in text.splitlines() if 줄.startswith("-- ") and " " not in 줄[3:].strip()]
        표들 = [t for t in 순서 if t in backup.외래키_의존()]

        assert 표들, "표 머리말을 못 읽었다"
        assert backup.순서_어긋남(표들) == [], "이름 순으로 써서 되살릴 때 외래키에 걸린다"


class Test운영_표시는_백업에_담지_않는다:
    """`settings` 에서 **줄 단위로** 빼는 것들 (docs/infra.md 25.155).

    `settings` 는 대부분 사용자 값이지만 몇 줄은 **지금 이 DB 의 상태**를 적는 운영 표시다.
    되살리면 옛 상태가 새 DB 에 심긴다 — `api_usage` 를 통째로 뺀 것과 같은 이유다.
    가장 나쁜 것은 복귀 표시다. `auto` 판정이 그 한 줄로 Turso/D1 을 고른다(25.12).
    """

    def _백업(self, mem: MemClient) -> str:
        import io

        buf = io.StringIO()
        backup.dump_table(mem, "settings", buf)
        return buf.getvalue()

    def test_복귀_표시가_안_담긴다(self, client: MemClient) -> None:
        mem = client
        mem.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('db_return_done_at', '2026-09-01', 't')"
        )
        mem.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('total_investable_amount', '1000', 't')"
        )

        글 = self._백업(mem)

        assert "total_investable_amount" in 글, "사용자 값까지 빼면 안 된다"
        assert "db_return_done_at" not in 글

    def test_빼는_열쇠가_코드의_이름과_같다(self) -> None:
        """**이름이 바뀌면 조용히 다시 담긴다** (25.0 「테스트가 자료를 손으로 베낀다」)."""
        from pathlib import Path

        from batch.core.client import RETURN_MARKER

        assert RETURN_MARKER in backup.설정_제외_사유

        웹 = Path(__file__).resolve().parent.parent / "web" / "lib"
        요청키 = (웹 / "tursoWatch.ts").read_text(encoding="utf-8")
        잠금키 = (웹 / "loginGuard.ts").read_text(encoding="utf-8")
        for 이름, 글 in (("db_return_requested_at", 요청키), ("login_global_lock_alerted_at", 잠금키)):
            assert f'"{이름}"' in 글, f"{이름} 이 코드에서 사라졌다 — 목록이 낡았다"
            assert 이름 in backup.설정_제외_사유

    def test_사유가_비어_있지_않다(self) -> None:
        짧은것 = [k for k, v in backup.설정_제외_사유.items() if len(v.strip()) < 20]
        assert not 짧은것, f"사유가 너무 짧다: {짧은것}"

    def test_거르기가_settings_밖으로_새지_않는다(self, client: MemClient) -> None:
        """다른 표까지 걸러 내면 사용자 데이터가 조용히 빠진다."""
        import io

        buf = io.StringIO()
        n = backup.dump_table(client, "trades", buf)

        assert n == 1 and "INSERT INTO trades" in buf.getvalue()


class Test백업이_자취를_남긴다:
    """**백업만 자취가 없었다** (docs/infra.md 25.167).

    다른 예약 작업은 전부 저장하는 표가 있어 `/status` 의 데이터 신선도로 보인다.
    백업은 산출물이 GitHub 아티팩트라 **DB 에 아무것도 안 남겼다** — 주 1회가 몇 주째
    멈춰도 알 길이 없었다. 그리고 이것은 **잃으면 끝인 자료를 지키는 유일한 장치**다
    (docs/backup.md 0장).

    덤프 자체는 그대로 읽기 전용이다. 남기는 것은 `batch_runs` 한 행뿐이다.
    """

    def test_덤프는_여전히_아무것도_쓰지_않는다(self, client: MemClient, tmp_path: Path) -> None:
        backup.run("full", tmp_path)
        assert client.writes == 0, "`run()` 은 읽기 전용이다. 기록은 감싸는 쪽이 한다"

    def test_성공은_덤프가_아니라_업로드_뒤에_적는다(self, monkeypatch, tmp_path: Path) -> None:
        """**덤프가 끝난 것과 내려받을 파일이 생긴 것은 다른 일이다** (25.167 덧).

        먼저 닫으면 아티팩트 업로드가 실패한 날에도 기록이 `success` 로 남는다 —
        파일이 없는데 신선도 칸은 초록이다. 백업에서 가장 나쁜 거짓말이다.
        """
        순서: list[str] = []

        monkeypatch.setattr(backup.db, "start_batch_run", lambda *a, **k: 순서.append("start") or 7)
        monkeypatch.setattr(
            backup.db,
            "finish_batch_run",
            lambda *a, **k: 순서.append(f"finish:{k.get('status')}"),
        )
        monkeypatch.setattr(backup, "TursoClient", lambda: _가짜클라이언트())
        monkeypatch.setattr(backup, "run", lambda scope, out: 순서.append("dump") or 0)

        assert backup.run_recorded("essential", tmp_path) == 0
        assert 순서 == ["start", "dump"], "덤프만으로 닫으면 안 된다 — 워크플로가 업로드 뒤에 닫는다"

        backup.마무리(7, "success", None, {"artifact": True})
        assert 순서[-1] == "finish:success"

    def test_실행_번호를_다음_스텝에_넘긴다(self, monkeypatch, tmp_path: Path) -> None:
        """넘기지 못하면 닫을 수 없고, 기록이 영영 `running` 으로 남는다."""
        나온것 = tmp_path / "out.txt"
        monkeypatch.setenv("GITHUB_OUTPUT", str(나온것))
        monkeypatch.setattr(backup.db, "start_batch_run", lambda *a, **k: 42)
        monkeypatch.setattr(backup.db, "finish_batch_run", lambda *a, **k: None)
        monkeypatch.setattr(backup, "TursoClient", lambda: _가짜클라이언트())
        monkeypatch.setattr(backup, "run", lambda scope, out: 0)

        backup.run_recorded("essential", tmp_path)
        assert "run_id=42" in 나온것.read_text(encoding="utf-8")

    def test_덤프가_터지면_실패로_적고_다시_던진다(self, monkeypatch, tmp_path: Path) -> None:
        본것: list[str] = []
        monkeypatch.setattr(backup.db, "start_batch_run", lambda *a, **k: 7)
        monkeypatch.setattr(
            backup.db, "finish_batch_run", lambda *a, **k: 본것.append(str(k.get("status")))
        )
        monkeypatch.setattr(backup, "TursoClient", lambda: _가짜클라이언트())

        def 터짐(scope, out):
            raise RuntimeError("디스크가 없다")

        monkeypatch.setattr(backup, "run", 터짐)
        with pytest.raises(RuntimeError, match="디스크"):
            backup.run_recorded("essential", tmp_path)
        assert 본것 == ["failed"], "조용히 넘어가면 실패한 백업이 기록에도 안 남는다"

    def test_워크플로가_업로드_뒤에_닫는다(self) -> None:
        글 = (ROOT / ".github" / "workflows" / "backup.yml").read_text(encoding="utf-8")

        assert "--record-run" in 글, "깃발을 만들어 두고 안 쓰면 자취는 그대로 없다"
        assert "--finish-run" in 글, "열기만 하면 기록이 영영 `running` 이다"
        assert 글.index("upload-artifact") < 글.index("--finish-run"), (
            "업로드보다 먼저 닫으면 파일이 없는 날에도 성공으로 남는다"
        )

    def test_화면이_같은_이름으로_찾는다(self) -> None:
        """**두 곳이 같은 글자를 봐야 한다.** 이름이 갈라지면 칸이 영영 '없음' 이다."""
        글 = (ROOT / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
        assert f"job_name = '{backup.JOB_NAME}'" in 글
        assert '{ key: "backup"' in 글


class _가짜클라이언트:
    def close(self) -> None:
        pass


class Test종목_번호_대조:
    """필수 백업에는 종목 마스터가 없다 — 번호가 다른 DB 에 되살리면 매매가 다른 종목에 붙었다 (docs/infra.md 25.676, 감사)."""

    def _대상(self, monkeypatch: pytest.MonkeyPatch, ticker: str) -> MemClient:
        from scripts import restore_backup as restore

        대상 = MemClient()
        db.apply_migrations(대상)  # type: ignore[arg-type]
        대상.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, ?, 'KOSPI', 'KR', 'x', 'KRW', 'active', 't', 't')",
            [ticker],
        )
        monkeypatch.setattr(restore, "TursoClient", lambda: 대상)
        return 대상

    def test_백업에_대응표가_실린다(self, client: MemClient, tmp_path: Path) -> None:
        backup.run("essential", tmp_path)
        글 = read_gz(next(tmp_path.glob("*.sql.gz")))
        assert f"{backup.STOCK_KEY_MARK} 1\t005930\tKOSPI" in 글

    def test_같은_번호가_다른_종목이면_넣지_않는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import restore_backup as restore

        backup.run("essential", tmp_path)
        대상 = self._대상(monkeypatch, "000660")  # 1번이 SK하이닉스인 DB
        assert restore.run(next(tmp_path.glob("*.sql.gz")), {"trades"}, None, dry_run=False) == 1
        assert 대상.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0

    def test_같은_종목이면_넣는다(self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts import restore_backup as restore

        backup.run("essential", tmp_path)
        대상 = self._대상(monkeypatch, "005930")
        assert restore.run(next(tmp_path.glob("*.sql.gz")), {"trades"}, None, dry_run=False) == 0
        assert 대상.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

    def test_빈_새_DB_에는_종목_자리를_만들고_넣는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """25.3 절차(빈 DB 에 되살린 뒤 마스터 수집)를 25.676 이 막았다 (docs/infra.md 25.677, 교차검증)."""
        from scripts import restore_backup as restore

        backup.run("essential", tmp_path)
        대상 = MemClient()
        db.apply_migrations(대상)  # type: ignore[arg-type]
        monkeypatch.setattr(restore, "TursoClient", lambda: 대상)
        assert restore.run(next(tmp_path.glob("*.sql.gz")), {"trades"}, None, dry_run=False) == 0
        assert 대상.conn.execute("SELECT id, ticker, market, country FROM stocks").fetchall() == [
            (1, "005930", "KOSPI", "KR")
        ]
        assert 대상.conn.execute("SELECT stock_id FROM trades").fetchall() == [(1,)]

    def test_같은_종목이_다른_번호면_넣지_않는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import restore_backup as restore

        backup.run("essential", tmp_path)
        대상 = MemClient()
        db.apply_migrations(대상)  # type: ignore[arg-type]
        대상.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (389, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        monkeypatch.setattr(restore, "TursoClient", lambda: 대상)
        assert restore.run(next(tmp_path.glob("*.sql.gz")), {"trades"}, None, dry_run=False) == 1

    def test_사람_입력이_아닌_종목_표도_대조한다(self, tmp_path: Path) -> None:
        """`--only news` 가 대조 없이 들어갔다 (25.677, 교차검증)."""
        from scripts import restore_backup as restore

        rows = [("news", "INSERT INTO news (id, stock_id, title) VALUES (1, 1, 'x');"),
                ("settings", "INSERT INTO settings (key, value) VALUES ('a', 'b');")]  # fmt: skip
        assert restore.종목번호를_가진_표(rows) == ["news"]

    def test_대응표가_있으면_trust_로도_어긋남을_넘기지_않는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """trust 가 확인된 어긋남까지 넘겼다 (docs/infra.md 25.679, 교차검증)."""
        from scripts import restore_backup as restore

        backup.run("essential", tmp_path)
        self._대상(monkeypatch, "000660")
        assert restore.run(next(tmp_path.glob("*.sql.gz")), {"trades"}, None, dry_run=False, trust_stock_ids=True) == 1

    def test_시장만_옮긴_같은_종목은_어긋남이_아니다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import restore_backup as restore

        backup.run("essential", tmp_path)
        대상 = self._대상(monkeypatch, "005930")
        대상.conn.execute("UPDATE stocks SET market = 'KOSDAQ' WHERE id = 1")  # 거래소 이전 — 번호는 같다
        assert restore.run(next(tmp_path.glob("*.sql.gz")), {"trades"}, None, dry_run=False) == 0

    def test_전체_백업을_only_로_나눠도_파일의_종목_행을_먼저_넣는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """이름 없는 자리가 먼저 생겨 백업의 실제 종목 행이 버려졌다 (25.679, 교차검증)."""
        from scripts import restore_backup as restore

        client.conn.execute("UPDATE stocks SET name_ko = '삼성전자', sector = '반도체' WHERE id = 1")
        backup.run("full", tmp_path)
        대상 = MemClient()
        db.apply_migrations(대상)  # type: ignore[arg-type]
        monkeypatch.setattr(restore, "TursoClient", lambda: 대상)
        assert restore.run(next(tmp_path.glob("*full*.sql.gz")), {"trades"}, None, dry_run=False) == 0
        assert 대상.conn.execute("SELECT name_ko, sector FROM stocks WHERE id = 1").fetchone() == ("삼성전자", "반도체")

    def test_전체_백업이라도_마스터가_있는_DB_에서_번호가_다르면_막는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """25.679 가 종목 행을 붙여 대조를 껐다 — 종목 행은 무시되고 매매가 남의 종목에 붙었다 (docs/infra.md 25.681)."""
        from scripts import restore_backup as restore

        backup.run("full", tmp_path)
        대상 = self._대상(monkeypatch, "000660")
        for only in ({"trades"}, None):
            assert restore.run(next(tmp_path.glob("*full*.sql.gz")), only, None, dry_run=False) == 1
        assert 대상.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0

    def test_only_로_나눠도_max_rows_가_종목_행에_먹히지_않는다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts import restore_backup as restore

        backup.run("full", tmp_path)
        대상 = MemClient()
        db.apply_migrations(대상)  # type: ignore[arg-type]
        monkeypatch.setattr(restore, "TursoClient", lambda: 대상)
        assert restore.run(next(tmp_path.glob("*full*.sql.gz")), {"trades"}, 1, dry_run=False) == 0
        assert 대상.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

    def _옛_전체(self, tmp_path: Path) -> Path:
        backup.run("full", tmp_path)
        원본 = next(tmp_path.glob("*full*.sql.gz"))
        with gzip.open(원본, "rt", encoding="utf-8") as h:
            글 = "".join(줄 for 줄 in h if not 줄.startswith("-- stock_key"))
        옛 = tmp_path / "old_full.sql.gz"
        with gzip.open(옛, "wt", encoding="utf-8") as h:
            h.write(글)
        return 옛

    def test_대응표_없는_옛_전체_백업은_파일의_종목_행으로_대조한다(
        self, client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """25.681 뒤 빈 DB 에도 막혔고, trust 로 넣으면 남의 종목에 붙었다 (docs/infra.md 25.683)."""
        from scripts import restore_backup as restore

        옛 = self._옛_전체(tmp_path)
        빈 = MemClient()
        db.apply_migrations(빈)  # type: ignore[arg-type]
        monkeypatch.setattr(restore, "TursoClient", lambda: 빈)
        assert restore.run(옛, None, None, dry_run=False) == 0
        assert 빈.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

        대상 = self._대상(monkeypatch, "000660")
        assert restore.run(옛, None, None, dry_run=False, trust_stock_ids=True) == 1
        assert 대상.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_잘린_종목_문장이_있으면_대조할_수_없어_멈춘다() -> None:
    """25.686 은 잘린 행만 빼 그 번호를 가리키는 표가 대조 없이 남의 종목에 붙었다 (docs/infra.md 25.689, 교차검증)."""
    from scripts import restore_backup as restore

    잘린 = ("stocks", "INSERT INTO stocks (id, ticker, market) VALUES (9, '000020', '이름")
    온전 = ("stocks", "INSERT INTO stocks (id, ticker, market) VALUES (7, '005930', 'KOSPI')")
    assert restore.파일_대응표([온전]) == {7: ("005930", "KOSPI", "KR", "KRW")}
    with pytest.raises(ValueError, match="종목 행을 읽지 못했습니다"):
        restore.파일_대응표([잘린, 온전])


# ----------------------------------------------------------------------
# 페이지 넘김이 OFFSET 이 아니라 rowid 이어 읽기다 (docs/infra.md 25.852)
# ----------------------------------------------------------------------


def test_여러_쪽에_걸친_표도_빠짐없이_한_번씩_담고_OFFSET_을_쓰지_않는다(
    client: MemClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "PAGE", 3)
    for i in range(2, 12):  # 10행 + 앞의 1행 = 11행, 3행씩 네 쪽. 중간을 지워 rowid 에 구멍을 낸다
        client.conn.execute(
            "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
            " created_at, updated_at) VALUES (?, 1, 'buy', '2026-09-01', ?, 1, 'KRW', 1, 'none', 't', 't')",
            [i, 1000 + i],
        )
    client.conn.execute("DELETE FROM trades WHERE id IN (4, 5)")
    for k in range(7):  # 설정도 여러 쪽 — 운영 표시 거르기와 이어 읽기가 함께 맞아야 한다
        client.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, '1', 't')", [f"k{k}"])
    질의: list[str] = []
    원래 = client.execute
    monkeypatch.setattr(client, "execute", lambda sql, args=None: (질의.append(sql), 원래(sql, args))[1])

    assert backup.run("essential", tmp_path) == 0
    text = read_gz(next(tmp_path.glob("*.sql.gz")))
    assert not [q for q in 질의 if "OFFSET" in q.upper()]
    assert backup.ROWID_COL not in text  # 이어 읽기용 열은 백업에 남지 않는다
    restored = sqlite3.connect(":memory:")
    restored.executescript(text)
    ids = [r[0] for r in restored.execute("SELECT id FROM trades ORDER BY id")]
    assert ids == [1, 2, 3, 6, 7, 8, 9, 10, 11]
    assert restored.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'k%'").fetchone()[0] == 7


def test_모든_표가_rowid_표다() -> None:
    """이어 읽기는 rowid 에 기댄다. WITHOUT ROWID 표가 생기면 백업이 그 표에서 깨진다 (25.852)."""
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        assert "WITHOUT ROWID" not in f.read_text(encoding="utf-8").upper(), f.name


def test_D1_내부_표는_전체_백업에서_뺀다_25_854(client: MemClient, tmp_path: Path) -> None:
    """D1 의 `_cf_KV` 는 접근이 막힌 WITHOUT ROWID 표다 — 담으려다 전체 백업이 멈췄다."""
    client.conn.execute("CREATE TABLE _cf_KV (key TEXT PRIMARY KEY, value BLOB) WITHOUT ROWID")
    assert "_cf_KV" not in backup.list_tables(client)
    assert backup.run("full", tmp_path) == 0
    assert "_cf_KV" not in read_gz(next(tmp_path.glob("*.sql.gz")))


class Test8일_예비는_그달_전체_백업이_있으면_건너뛴다:
    """매월 8일 전체 백업은 1일 것의 예비다 — 성공한 달에도 시세까지 다시 읽었다(약 780만 행, docs/infra.md 25.887)."""

    def _client(self):
        from tests.test_scores_job import _sqlite_client

        return _sqlite_client()

    def _넣기(self, c, started: str, status: str, scope: str) -> None:
        c.execute(
            "INSERT INTO batch_runs (job_name, started_at, status, step_log) VALUES ('backup', ?, ?, ?)",
            [started, status, f'{{"scope": "{scope}", "artifact": true}}'],
        )

    def test_1일_전체가_성공했으면_있다(self) -> None:
        from datetime import UTC, datetime

        c = self._client()
        self._넣기(c, "2026-11-01T16:50:00+00:00", "success", "full")
        assert load_script().full_done_this_month(c, datetime(2026, 11, 8, 16, 47, tzinfo=UTC)) is True

    def test_필수만_있거나_실패했거나_지난달이면_없다(self) -> None:
        from datetime import UTC, datetime

        c = self._client()
        self._넣기(c, "2026-11-02T16:13:00+00:00", "success", "essential")
        self._넣기(c, "2026-11-01T16:50:00+00:00", "failed", "full")
        self._넣기(c, "2026-10-01T16:50:00+00:00", "success", "full")
        assert load_script().full_done_this_month(c, datetime(2026, 11, 8, 16, 47, tzinfo=UTC)) is False

    def test_워크플로가_8일_예비에서만_묻는다(self) -> None:
        text = (ROOT / ".github" / "workflows" / "backup.yml").read_text(encoding="utf-8")
        assert '"47 16 8 * *" ] && python scripts/backup_db.py --full-done-this-month' in text
        assert "--rows-read" in text
