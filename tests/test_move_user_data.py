"""D1 → Turso 로 사람이 넣은 데이터 옮기기 (docs/infra.md 25.9).

**두 DB 의 종목 번호가 다르다**(D1 은 마스터를 새로 만들었다). 번호를 그대로 옮기면 매매 기록이
엉뚱한 종목에 붙는다. 여기서는 번호가 다른 두 메모리 DB 로 그것을 재현한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import move_user_data as mover  # noqa: E402

from batch.core import db  # noqa: E402
from tests.test_portfolio_job import MemClient  # noqa: E402


def make_db(stocks: list[tuple[int, str, str]]) -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for sid, ticker, market in stocks:
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, ?, 'KR', 'KRW', 'active', 't', 't')",
            [sid, ticker, market],
        )
    return mem


TRADE = (
    "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
    " created_at, updated_at) VALUES (?, 'buy', '2026-09-20', 70000, 10, 'KRW', 1, 'none', ?, ?)"
)


@pytest.fixture
def pair() -> tuple[MemClient, MemClient]:
    # D1: 삼성전자 389, SK하이닉스 531, 사라진 종목 900 / Turso: 삼성전자 1, SK하이닉스 2
    src = make_db([(389, "005930", "KOSPI"), (531, "000660", "KOSPI"), (900, "999999", "KOSDAQ")])
    dst = make_db([(1, "005930", "KOSPI"), (2, "000660", "KOSPI")])
    return src, dst


def test_종목_번호를_종목코드로_다시_맞춘다(pair) -> None:
    src, dst = pair
    src.conn.execute(TRADE, [389, "2026-09-20T01:00:00", "2026-09-20T01:00:00"])
    mover.run(src, dst, do_apply=True)
    assert dst.conn.execute("SELECT stock_id, price FROM trades").fetchall() == [(1, 70000)]  # 389 가 아니라 1


def test_두_번_옮겨도_한_번만_들어간다(pair) -> None:
    src, dst = pair
    src.conn.execute(TRADE, [531, "2026-09-21T02:00:00", "2026-09-21T02:00:00"])
    mover.run(src, dst, do_apply=True)
    mover.run(src, dst, do_apply=True)
    assert dst.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


class Test대상에_없는_종목:
    """**사람이 쓴 종목이면 마스터째 옮긴다** (2026-09-21, docs/infra.md 25.81).

    2026-09-21 까지는 그냥 버렸다. 그런데 버린다는 사실이 **로그에만** 남고,
    `turso_return.py` 는 그것을 보지 않은 채 복귀 표시를 적고
    "✅ 매매·관심종목·설정을 옮겼습니다" 를 텔레그램으로 보냈다.
    매매 기록은 사람이 넣은 것이라 **다른 곳에 없다**(CLAUDE.md 매매 규칙).

    **언제 일어나나.** Turso 의 마스터는 막힌 날에 멈춰 있다. D1 으로 운영하는 동안
    새로 상장한 종목을 사서 적어 두면 그 종목은 Turso 에 없다. 시장을 옮긴 종목
    (코스닥→코스피)도 `(종목코드, 시장)` 짝이 달라져 같은 일을 당한다.
    """

    def test_매매가_달린_종목은_마스터째_옮긴다(self, pair) -> None:
        src, dst = pair
        src.conn.execute(TRADE, [900, "t", "t"])

        결과 = mover.run(src, dst, do_apply=True)

        # 종목이 생기고, 매매가 **그 종목에** 붙었다
        새종목 = dst.conn.execute("SELECT id FROM stocks WHERE ticker = '999999'").fetchone()
        assert 새종목 is not None, "사람이 쓴 종목을 버렸다 — 그 매매 기록은 어디에도 없다"
        assert dst.conn.execute("SELECT stock_id FROM trades").fetchall() == [(새종목[0],)]
        assert 결과.온전한가 and 결과.carried == 1

    def test_마스터를_그대로_옮긴다_지어내지_않는다(self, pair) -> None:
        src, dst = pair
        src.conn.execute("UPDATE stocks SET name_ko = '새내기', source = 'krx', fetched_at = '2026-09-25' WHERE id = 900")
        src.conn.execute(TRADE, [900, "t", "t"])

        mover.run(src, dst, do_apply=True)

        옮긴것 = dst.conn.execute(
            "SELECT ticker, market, country, name_ko, currency, source, fetched_at FROM stocks WHERE ticker = '999999'"
        ).fetchone()
        assert 옮긴것 == ("999999", "KOSDAQ", "KR", "새내기", "KRW", "krx", "2026-09-25")

    def test_번호는_대상이_새로_매긴다(self, pair) -> None:
        """원본 번호 900 을 그대로 쓰면 나중에 대상이 900번을 다른 종목에 줄 때 부딪힌다."""
        src, dst = pair
        src.conn.execute(TRADE, [900, "t", "t"])

        mover.run(src, dst, do_apply=True)

        번호 = dst.conn.execute("SELECT id FROM stocks WHERE ticker = '999999'").fetchone()[0]
        assert 번호 != 900

    def test_관심_종목만_달려_있어도_옮긴다(self, pair) -> None:
        src, dst = pair
        src.conn.execute("INSERT INTO watchlist (stock_id, added_at, target_buy_price) VALUES (900, 't', 5000)")

        결과 = mover.run(src, dst, do_apply=True)

        assert 결과.carried == 1
        assert dst.conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 1

    def test_사람이_안_쓴_종목은_마스터를_옮기지_않는다(self, pair) -> None:
        """뉴스만 달린 종목까지 옮기면 마스터가 부풀고, 그것은 사람이 넣은 것이 아니다."""
        src, dst = pair
        src.conn.execute(
            "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (900, '기사', 'https://y/9', '2026-09-20T00:00:00Z', 'ko', 'yna_rss', 't')"
        )

        결과 = mover.run(src, dst, do_apply=True)

        assert dst.conn.execute("SELECT COUNT(*) FROM stocks WHERE ticker = '999999'").fetchone()[0] == 0
        assert 결과.unmapped == ["999999·KOSDAQ"], "버렸으면 버렸다고 말해야 한다"
        assert not 결과.온전한가

    def test_두_번_옮겨도_마스터가_하나다(self, pair) -> None:
        src, dst = pair
        src.conn.execute(TRADE, [900, "t", "t"])

        mover.run(src, dst, do_apply=True)
        두번째 = mover.run(src, dst, do_apply=True)

        assert dst.conn.execute("SELECT COUNT(*) FROM stocks WHERE ticker = '999999'").fetchone()[0] == 1
        assert 두번째.carried == 0
        assert dst.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1

    def test_시험_실행은_마스터도_안_쓴다(self, pair, capsys) -> None:
        src, dst = pair
        src.conn.execute(TRADE, [900, "t", "t"])

        mover.run(src, dst, do_apply=False)

        assert dst.conn.execute("SELECT COUNT(*) FROM stocks WHERE ticker = '999999'").fetchone()[0] == 0
        assert "종목 마스터 1개를 먼저 옮기게 된다" in capsys.readouterr().out


def test_설정은_덮고_관심종목은_번호를_맞춘다(pair) -> None:
    src, dst = pair
    src.conn.execute("DELETE FROM settings")
    src.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('total_investable_amount', '50000000', 'd1')")
    src.conn.execute("INSERT INTO watchlist (stock_id, added_at, target_buy_price) VALUES (531, 't', 1500000)")
    mover.run(src, dst, do_apply=True)
    value = dst.conn.execute("SELECT value FROM settings WHERE key = 'total_investable_amount'").fetchone()[0]
    assert value == "50000000"
    assert dst.conn.execute("SELECT stock_id, target_buy_price FROM watchlist").fetchall() == [(2, 1500000)]


def test_기사와_채점을_함께_옮긴다_기사_번호도_다시_맞춘다(pair) -> None:
    src, dst = pair
    src.conn.execute(
        "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (77, 389, '삼성전자 호조', 'https://y/1', '2026-09-20T00:00:00Z', 'ko', 'yna_rss', 't')"
    )
    src.conn.execute(
        "INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (77, 0.8, 'korfinasc', 't')"
    )
    # 대상에 이미 다른 기사가 있어 번호가 어긋난다
    dst.conn.execute(
        "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (1, 2, '다른 기사', 'https://y/0', '2026-09-01T00:00:00Z', 'ko', 'yna_rss', 't')"
    )
    mover.run(src, dst, do_apply=True)
    row = dst.conn.execute(
        "SELECT n.stock_id, n.url, a.score FROM article_sentiments a JOIN news n ON n.id = a.news_id"
    ).fetchone()
    assert row == (1, "https://y/1", 0.8)


def test_시험_실행은_쓰지_않는다(pair) -> None:
    src, dst = pair
    src.conn.execute(TRADE, [389, "t", "t"])
    mover.run(src, dst, do_apply=False)
    assert dst.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


class Test운영_표시는_안_옮긴다:
    """"지금 이 DB 가 어떤 상태인지" 는 옮길 데이터가 아니다 (docs/infra.md 25.156).

    가장 나쁜 것은 복귀 표시다. `auto` 판정이 그 한 줄로 Turso 냐 D1 이냐를 고른다(25.12).
    옛 표시가 딸려 오면 앱 전체가 엉뚱한 DB 를 본다 — 하필 **복귀하는 날**에.

    25.155 가 백업에서 같은 것을 뺐다. 여기서는 **같은 목록을 가져다 쓴다** — 25.118 에서
    "사람이 넣은 것" 의 표 목록이 두 벌이라 갈라진 적이 있다.
    """

    def _두DB(self) -> tuple[MemClient, MemClient]:
        src = make_db([(1, "005930", "KOSPI")])
        dst = make_db([(7, "005930", "KOSPI")])
        return src, dst

    def test_복귀_표시는_안_옮기고_사용자_설정은_옮긴다(self) -> None:
        src, dst = self._두DB()
        src.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('db_return_done_at', '2026-09-01', 't')"
        )
        src.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('total_investable_amount', '1000', 't')"
        )

        statements, _counts, _unmapped = mover.plan(src, dst)
        for sql, args in statements:
            dst.execute(sql, args)

        남은 = {k for (k,) in dst.conn.execute("SELECT key FROM settings").fetchall()}
        assert "total_investable_amount" in 남은
        assert "db_return_done_at" not in 남은

    def test_목록을_백업과_함께_본다(self) -> None:
        """두 벌이 되면 한쪽만 고쳐질 때 갈라진다 (25.0 「한 규칙이 두 곳에 있다」)."""
        import importlib.util
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("backup_db", 뿌리 / "scripts" / "backup_db.py")
        assert spec and spec.loader
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)

        assert mover.설정_제외_사유 is backup.설정_제외_사유 or (
            mover.설정_제외_사유 == backup.설정_제외_사유
        ), "이주가 백업과 다른 목록을 들고 있다"

    def test_세는_수도_거른_뒤의_수다(self) -> None:
        """화면에 "settings 2" 라고 찍고 하나만 옮기면 사람이 셈을 못 맞춘다."""
        src, dst = self._두DB()
        src.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('db_return_requested_at', 'x', 't')"
        )
        src.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('fees', '{}', 't')")

        _statements, counts, _unmapped = mover.plan(src, dst)

        assert counts["settings"] == 1


def test_사람이_더한_별칭도_번호를_맞춰_옮긴다_25_848(pair) -> None:
    src, dst = pair
    src.conn.execute("INSERT INTO stock_aliases VALUES (531, '하이닉스반도체', 'user', 't')")
    mover.run(src, dst, do_apply=True)
    mover.run(src, dst, do_apply=True)  # 두 번 옮겨도 한 줄
    assert dst.conn.execute("SELECT stock_id, alias, added_by FROM stock_aliases").fetchall() == [
        (2, "하이닉스반도체", "user")
    ]


def test_받는_DB_에_별칭_표가_없으면_건너뛰고_매매는_옮긴다_25_848(pair, capsys) -> None:
    src, dst = pair
    dst.conn.execute("DROP TABLE stock_aliases")
    src.conn.execute("INSERT INTO stock_aliases VALUES (531, '하이닉스반도체', 'user', 't')")
    src.conn.execute(TRADE, [389, "2026-09-20T01:00:00", "2026-09-20T01:00:00"])
    mover.run(src, dst, do_apply=True)
    assert dst.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert "stock_aliases 표가 없어" in capsys.readouterr().out


def test_보내는_쪽에_별칭_표가_없어도_멈추지_않는다_25_853(pair, capsys) -> None:
    src, dst = pair
    src.conn.execute("DROP TABLE stock_aliases")
    src.conn.execute(TRADE, [389, "2026-09-20T01:00:00", "2026-09-20T01:00:00"])
    mover.run(src, dst, do_apply=True)
    assert dst.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert "stock_aliases 표가 없어" in capsys.readouterr().out


def test_받는_DB_에_없는_종목의_별칭은_못_맞춤으로_치지_않는다_25_853(pair) -> None:
    src, dst = pair
    src.conn.execute("INSERT INTO stock_aliases VALUES (900, '사라진회사', 'user', 't')")
    결과 = mover.run(src, dst, do_apply=True)
    assert 결과.온전한가


def test_사람이_별칭만_단_새_종목도_마스터째_옮긴다_25_854(pair) -> None:
    src, dst = pair
    src.conn.execute("INSERT INTO stock_aliases VALUES (900, '새회사', 'user', 't')")
    결과 = mover.run(src, dst, do_apply=True)
    assert 결과.온전한가
    assert dst.conn.execute(
        "SELECT a.alias FROM stock_aliases a JOIN stocks s ON s.id = a.stock_id WHERE s.ticker = '999999'"
    ).fetchall() == [("새회사",)]
