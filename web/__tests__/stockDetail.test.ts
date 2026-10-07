/**
 * 종목 상세 조회 테스트 (docs/stock_detail.md). 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 *
 * 완료 기준: 데이터가 없는 항목이 무너지지 않고 "데이터 없음" 사유와 기준 시각을 낸다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  type Exec,
  SECTIONS,
  bandPositions,
  eventOrigin,
  formatAmount,
  formatPct,
  loadOverview,
  loadSection,
  overlaysFor,
  pickAnnual,
  radarPoints,
  rangeStart,
} from "@/lib/stockDetail";
import { METRIC_LABELS, metricLabel } from "@/lib/stockDetail";

let db: DatabaseSync;
const exec: Exec = async (sql, args = []) =>
  db.prepare(sql).all(...args) as Record<string, unknown>[];

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const file of readdirSync(dir)
    .filter((f) => f.endsWith(".sql"))
    .sort())
    db.exec(readFileSync(join(dir, file), "utf-8"));
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, sector) VALUES
      (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't', '전자부품'),
      (2, '000001', 'KOSPI', 'KR', '빈종목', 'KRW', 'active', 't', 't', NULL);
    INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at) VALUES
      (1, '2024-09-01', 1, 1, 1, 50000, 10, 'KRW', 'krx', 't'),
      (1, '2026-09-15', 1, 1, 1, 70000, 10, 'KRW', 'krx', 't'),
      (1, '2026-09-16', NULL, NULL, NULL, 71000, NULL, 'KRW', 'krx', 't');
    INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date, receipt_no, currency, unit,
      revenue, total_equity, source, fetched_at) VALUES
      (1, 2024, '11011', 'A', 0, '2025-03-10', 'r0', 'KRW', 'KRW', 900, 1, 'dart', 't'),
      (1, 2024, '11011', 'A', 1, '2025-03-10', 'r1', 'KRW', 'KRW', 1000, 1, 'dart', 't'),
      (1, 2025, '11011', 'A', 1, '2026-03-10', 'r2', 'KRW', 'KRW', 1100, 1, 'dart', 't'),
      (1, 2025, '11012', 'Q', 1, '2025-08-10', 'r3', 'KRW', 'KRW', 1, 1, 'dart', 't');
    INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, data_points, calc_version, created_at) VALUES
      (1, '2026-09-10', '1Y', 0.9, -0.1, 250, 1, 't'),
      (1, '2026-09-17', '5Y', 0.1, -0.4, 1200, 1, 't'),
      (1, '2026-09-17', '1Y', 0.2, -0.2, 250, 1, 't');
    INSERT INTO valuation_bands (stock_id, as_of_date, metric, current_value, p20, p30, p50, p80, sample, band_rank, currency,
      calc_version, created_at) VALUES (1, '2026-09-17', 'PBR', 1.2, 1.0, 1.1, 1.3, 1.6, 750, 35, 'KRW', 1, 't');
    INSERT INTO valuation_bands (stock_id, as_of_date, metric, current_value, sample, currency, skip_reason, calc_version, created_at)
      VALUES (2, '2026-09-17', 'PBR', NULL, 0, 'KRW', '상장주식수 없음', 1, 't');
    INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json, calc_version, created_at)
      VALUES (1, '2026-09-16', 70, '{"value": 80, "risk": null}', 0.1, '{}', 1, 't');
    INSERT INTO factors (stock_id, as_of_date, factor, raw_json, score, peer_group, peer_size, missing_fields, calc_version, created_at)
      VALUES (1, '2026-09-16', 'value', '{"bp": 0.5}', 80, 'sector:KOSPI:전자부품', 30, '["ep"]', 1, 't');
    INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan, target_price,
      stop_price, size_reduction, rationale_text, rationale_data, calc_version, created_at) VALUES
      (1, '2026-09-15', 'short', '기술적 추세', 1, 2, 'KRW', '[]', 3, 0.5, 1, 'old', '{}', 1, 't'),
      (1, '2026-09-16', 'long', '밸류에이션 밴드', 68000, 70000, 'KRW', '[{"step":1,"ratio":0.4,"price":70000,"amount":null}]',
        105000, 52500, 1, '장기', '{"criteria": []}', 1, 't');
    INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at) VALUES
      (1, 1, '삼성전자 호조', 'https://y/1', '2026-09-16T01:00:00.000Z', 'ko', 'yna_rss', 't'),
      (2, 1, '삼성전자 미채점', 'https://y/2', '2026-09-16T02:00:00.000Z', 'ko', 'yna_rss', 't');
    INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (1, 0.8, 'korfinasc', 't');
  `);
});

describe("빈 종목은 섹션마다 사유를 내고 무너지지 않는다", () => {
  for (const section of SECTIONS) {
    it(section, async () => {
      const r = await loadSection(exec, section, 2, {
        country: "KR",
        today: "2026-09-17",
      });
      expect(r.empty).toBe(true);
      expect(r.reason).toBeTruthy();
    });
  }

  it("머리도 점수·보유 없이 돈다", async () => {
    const o = await loadOverview(exec, 2);
    expect(o?.score).toBeNull();
    expect(o?.factors).toEqual([]);
    expect(o?.position).toBeNull();
  });

  it("머리 질의 하나가 실패해도 나머지는 그리고 못 읽은 것을 말한다 (docs/infra.md 25.590)", async () => {
    const { STOCK_FLAGS } = await import("@/lib/stockDetail");
    const 반쯤: Exec = async (sql, args = []) => {
      if (sql === STOCK_FLAGS) throw new Error("HTTP 503");
      return exec(sql, args);
    };
    const o = await loadOverview(반쯤, 2);
    expect(o?.stock).toBeTruthy();
    expect(o?.unread).toEqual(["매도 플래그"]);
  });

  it("보유 재계산 전 여부를 실제 DB 질의로 판정한다 (25.806)", async () => {
    // 매매 기록이 없고 요약도 없으면 늦지 않았다. 매매 한 건이 들어가면 늦었다
    expect((await loadOverview(exec, 2))?.position_stale).toBe(false);
    const 기록한: Exec = async (sql, args = []) => {
      if (sql.includes("FROM trades) ||")) return [{ version: "t1:2026-09-30|d0:" }];
      return exec(sql, args);
    };
    expect((await loadOverview(기록한, 2))?.position_stale).toBe(true);
    const 못읽음: Exec = async (sql, args = []) => {
      if (sql.includes("portfolio_summary")) throw new Error("HTTP 503");
      return exec(sql, args);
    };
    const o = await loadOverview(못읽음, 2);
    expect(o?.position_stale).toBeNull();
    expect(o?.unread).toEqual([]);
  });

  it("묵은 감성은 날짜와 함께 점수에 쓰지 않는다고 말한다 (25.590)", async () => {
    const { staleSentimentNote } = await import("@/lib/stockDetail");
    expect(staleSentimentNote("2026-07-01", "2026-09-28")).toContain("2026-07-01 감성입니다");
    expect(staleSentimentNote("2026-09-26", "2026-09-28")).toBeNull();
    expect(staleSentimentNote(null, "2026-09-28")).toBeNull();
    // 연휴 뒤 배치 전: 점수 기준일(9/23)에서 재면 방금 쓰인 감성이다 (25.592)
    expect(staleSentimentNote("2026-09-23", "2026-09-23")).toBeNull();
    const 화면 = (await import("node:fs")).readFileSync("components/StockDetail.tsx", "utf8");
    // 점수가 7일 넘게 묵었으면 오늘에서 잰다 (25.594)
    expect(화면).toContain("점수묵음 ? 오늘 : scoreAsOf!.slice(0, 10)");
    expect(화면).toContain('unread.includes("종합 점수") ? "점수를 읽지 못했습니다');
  });

  it("없는 종목은 null", async () => {
    expect(await loadOverview(exec, 999)).toBeNull();
  });

  it("표가 없으면 사유로 바꾼다", async () => {
    const broken: Exec = async () => {
      throw new Error("SQL_PARSE_ERROR: no such table: valuation_bands");
    };
    const r = await loadSection(broken, "valuation", 1);
    expect(r).toMatchObject({
      empty: true,
      reason: expect.stringContaining("표가 아직 없습니다"),
    });
  });
});

describe("데이터가 있으면 기준 시각과 함께", () => {
  it("가격은 마지막 거래일에서 기간을 센다", async () => {
    const r = await loadSection(exec, "prices", 1, { range: "1Y" });
    expect(r.as_of).toBe("2026-09-16");
    expect((r.rows as unknown[]).length).toBe(2);
    const five = await loadSection(exec, "prices", 1, { range: "5Y" });
    expect((five.rows as unknown[]).length).toBe(3);
  });

  it("재무는 연도마다 연결 우선, 오래된 해부터, 분기 보고서 제외", async () => {
    const r = await loadSection(exec, "financials", 1);
    const rows = r.rows as Array<{
      fiscal_year: number;
      consolidated: number;
      revenue: number;
    }>;
    expect(rows.map((x) => [x.fiscal_year, x.consolidated, x.revenue])).toEqual(
      [
        [2024, 1, 1000],
        [2025, 1, 1100],
      ],
    );
    expect(r.as_of).toBe("2026-03-10");
  });

  it("성과는 최신 계산일만, 1Y·3Y·5Y 순", async () => {
    const r = await loadSection(exec, "metrics", 1);
    expect(
      (r.rows as Array<{ window: string; cagr: number }>).map((x) => [
        x.window,
        x.cagr,
      ]),
    ).toEqual([
      ["1Y", 0.2],
      ["5Y", 0.1],
    ]);
  });

  it("밴드를 못 만든 종목은 사유를 보인다", async () => {
    const ok = await loadSection(exec, "valuation", 1);
    expect(ok).toMatchObject({ empty: false, as_of: "2026-09-17" });
    const skipped = await loadSection(exec, "valuation", 2);
    expect(skipped).toMatchObject({ empty: true, reason: "상장주식수 없음" });
  });

  it("신호는 그 나라 최신 신호일 것만, 옛 신호는 날짜로 알린다", async () => {
    const r = await loadSection(exec, "signals", 1, { country: "KR" });
    const rows = r.rows as Array<{ horizon: string; tranches: unknown[] }>;
    expect(rows.map((x) => x.horizon)).toEqual(["long"]);
    expect(rows[0].tranches).toHaveLength(1);
    const none = await loadSection(exec, "signals", 2, { country: "KR" });
    expect(none.reason).toBe("2026-09-16 기준 신호 없음");
  });

  it("분기·반기는 사업보고서를 빼고 오래된 것부터, 금액을 가공하지 않는다", async () => {
    db.exec("DELETE FROM financials WHERE report_code <> '11011'");
    db.exec(`INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,
        receipt_no, currency, unit, revenue, operating_income, source, fetched_at) VALUES
      (1, 2026, '11013', 'Q', 1, '2026-05-15', 'q1', 'KRW', 'KRW', 300, 30, 'dart', 't'),
      (1, 2026, '11012', 'Q', 1, '2026-08-14', 'q2', 'KRW', 'KRW', 650, 70, 'dart', 't'),
      (1, 2025, '11014', 'Q', 1, '2025-11-14', 'q3', 'KRW', 'KRW', 900, 90, 'dart', 't')`);
    const r = await loadSection(exec, "quarterly", 1);
    const rows = r.rows as Array<{ report_code: string; revenue: number }>;
    // 오래된 것부터. 코드 순서가 아니라 시간 순서다 (11013 1분기 < 11012 반기 < 11014 3분기)
    expect(rows.map((x) => x.report_code)).toEqual(["11014", "11013", "11012"]);
    expect(rows[2].revenue).toBe(650); // 보고서 값 그대로 (650 − 300 을 계산하지 않는다)
    expect(r.as_of).toBe("2026-08-14");
    // 사업보고서(11011)는 연간 섹션의 몫이라 여기 없다
    expect(rows.some((x) => x.report_code === "11011")).toBe(false);
    const none = await loadSection(exec, "quarterly", 3);
    expect(none.empty).toBe(true);
    db.exec("DELETE FROM financials WHERE report_code <> '11011'");
  });

  it("배당은 같은 사업연도의 가장 최근 보고서 값을 쓴다", async () => {
    db.exec(`INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date,
        cash_dividend_total, dps_common, payout_ratio, payout_basis, source, fetched_at) VALUES
      (1, 2024, 2024, 'r1', '2025-03-10', 900, 1400, 25.0, '연결', 'dart', 't'),
      (1, 2024, 2025, 'r2', '2026-03-10', 950, 1450, 26.0, '연결', 'dart', 't'),
      (1, 2025, 2025, 'r3', '2026-03-10', 1000, 1500, 27.0, '연결', 'dart', 't')`);
    const r = await loadSection(exec, "dividends", 1);
    const rows = r.rows as Array<{
      fiscal_year: number;
      cash_dividend_total: number;
    }>;
    expect(rows.map((x) => x.fiscal_year)).toEqual([2025, 2024]);
    expect(rows[1].cash_dividend_total).toBe(950); // 2025 보고서가 다시 적은 값
    const none = await loadSection(exec, "dividends", 2);
    expect(none.empty).toBe(true);
    db.exec("DELETE FROM stock_dividends");
  });

  it("신호가 없는 종목은 판정표로 왜 없는지 보인다 (docs/signals.md 9장)", async () => {
    db.exec(`INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at)
      VALUES (2, '2026-09-16', 'mid', 0, 1,
        '[{"label":"성장 점수","display":"40점","threshold":"≥ 70점","source":"scores (팩터 점수)","as_of":"2026-09-16","passed":false}]', 1, 't')`);
    const r = await loadSection(exec, "signals", 2, { country: "KR" });
    expect(r.empty).toBe(false);
    const checks = r.checks as Array<{
      horizon: string;
      passed: boolean;
      failed_count: number;
      rows: Array<{ passed: boolean }>;
    }>;
    expect(checks).toHaveLength(1);
    expect(checks[0]).toMatchObject({
      horizon: "mid",
      passed: false,
      failed_count: 1,
    });
    expect(checks[0].rows[0].passed).toBe(false);
    db.exec("DELETE FROM signal_checks");
  });

  it("한 건도 안 걸린 날에도 오늘 판정표를 보여 준다", async () => {
    /**
     * **있었던 일** (docs/infra.md 25.145). 기준일을 `signals` 의 MAX 로만 잡았다.
     * 그 표에는 **걸린 신호만** 들어간다. 그래서 그 나라에서 한 건도 안 걸린 날에는
     * 기준일이 어제로 남고, 판정표는 오늘 것만 남으므로(`jobs/signals` 가 지난 날짜를
     * 지운다) **화면이 "왜 없나" 를 바로 그날 못 보여 줬다.**
     * 추천 화면은 그 위에서 **어제 신호를 오늘 것처럼** 보여 주고 있었다.
     *
     * 이 상황은 **DB 전체에 오늘 신호가 하나도 없어야** 만들어진다. 그래서 공용 fixture 를
     * 쓰지 않고 따로 연다 — 여기서 `DELETE FROM signals` 를 하면 뒤따르는 검사가 무너진다.
     */
    const local = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const file of readdirSync(dir)
      .filter((f) => f.endsWith(".sql"))
      .sort()) {
      local.exec(readFileSync(join(dir, file), "utf-8"));
    }
    const lexec: Exec = async (sql, args = []) =>
      local.prepare(sql).all(...args) as Record<string, unknown>[];
    local.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (2, '000001', 'KOSPI', 'KR', '빈종목', 'KRW', 'active', 't', 't');
      INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,
          currency, tranche_plan, size_reduction, sector_cap_applied, rationale_text, rationale_data, calc_version, created_at)
      VALUES (2, '2026-09-15', 'mid', '실적 모멘텀', 100, 110, 'KRW', '[]', 1.0, 0, '', '{}', 1, 't');
      INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at)
      VALUES (2, '2026-09-16', 'mid', 0, 1,
        '[{"label":"성장 점수","display":"40점","threshold":"≥ 70점","source":"scores","as_of":"2026-09-16","passed":false}]', 1, 't');
    `);

    const r = await loadSection(lexec, "signals", 2, { country: "KR" });

    expect(r.as_of, "어제 신호를 오늘 기준일이라고 말한다").toBe("2026-09-16");
    expect(r.rows, "어제 신호가 오늘 것처럼 딸려 나왔다").toHaveLength(0);
    const checks = r.checks as Array<{ as_of: string }>;
    expect(checks, "바로 그날 판정표가 사라졌다").toHaveLength(1);
    expect(checks[0].as_of).toBe("2026-09-16");
    expect(r.last_signal_date).toBe("2026-09-15");
  });

  it("뉴스는 미채점 기사도 보이고 감성 집계가 없어도 된다", async () => {
    const r = await loadSection(exec, "news", 1);
    const articles = r.articles as Array<{
      title: string;
      score: number | null;
    }>;
    expect(articles.map((a) => a.score)).toEqual([null, 0.8]);
    expect(r.latest).toBeNull();
    expect(r.as_of).toBe("2026-09-16T02:00:00.000Z");
  });

  it("머리: 점수·팩터 빠진 지표", async () => {
    const o = await loadOverview(exec, 1);
    expect(o?.score?.factor_scores).toEqual({ value: 80, risk: null });
    expect(o?.factors[0].missing).toEqual(["ep"]);
  });
});

describe("표시 도우미", () => {
  it("기간 시작일", () => {
    expect(rangeStart("1Y", "2026-09-16")).toBe("2025-09-15");
  });

  it("연도 고르기는 5개까지", () => {
    const rows = [2026, 2025, 2024, 2023, 2022, 2021].map((y) => ({
      fiscal_year: y,
    }));
    expect(pickAnnual(rows).map((r) => r.fiscal_year)).toEqual([
      2022, 2023, 2024, 2025, 2026,
    ]);
  });

  it("금액과 비율", () => {
    expect(formatAmount(302_231_000_000_000, "KRW")).toBe("302.2조");
    expect(formatAmount(-5_300_000_000, "KRW")).toBe("-53억");
    expect(formatAmount(394_328_000_000, "USD")).toBe("$394.3B");
    expect(formatAmount(null, "USD")).toBe("-");
    expect(formatPct(-0.3512)).toBe("-35.1%");
  });

  it("겹침 선은 저장된 값만", () => {
    expect(overlaysFor(null, null)).toEqual([]);
    const lines = overlaysFor(
      {
        buy_zone_low: 1,
        buy_zone_high: 2,
        target_price: null,
        stop_price: 0.5,
      },
      { avg_price: 1.5 },
    );
    expect(lines.map((l) => l.kind)).toEqual(["zone", "zone", "stop", "cost"]);
  });

  it("밴드 위치는 0~100 안, 값이 하나뿐이면 그리지 않는다", () => {
    const pos = bandPositions({
      p20: 1,
      p30: 1.1,
      p50: 1.3,
      p80: 1.6,
      current_value: 2,
    })!;
    for (const v of Object.values(pos))
      (expect(v).toBeGreaterThan(0), expect(v).toBeLessThan(100));
    expect(pos.current).toBeGreaterThan(pos.p80!);
    expect(bandPositions({ current_value: 1 })).toBeNull();
  });

  it("레이더는 없는 점수를 0 에 찍고 null 로 알린다", () => {
    const pts = radarPoints({ value: 100, risk: null }, 70, 90);
    expect(pts[0]).toMatchObject({ key: "value", score: 100, x: 90, y: 20 });
    expect(pts[4]).toMatchObject({ key: "risk", score: null, x: 90, y: 90 });
  });
});

describe("지표 라벨", () => {
  it("파이썬 FACTOR_METRICS 의 모든 지표에 한글 이름이 있다", () => {
    const { readFileSync } = require("node:fs");
    const { join } = require("node:path");
    const src: string = readFileSync(
      join(process.cwd(), "..", "batch", "services", "scoring.py"),
      "utf8",
    );
    const block = src.slice(
      src.indexOf("FACTOR_METRICS: dict"),
      src.indexOf("\n}\n", src.indexOf("FACTOR_METRICS: dict")),
    );
    const keys = [...block.matchAll(/Metric\("([a-z0-9_]+)"/g)].map(
      (m) => m[1],
    );
    expect(keys.length).toBeGreaterThan(20);
    for (const key of keys) expect(METRIC_LABELS[key], key).toBeTruthy();
  });

  it("모르는 키는 그대로 보여 준다", () => {
    expect(metricLabel("new_metric")).toBe("new_metric");
  });
});

describe("실적 일정 출처 (docs/portfolio.md 5장)", () => {
  it("야후 확정·야후 구간·법정 기한을 구분해 적는다", () => {
    expect(eventOrigin({ source: "yfinance:calendar", is_confirmed: 1 })).toBe(
      "야후 예정일",
    );
    expect(eventOrigin({ source: "yfinance:calendar", is_confirmed: 0 })).toBe(
      "추정: 야후 구간",
    );
    expect(
      eventOrigin({ source: "estimate:filing_deadline", is_confirmed: 0 }),
    ).toBe("추정: 법정 기한");
    expect(eventOrigin({ source: "krx", is_confirmed: 1 })).toBe("확정");
  });
});

describe("분기 표의 열 이름 (docs/data-sources.md 1.3)", () => {
  it("반기 보고서 값은 4~6월 3개월이다 — '반기' 라고 적으면 1~6월 누적으로 읽힌다", async () => {
    const { QUARTER_LABEL } = await import("@/lib/stockDetail");
    expect(QUARTER_LABEL["11012"]).toBe("2분기(4~6월)");
    expect(QUARTER_LABEL["11013"]).toBe("1분기(1~3월)");
    expect(QUARTER_LABEL["11014"]).toBe("3분기(7~9월)");
    expect(
      Object.values(QUARTER_LABEL).some((label) => label.includes("반기")),
    ).toBe(false);
  });
});

describe("실적 일정의 '오늘' 은 그 종목 시장의 현지 날짜다 (25.207)", () => {
  // 2026-10-30 21:00 UTC = 10-31 06:00 KST = 10-30 17:00 EDT. 미국 장 마감 뒤, 한국 아침이다
  const 그때 = new Date("2026-10-30T21:00:00Z");

  beforeAll(() => {
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)
      VALUES (77, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't');
      INSERT INTO earnings_calendar (stock_id, event_type, scheduled_date, is_confirmed, note, source, fetched_at)
      VALUES (77, '실적발표', '2026-10-30', 1, NULL, 'yahoo', 't'), (1, '실적발표', '2026-10-30', 1, NULL, 'yahoo', 't');`);
  });
  afterEach(() => vi.useRealTimers());

  async function 일정(id: number, country: string) {
    vi.useFakeTimers();
    vi.setSystemTime(그때);
    const r = await loadSection(exec, "events", id, { country });
    return (
      (r as { earnings?: Array<{ scheduled_date: string }> }).earnings ?? []
    ).map((e) => e.scheduled_date);
  }

  it("미국 종목은 뉴욕 날짜로 — 그날(10-30) 일정이 아직 남는다", async () => {
    expect(await 일정(77, "US")).toContain("2026-10-30");
  });

  it("국내 종목은 한국 날짜로 — 한국은 이미 10-31 이라 지난 일정이다", async () => {
    expect(await 일정(1, "KR")).not.toContain("2026-10-30");
  });
});

describe("recoveryText (docs/infra.md 25.333)", () => {
  it("표본이 모자라 MDD 가 없으면 '미회복' 이 아니라 '-'", async () => {
    const { recoveryText } = await import("@/lib/stockDetail");
    expect(recoveryText({ mdd: null, mdd_recovery_days: null })).toBe("-");
    expect(recoveryText({ mdd: -0.3, mdd_recovery_days: null })).toBe("미회복");
    expect(recoveryText({ mdd: -0.3, mdd_recovery_days: 42 })).toBe("42일");
  });
});

describe("재무 기준은 종목마다 하나 (docs/infra.md 25.334)", () => {
  it("연결로 바뀐 회사의 연간 표는 연결 해만 — 별도·연결을 섞지 않는다", async () => {
    db.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
        VALUES (3, '000003', 'KOSDAQ', 'KR', '바뀐회사', 'KRW', 'active', 't', 't');
      INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date, receipt_no,
        currency, unit, revenue, source, fetched_at) VALUES
        (3, 2022, '11011', 'A', 0, '2023-03-10', 'x1', 'KRW', 'KRW', 100, 'dart', 't'),
        (3, 2023, '11011', 'A', 1, '2024-03-10', 'x2', 'KRW', 'KRW', 300, 'dart', 't');
    `);
    const r = await loadSection(exec, "financials", 3);
    expect(
      (r.rows as Array<{ fiscal_year: number }>).map((x) => x.fiscal_year),
    ).toEqual([2023]);
  });

  it("밴드는 자본총계의 기준을 함께 읽는다", async () => {
    const r = (await loadSection(exec, "valuation", 1)) as unknown as {
      band: { equity_consolidated: number };
    };
    expect(r.band.equity_consolidated).toBe(1);
  });
});

describe("재무 기준은 가장 늦은 기간을 따른다 (25.550, DART 감사)", () => {
  it("연결을 끊은 회사는 별도로, 분기 표는 보고서 코드를 가로질러 한 기준으로", async () => {
    const { FINANCIALS, QUARTERLY } = await import("@/lib/stockDetail");
    const m = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort())
      m.exec(readFileSync(join(dir, file), "utf-8"));
    m.exec(`
      INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at) VALUES
        (9, '000009', 'KOSPI', 'KR', 'KRW', 'active', 't', 't');
      INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date, receipt_no,
        currency, unit, revenue, source, fetched_at) VALUES
        (9, 2023, '11011', 'A', 1, '2024-03-10', 'a1', 'KRW', 'KRW', 900, 'dart', 't'),
        (9, 2023, '11011', 'A', 0, '2024-03-10', 'a0', 'KRW', 'KRW', 500, 'dart', 't'),
        (9, 2025, '11011', 'A', 0, '2026-03-10', 'b0', 'KRW', 'KRW', 540, 'dart', 't'),
        (9, 2026, '11013', 'Q', 0, '2026-05-10', 'q1', 'KRW', 'KRW', 130, 'dart', 't'),
        (9, 2026, '11012', 'Q', 1, '2026-08-10', 'q2c', 'KRW', 'KRW', 200, 'dart', 't'),
        (9, 2026, '11012', 'Q', 0, '2026-08-10', 'q2s', 'KRW', 'KRW', 140, 'dart', 't');
    `);
    const 연간 = m.prepare(FINANCIALS).all(9) as { fiscal_year: number; consolidated: number }[];
    expect(연간.map((r) => [r.fiscal_year, r.consolidated])).toEqual([[2025, 0], [2023, 0]]);
    const 분기 = m.prepare(QUARTERLY).all(9) as { report_code: string; consolidated: number }[];
    expect(분기.map((r) => r.consolidated)).toEqual([1]); // 반기 연결만 — 1분기 별도와 섞지 않는다
  });
});
