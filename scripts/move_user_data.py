"""사람이 넣은 데이터를 한 DB 에서 다른 DB 로 옮긴다 (docs/infra.md 25.9).

Turso 가 막힌 동안 D1 으로 운영했다(25절). 되돌릴 때 **D1 에서 입력한 매매·배당·관심종목·설정을 잃으면 안 된다.**
시세·점수·신호는 다시 받거나 다시 계산하면 되지만, 이것들은 사람이 넣은 것이라 다른 곳에 없다.

**종목 번호가 두 DB 에서 다르다.** D1 은 종목 마스터를 새로 만들어 삼성전자가 389번이고 Turso 에서는 1번이다.
번호를 그대로 옮기면 매매 기록이 엉뚱한 종목에 붙는다. 그래서 **(종목코드, 시장)으로 번호를 다시 맞춘다.**
맞출 종목이 없는 행은 옮기지 않고 목록으로 알린다 — 지어내지 않는다.

같은 기록을 두 번 넣지 않는다.
  settings            키가 같으면 덮는다 (D1 에서 바꾼 설정이 최신이다).
                      **운영 표시는 안 옮긴다** — 복귀 표시 등은 그 DB 의 사정이지
                      옮길 데이터가 아니다 (2026-09-23, docs/infra.md 25.156).
                      목록은 `backup_db.설정_제외_사유` 하나다
  watchlist           종목이 같으면 덮는다
  screener_presets    (나라, 이름)이 같으면 덮는다
  stock_aliases       (종목, 별칭)이 같으면 건너뛴다. 받는 DB 에 표가 없으면 건너뛰고 알린다 (25.848)
  trades · dividend_receipts   종목·날짜·값·**입력 시각**이 모두 같으면 이미 있는 것으로 본다
  news                (종목, 주소)가 같으면 건너뛴다
  article_sentiments  옮긴 기사 번호로 다시 맞춘다. (기사, 방식)이 같으면 건너뛴다

기본은 **시험 실행**이다. `--apply` 를 붙여야 쓴다.

실행 (Actions "사용자 데이터 옮기기" 가 부른다)
  python scripts/move_user_data.py --from d1 --to turso
  python scripts/move_user_data.py --from d1 --to turso --apply
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# **백업과 같은 목록을 본다** (docs/infra.md 25.156). 25.118 에서 "사람이 넣은 것" 의
# 표 목록이 백업·이주 두 벌이라 갈라진 적이 있다. 설정 열쇠도 같은 일이 될 뻔했다
try:  # 워크플로는 `python scripts/move_user_data.py` 로 부른다
    from backup_db import 설정_제외_사유  # type: ignore[import-not-found]
except ImportError:  # 저장소 뿌리에서 패키지로 부를 때
    from scripts.backup_db import 설정_제외_사유

TRADE_COLS = (
    "side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, fee, tax, horizon, memo,"
    " snapshot_as_of, score_at_trade, signal_type_at_trade, sentiment_at_trade, factor_scores_at_trade,"
    " created_at, updated_at"
)
DIVIDEND_COLS = (
    "pay_date, amount_per_share, quantity, gross_amount, tax, net_amount, currency, fx_rate, fx_rate_source,"
    " memo, created_at, updated_at"
)
NEWS_COLS = "title, url, published_at, publisher, lang, source, fetched_at"
SENTIMENT_COLS = "score, method, method_version, matched_terms, created_at"
CHUNK = 80  # D1 파라미터 한도(100) 안쪽


def open_client(name: str):
    """백엔드 이름으로 클라이언트를 **직접** 만든다. DB_BACKEND 설정을 보지 않는다 — 두 곳을 동시에 연다."""
    if name == "d1":
        from batch.core.d1 import D1Client

        return D1Client()
    from batch.core.turso import TursoClient

    return TursoClient()


def cols(spec: str) -> list[str]:
    return [c.strip() for c in spec.split(",") if c.strip()]


def stock_keys(client) -> dict[int, tuple[str, str]]:
    """종목 번호 → (종목코드, 시장)."""
    return {int(r[0]): (str(r[1]), str(r[2])) for r in client.execute("SELECT id, ticker, market FROM stocks").rows}


def remap(src_keys: dict[int, tuple[str, str]], dst_keys: dict[int, tuple[str, str]]) -> dict[int, int]:
    """원본 번호 → 대상 번호. (종목코드, 시장)이 같은 것끼리 잇는다."""
    by_key = {key: sid for sid, key in dst_keys.items()}
    return {src_id: by_key[key] for src_id, key in src_keys.items() if key in by_key}


def 사람이_쓴_종목(src) -> set[int]:
    """매매·배당·관심종목에 걸린 종목 번호.

    **표마다 따로 묻는다.** D1 이 `UNION ALL` 항 수를 좁게 제한해
    "too many terms in compound SELECT" 로 거절한다 (docs/infra.md 25.5).
    """
    나온것: set[int] = set()
    나온것 |= {int(r[0]) for r in src.execute("SELECT DISTINCT stock_id FROM trades").rows}
    나온것 |= {int(r[0]) for r in src.execute("SELECT DISTINCT stock_id FROM dividend_receipts").rows}
    나온것 |= {int(r[0]) for r in src.execute("SELECT DISTINCT stock_id FROM watchlist").rows}
    # 사람이 단 별칭의 종목도 — 매매·관심에 없는 새 종목의 별칭이 마스터가 없어 조용히 버려졌다 (25.854, 교차검증)
    있나 = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'stock_aliases'"
    if src.execute(있나).rows:
        사람 = "SELECT DISTINCT stock_id FROM stock_aliases WHERE added_by = 'user'"
        나온것 |= {int(r[0]) for r in src.execute(사람).rows}
    return 나온것


def 마스터_옮길것(src, dst) -> list[tuple[str, list[Any]]]:
    """**대상에 없는데 사람이 쓴 종목**의 마스터 행 (2026-09-21, docs/infra.md 25.81).

    왜 있나: 번호를 맞추는 기준이 `(종목코드, 시장)` 인데, 그 짝이 대상에 아예 없으면
    매매 기록이 **조용히 버려진다.** D1 으로 운영하는 동안 새로 상장한 종목을 사서 적어 두면
    Turso 의 마스터에는 그 종목이 없다 — 마스터는 Turso 가 막힌 날에 멈춰 있기 때문이다.
    시장을 옮긴 종목(코스닥→코스피)도 짝이 달라져 같은 일을 당한다.

    **지어내는 것이 아니다.** 같은 행을 같은 `source`·`fetched_at` 그대로 옮긴다.
    번호(`id`)만 대상이 새로 매긴다.

    두 질의는 열을 **같은 순서로** 적는다. 열을 상수로 빼 f-string 으로 만들면 짧아지지만
    `tests/test_sql_schema.py` 의 그물이 못 읽는다(25.61). 옮긴 값이 원본과 같은지는
    `test_마스터를_그대로_옮긴다_지어내지_않는다` 가 본다.
    """
    쓴것 = 사람이_쓴_종목(src)
    if not 쓴것:
        return []
    있는짝 = set(stock_keys(dst).values())
    나온것 = []
    for row in src.execute(
        "SELECT id, ticker, market, country, name_ko, name_en, sector, currency, yahoo_symbol,"
        " status, source, fetched_at FROM stocks"
    ).rows:
        if int(row[0]) not in 쓴것 or (str(row[1]), str(row[2])) in 있는짝:
            continue
        나온것.append((
            "INSERT INTO stocks (ticker, market, country, name_ko, name_en, sector, currency, yahoo_symbol,"
            " status, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (ticker, market) DO NOTHING",
            list(row[1:]),
        ))  # fmt: skip
    return 나온것


def plan(src, dst) -> tuple[list[tuple[str, list[Any]]], dict[str, int], list[str]]:
    """옮길 문장, 표마다 옮길 행 수, 번호를 못 맞춘 종목. 쓰지 않는다."""
    ids = remap(stock_keys(src), stock_keys(dst))
    src_keys = stock_keys(src)
    statements: list[tuple[str, list[Any]]] = []
    counts: dict[str, int] = {}
    unmapped: set[str] = set()

    def mapped(stock_id: Any) -> int | None:
        target = ids.get(int(stock_id))
        if target is None:
            ticker, market = src_keys.get(int(stock_id), ("?", "?"))
            unmapped.add(f"{ticker}·{market}")
        return target

    # 설정: 키가 같으면 덮는다. **운영 표시는 안 옮긴다** (docs/infra.md 25.156) —
    # "지금 이 DB 가 어떤 상태인지" 는 옮길 데이터가 아니라 그 DB 의 사정이다.
    # 특히 복귀 표시를 옮기면 `auto` 판정이 엉뚱한 DB 를 고른다(25.12·25.155)
    자리 = ", ".join(["?"] * len(설정_제외_사유))
    rows = src.execute(
        f"SELECT key, value, updated_at FROM settings WHERE key NOT IN ({자리})",
        sorted(설정_제외_사유),
    ).rows
    for key, value, updated_at in rows:
        statements.append((
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            [key, value, updated_at],
        ))  # fmt: skip
    counts["settings"] = len(rows)

    # 관심 종목: 종목이 같으면 덮는다
    n = 0
    for stock_id, added_at, memo, target_price, alert in src.execute(
        "SELECT stock_id, added_at, memo, target_buy_price, alert_enabled FROM watchlist"
    ).rows:
        target = mapped(stock_id)
        if target is None:
            continue
        statements.append((
            "INSERT INTO watchlist (stock_id, added_at, memo, target_buy_price, alert_enabled) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (stock_id) DO UPDATE SET memo = excluded.memo,"
            " target_buy_price = excluded.target_buy_price, alert_enabled = excluded.alert_enabled",
            [target, added_at, memo, target_price, alert],
        ))  # fmt: skip
        n += 1
    counts["watchlist"] = n

    # 스크리너 조건: (나라, 이름)이 같으면 덮는다
    rows = src.execute("SELECT name, country, filters_json, created_at, last_used_at FROM screener_presets").rows
    for row in rows:
        statements.append((
            "INSERT INTO screener_presets (name, country, filters_json, created_at, last_used_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (country, name) DO UPDATE SET filters_json = excluded.filters_json,"
            " last_used_at = excluded.last_used_at",
            list(row),
        ))  # fmt: skip
    counts["screener_presets"] = len(rows)

    # 종목 별칭: (종목, 별칭)이 같으면 건너뛴다 (25.848). 받는 DB 에 표가 아직 없으면(0043 미적용) 건너뛰고 알린다 —
    # 이 한 표 때문에 매매·배당 옮기기 전체가 깨지면 안 된다
    def 별칭표(client) -> bool:
        있나 = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'stock_aliases'"
        return bool(client.execute(있나).rows)

    # 양쪽 다 본다 — `--from turso` 처럼 **보내는 쪽**에 0043 이 아직 없어도 전체가 멈추면 안 된다 (25.853, 교차검증)
    if 별칭표(src) and 별칭표(dst):
        n = 0
        못맞춤 = 0
        for stock_id, alias, added_by, created_at in src.execute(
            "SELECT stock_id, alias, added_by, created_at FROM stock_aliases"
        ).rows:
            # `mapped` 를 쓰지 않는다 — 별칭은 뉴스 매칭 보조라, 받는 DB 에 종목이 없다고 옮기기 전체를 "못 맞춤"(종료
            # 코드 1)으로
            # 만들 일이 아니다. 따로 세어 알린다 (25.853)
            target = ids.get(int(stock_id))
            if target is None:
                못맞춤 += 1
                continue
            statements.append((
                "INSERT INTO stock_aliases (stock_id, alias, added_by, created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (stock_id, alias) DO NOTHING",
                [target, alias, added_by, created_at],
            ))  # fmt: skip
            n += 1
        counts["stock_aliases"] = n
        if 못맞춤:
            print(f"받는 DB 에 종목이 없어 별칭 {못맞춤}개는 옮기지 않았다 (뉴스 매칭 보조라 옮기기는 계속한다)")
    else:
        print(
            "보내는 쪽이나 받는 DB 에 stock_aliases 표가 없어 별칭은 옮기지 않았다"
            " — 마이그레이션(0043)을 먼저 적용한다"
        )

    # 매매·배당: 자연 키가 없어 모든 값이 같으면(입력 시각 포함) 이미 있는 것으로 본다
    for table, spec in (("trades", TRADE_COLS), ("dividend_receipts", DIVIDEND_COLS)):
        names = cols(spec)
        existing = {
            (int(r[0]), *r[1:])
            for r in dst.execute(f"SELECT stock_id, {spec} FROM {table}").rows
        }
        n = 0
        for row in src.execute(f"SELECT stock_id, {spec} FROM {table}").rows:
            target = mapped(row[0])
            if target is None:
                continue
            values = [target, *row[1:]]
            if tuple(values) in existing:
                continue
            holes = ", ".join(["?"] * (len(names) + 1))
            statements.append((f"INSERT INTO {table} (stock_id, {spec}) VALUES ({holes})", values))
            n += 1
        counts[table] = n

    # 뉴스: (종목, 주소)가 같으면 건너뛴다. 감성은 기사 번호를 다시 맞춘 뒤에 옮긴다(apply 가 한다)
    n = 0
    for row in src.execute(f"SELECT stock_id, {NEWS_COLS} FROM news").rows:
        target = mapped(row[0])
        if target is None:
            continue
        holes = ", ".join(["?"] * (len(cols(NEWS_COLS)) + 1))
        statements.append((
            f"INSERT INTO news (stock_id, {NEWS_COLS}) VALUES ({holes}) ON CONFLICT (stock_id, url) DO NOTHING",
            [target, *row[1:]],
        ))  # fmt: skip
        n += 1
    counts["news"] = n
    return statements, counts, sorted(unmapped)


def sentiment_statements(src, dst) -> list[tuple[str, list[Any]]]:
    """기사 채점을 옮긴다. 기사 번호가 두 DB 에서 다르므로 (종목코드, 시장, 주소)로 다시 맞춘다."""
    src_keys = stock_keys(src)
    dst_ids = {key: sid for sid, key in stock_keys(dst).items()}
    dst_news = {(int(r[1]), str(r[2])): int(r[0]) for r in dst.execute("SELECT id, stock_id, url FROM news").rows}
    out: list[tuple[str, list[Any]]] = []
    for row in src.execute(
        f"SELECT n.stock_id, n.url, {', '.join('a.' + c for c in cols(SENTIMENT_COLS))}"
        " FROM article_sentiments a JOIN news n ON n.id = a.news_id"
    ).rows:
        key = src_keys.get(int(row[0]))
        target_stock = dst_ids.get(key) if key else None
        news_id = dst_news.get((target_stock, str(row[1]))) if target_stock else None
        if news_id is None:
            continue
        holes = ", ".join(["?"] * (len(cols(SENTIMENT_COLS)) + 1))
        out.append((
            f"INSERT INTO article_sentiments (news_id, {SENTIMENT_COLS}) VALUES ({holes})"
            " ON CONFLICT (news_id, method) DO NOTHING",
            [news_id, *row[2:]],
        ))  # fmt: skip
    return out


def apply(dst, statements: list[tuple[str, list[Any]]]) -> int:
    for start in range(0, len(statements), CHUNK):
        dst.batch(statements[start : start + CHUNK])
    return len(statements)


@dataclass
class 옮긴결과:
    """무엇을 옮겼고 **무엇을 못 옮겼나**.

    예전에는 `run` 이 늘 0 을 돌려줬다. 부르는 쪽(`turso_return.py`)은 그래서
    "다 옮겼다" 고 밖에 말할 수 없었고, 실제로 매매 기록이 버려진 날에도
    **"✅ 옮겼습니다" 라고 텔레그램을 보냈다** (docs/infra.md 25.81).
    """

    counts: dict[str, int] = field(default_factory=dict)
    #: 번호를 못 맞춰 **버린** 종목. 마스터를 옮긴 뒤에도 남았다면 진짜로 버린 것이다
    unmapped: list[str] = field(default_factory=list)
    #: 대상에 없어 마스터째 함께 옮긴 종목 수
    carried: int = 0
    written: int = 0

    @property
    def 온전한가(self) -> bool:
        return not self.unmapped

    def 한줄(self) -> str:
        글 = ", ".join(f"{t} {n:,}" for t, n in self.counts.items()) or "옮길 것 없음"
        if self.carried:
            글 += f" (종목 마스터 {self.carried:,}개 함께)"
        if self.unmapped:
            글 += f" · **버린 종목 {len(self.unmapped)}개**: {', '.join(self.unmapped[:20])}"
        return 글


def run(src, dst, do_apply: bool) -> 옮긴결과:
    """**마스터를 먼저 옮기고** 나서 사람이 넣은 데이터를 옮긴다.

    순서가 중요하다. 마스터가 들어간 **뒤에야** 대상 쪽 종목 번호를 알 수 있다 —
    기사 채점을 기사가 들어간 뒤에 옮기는 것과 같은 이유다.
    """
    carried = 0
    if do_apply:
        마스터 = 마스터_옮길것(src, dst)
        if 마스터:
            carried = apply(dst, 마스터)
            print(f"대상에 없던 종목 {carried:,}개의 마스터를 먼저 옮겼습니다 (사람이 매매·관심에 쓴 종목)")

    statements, counts, unmapped = plan(src, dst)
    결과 = 옮긴결과(counts=counts, unmapped=unmapped, carried=carried)
    print("옮길 행:", 결과.한줄())
    if unmapped:
        print(
            f"번호를 맞추지 못해 건너뛸 종목 {len(unmapped)}개: {', '.join(unmapped[:20])}\n"
            "  → 사람이 넣은 매매·관심이 달린 종목이면 **그 기록은 옮겨지지 않는다**"
        )
    if not do_apply:
        미리 = 마스터_옮길것(src, dst)
        if 미리:
            print(f"시험 실행: 종목 마스터 {len(미리):,}개를 먼저 옮기게 된다 — 위 '버린 종목' 은 그만큼 줄어든다")
        print("시험 실행이라 쓰지 않았습니다. --apply 로 씁니다")
        return 결과

    결과.written = carried + apply(dst, statements)
    # 기사가 들어간 뒤에야 대상 쪽 기사 번호를 알 수 있다
    sentiments = sentiment_statements(src, dst)
    결과.written += apply(dst, sentiments)
    print(f"썼습니다: {결과.written:,}문장 (기사 채점 {len(sentiments):,}건 포함). 포트폴리오 재계산을 돌리세요")
    return 결과


def main() -> int:
    parser = argparse.ArgumentParser(description="사람이 넣은 데이터를 다른 DB 로 옮긴다")
    parser.add_argument("--from", dest="source", choices=["d1", "turso"], required=True)
    parser.add_argument("--to", dest="target", choices=["d1", "turso"], required=True)
    parser.add_argument("--apply", action="store_true", help="실제로 쓴다 (없으면 시험 실행)")
    args = parser.parse_args()
    if args.source == args.target:
        print("원본과 대상이 같습니다")
        return 1
    src, dst = open_client(args.source), open_client(args.target)
    try:
        # **버린 종목이 있으면 종료코드 1.** 워크플로가 초록으로 끝나면 아무도 안 본다
        return 0 if run(src, dst, args.apply).온전한가 else 1
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    sys.exit(main())
