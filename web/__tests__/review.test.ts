/**
 * 매매 복기 조회 테스트 (docs/review.md). 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 * 값은 배치가 넣은 것을 그대로 읽는지만 본다. 계산은 tests/test_review.py 가 검증한다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import { OUTCOME_LABEL, REVIEWS, REVIEW_STATS, factorEntries, reviewPnlText, sampleNote, type ReviewRow, type ReviewStat } from "@/lib/review";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't');
    INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)
    VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple Inc.', 'USD', 'active', 't', 't');
    INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
    VALUES (1, 1, 'buy', '2026-06-01', 70000, 10, 'KRW', 1, 'none', 't', 't'),
           (3, 2, 'buy', '2026-06-01', 200, 5, 'USD', 1380, 'auto', 't', 't');
    INSERT INTO trade_reviews (buy_trade_id, stock_id, horizon, buy_date, last_sell_date, currency, quantity_sold, quantity_bought,
      partial, cost, proceeds, return_pct, holding_days, realized_pnl_krw, price_pnl_krw, fx_pnl_krw, has_snapshot, snapshot_as_of,
      score_at_trade, signal_type_at_trade, sentiment_at_trade, factor_scores_at_trade, target_pct, stop_pct, outcome, verdict_text,
      calc_version, created_at)
    VALUES (1, 1, 'mid', '2026-06-01', '2026-07-01', 'KRW', 10, 10, 0, 700000, 770000, 0.1, 30, 70000, 70000, 0, 1, '2026-05-29',
            78, '실적 모멘텀', 12, '{"value": 60, "growth": 80}', 25, -15, 'gain', '중기 · 실적 모멘텀 · 매수 시 점수 78 → +10.0% (30일). 목표 +25% 미달, 손절 -15% 유지', 1, 't'),
           (3, 2, NULL, '2026-06-01', '2026-06-11', 'USD', 2, 5, 1, 400, 380, -0.05, 10, -27000, -27600, 600, 0, NULL,
            NULL, NULL, NULL, NULL, NULL, NULL, 'none', '저장된 근거 없음 → -5.0% (10일), 일부 매도', 1, 't');
    INSERT INTO review_stats (group_key, group_kind, label, n, sample_ok, win_rate, avg_return_pct, median_return_pct, avg_holding_days,
      target_rate, stop_rate, total_pnl_krw, calc_version, created_at)
    VALUES ('signal:실적 모멘텀', 'signal', '실적 모멘텀', 1, 0, 1, 0.1, 0.1, 30, 0, 0, 70000, 1, 't'),
           ('horizon:mid', 'horizon', '중기', 1, 0, 1, 0.1, 0.1, 30, 0, 0, 70000, 1, 't'),
           ('all', 'all', '전체', 2, 0, 0.5, 0.045, 0.025, 20, 0, 0, 43000, 1, 't');
  `);
});

describe("복기 질의", () => {
  it("최근 청산 순이고 종목 이름이 붙는다", () => {
    const rows = db.prepare(REVIEWS).all() as unknown as ReviewRow[];
    expect(rows.map((r) => r.buy_trade_id)).toEqual([1, 3]);
    expect(rows[0].name).toBe("삼성전자");
    expect(rows[0].score_at_trade).toBe(78);
    expect(rows[0].verdict_text).toContain("목표 +25% 미달");
    expect(rows[1].name).toBe("Apple Inc.");
    expect(rows[1].has_snapshot).toBe(0);
    expect(rows[1].partial).toBe(1);
  });

  it("통계는 전체 → 기간 → 신호 순", () => {
    const rows = db.prepare(REVIEW_STATS).all() as unknown as ReviewStat[];
    expect(rows.map((r) => r.group_key)).toEqual(["all", "horizon:mid", "signal:실적 모멘텀"]);
  });
});

describe("표시 도우미", () => {
  it("표본이 부족하면 단정하지 않는 문구, 충분하면 없음", () => {
    expect(sampleNote({ n: 2, sample_ok: 0 })).toBe("표본 2건 — 단정하지 않는다");
    expect(sampleNote({ n: 12, sample_ok: 1 })).toBeNull();
    // 25.704 — 일부 매도만 있는 집단(0건)은 "표본 0건" 이 아니라 끝난 매수가 없다고 말한다
    expect(sampleNote({ n: 0, sample_ok: 0 })).toContain("끝난 매수 0건");
  });

  it("팩터 점수 JSON 은 한국어 이름으로, 깨지면 빈 목록", () => {
    expect(factorEntries('{"value": 60, "growth": 80, "unknown": "x"}')).toEqual([["밸류", 60], ["성장", 80]]);
    expect(factorEntries("{broken")).toEqual([]);
    expect(factorEntries(null)).toEqual([]);
  });

  it("판정 여섯 가지에 모두 이름이 있다 (batch/services/review.outcome_of 와 같은 집합)", () => {
    expect(Object.keys(OUTCOME_LABEL).sort()).toEqual(["flat", "gain", "loss", "none", "stop", "target"]);
  });
});

describe("해외 복기는 주가와 환을 나눠 적는다 (25.241)", () => {
  const 원 = (v: number) => `${v >= 0 ? "+" : ""}${v}원`;

  it("주가 이익·환 손실로 원화가 손실이면 둘을 다 보인다", () => {
    const 글 = reviewPnlText({ currency: "USD", realized_pnl_krw: -4800, price_pnl_krw: 5600, fx_pnl_krw: -10400 }, 원);
    expect(글).toContain("원화 -4800원");
    expect(글).toContain("주가 +5600원");
    expect(글).toContain("환 -10400원");
  });

  it("국내는 원화 손익 하나", () => {
    expect(reviewPnlText({ currency: "KRW", realized_pnl_krw: 1000, price_pnl_krw: 1000, fx_pnl_krw: 0 }, 원)).toBe("+1000원");
  });
});


describe("비용 추정 표시 (25.711)", () => {
  it("추정 비용이 섞인 복기 행에 적는다", () => {
    const 원 = (v: number) => `${v}원`;
    const 행 = { currency: "KRW", realized_pnl_krw: 100, price_pnl_krw: 100, fx_pnl_krw: 0 };
    expect(reviewPnlText({ ...행, cost_estimated: 1 }, 원)).toBe("100원 (비용 일부 추정)");
    expect(reviewPnlText({ ...행, cost_estimated: 0 }, 원)).toBe("100원");
    // 25.716 — 행마다 도는 상관 서브쿼리가 아니라 한 번 묶어 붙인다
    expect(REVIEWS).toContain("GROUP BY buy_trade_id");
    expect(REVIEWS).not.toContain("EXISTS");
  });
});
