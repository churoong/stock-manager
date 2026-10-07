/**
 * 종목 찾기 질의를 **실제 스키마에 돌려 본다** (recommendSql.test.ts 와 같은 방식).
 *
 * 국내·미국 탭을 붙이면서 바인딩 인자 순서가 바뀌었다. 인자가 한 칸 밀리면
 * SQL 은 오류 없이 돌고 결과만 조용히 틀린다. 그래서 실제로 실행해 행을 확인한다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  MARKETS_BY_COUNTRY,
  SECTORS,
  buildQuery,
  defaultFilters,
  describe as describeFilters,
  screenerFilterSchema,
} from "@/lib/screener";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

let db: DatabaseSync;

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't'),
           (2, '247540', 'KOSDAQ', 'KR', '에코프로비엠', 'KRW', 'active', 't', 't');
    INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)
    VALUES (3, 'AAPL', 'NASDAQ', 'US', 'Apple Inc.', 'USD', 'active', 't', 't'),
           (4, 'KO', 'NYSE', 'US', 'Coca-Cola', 'USD', 'active', 't', 't');

    -- 나라마다 스냅샷 날짜가 다르다. 전체 MAX 로 잡으면 미국이 사라진다
    INSERT INTO universe_members (snapshot_date, stock_id, included, market_cap, avg_turnover_20d, currency, created_at)
    VALUES ('2026-09-16', 1, 1, 1500000000000000, 2000000000000, 'KRW', 't'),
           ('2026-09-16', 2, 1, 20000000000000, 300000000000, 'KRW', 't'),
           ('2026-09-15', 3, 0, NULL, 12000000000, 'USD', 't'),
           ('2026-09-15', 4, 0, NULL, 900000000, 'USD', 't');

    INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)
    VALUES (3, '2026-09-15', 230.5, 'USD', 't', 't');

    -- 사업보고서 한 줄. 사업연도 표시용이다 — 비율은 이 행을 나누지 않고 아래 factors 에서 읽는다 (25.490)
    INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date, receipt_no,
                            currency, unit, total_assets, total_liabilities, total_equity, source, fetched_at)
    VALUES (2, 2025, '11011', 'A', 1, '2026-03-10', 'r', 'KRW', 'KRW', 300, NULL, 200, 't', 't');

    -- 배치가 저장한 팩터 원시값. 삼성전자는 적자(ep < 0) — PER 이 비어야 한다
    INSERT INTO factors (stock_id, as_of_date, factor, raw_json, peer_group, peer_size, calc_version, created_at)
    VALUES (2, '2026-09-16', 'value', '{"ep": 0.05, "bp": 0.5, "sp": 1.0}', 'market:KOSDAQ', 10, 1, 't'),
           (2, '2026-09-16', 'quality', '{"roe": 0.1, "debt_ratio": 0.5, "operating_margin": 0.12}', 'market:KOSDAQ', 10, 1, 't'),
           (2, '2026-09-16', 'growth', '{"revenue_growth": 0.2, "operating_income_growth": -0.1}', 'market:KOSDAQ', 10, 1, 't'),
           (1, '2026-09-16', 'value', '{"ep": -0.02, "bp": 0.8}', 'market:KOSPI', 10, 1, 't');
  `);
});

function run(input: object) {
  const { sql, args } = buildQuery(screenerFilterSchema.parse(input));
  return db.prepare(sql).all(...args) as Array<Record<string, unknown>>;
}

describe("국내", () => {
  it("기본 조건으로 국내 종목만 나온다", () => {
    const rows = run(defaultFilters("KR"));
    expect(rows.map((r) => r.ticker).sort()).toEqual(["005930", "247540"]);
  });

  it("시장과 금액(억원) 조건이 제자리에 들어간다", () => {
    // 시총 1,000조(=10,000,000억) 이상 → 삼성전자만
    const rows = run({
      country: "KR",
      market: "KOSPI",
      market_cap: { min: 10_000_000, max: null },
    });
    expect(rows.map((r) => r.ticker)).toEqual(["005930"]);
  });
});

describe("미국", () => {
  it("스냅샷 날짜가 국내와 달라도 미국 종목이 나온다", () => {
    const rows = run(defaultFilters("US"));
    expect(rows.map((r) => r.ticker)).toEqual(["AAPL", "KO"]); // 거래대금 큰 순
    expect(rows[0].close).toBe(230.5);
  });

  it("금액은 백만달러로 받는다", () => {
    // 거래대금 1,000$M(=10억 달러) 이상 → AAPL 만
    const rows = run({
      ...defaultFilters("US"),
      avg_turnover_20d: { min: 1_000, max: null },
    });
    expect(rows.map((r) => r.ticker)).toEqual(["AAPL"]);
  });

  it("시장으로 거를 수 있다", () => {
    const rows = run({ ...defaultFilters("US"), market: "NYSE" });
    expect(rows.map((r) => r.ticker)).toEqual(["KO"]);
  });

  it("유니버스만 켜면 편입 0개라 비어 있다", () => {
    expect(run({ ...defaultFilters("US"), universe_only: true })).toHaveLength(
      0,
    );
  });

  it("나라에 없는 시장은 거부한다", () => {
    expect(
      screenerFilterSchema.safeParse({ country: "US", market: "KOSPI" })
        .success,
    ).toBe(false);
    expect(
      screenerFilterSchema.safeParse({ country: "KR", market: "NYSE" }).success,
    ).toBe(false);
  });

  it("조건 문장에 나라와 단위가 드러난다", () => {
    const text = describeFilters({
      ...defaultFilters("US"),
      avg_turnover_20d: { min: 5, max: null },
    });
    expect(text[0]).toContain("미국");
    expect(text.join(" ")).toContain("$M");
  });
});

describe("업종·감성 조건 (Step 33)", () => {
  beforeAll(() => {
    db.exec(`
      UPDATE stocks SET sector = '반도체' WHERE id = 1;
      UPDATE stocks SET sector = '2차전지' WHERE id = 2;
      INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count, negative_count,
        negative_count_7d, decay_halflife_days, method, calc_version, created_at)
      VALUES (1, '2026-09-10', -30, 5, 1, 4, 2, 7, 'korfinasc', 1, 't'),
             (1, '2026-09-16', 45, 8, 6, 1, 0, 7, 'korfinasc', 1, 't');
    `);
  });

  it("업종 목록은 그 나라의 실제 값만", () => {
    const rows = db.prepare(SECTORS).all("KR") as Array<{ sector: string }>;
    expect(rows.map((r) => r.sector)).toEqual(["2차전지", "반도체"]);
    expect(db.prepare(SECTORS).all("US")).toEqual([]);
  });

  it("업종으로 거르면 그 업종만 나온다", () => {
    const f = {
      ...defaultFilters("KR"),
      universe_only: false,
      sector: "반도체",
    };
    const { sql, args } = buildQuery(f);
    const rows = db.prepare(sql).all(...args) as Array<{
      ticker: string;
      sector: string;
    }>;
    expect(rows.map((r) => r.ticker)).toEqual(["005930"]);
    expect(describeFilters(f)).toContain("업종 반도체");
  });

  it("감성은 가장 최근 기준일 값을 쓰고, 범위로 거른다", () => {
    const base = { ...defaultFilters("KR"), universe_only: false };
    const all = db
      .prepare(buildQuery(base).sql)
      .all(...buildQuery(base).args) as Array<{
      ticker: string;
      sentiment: number | null;
      sentiment_date: string | null;
    }>;
    const samsung = all.find((r) => r.ticker === "005930")!;
    expect(samsung.sentiment).toBe(45); // 9/10 의 −30 이 아니라 9/16 의 45
    expect(samsung.sentiment_date).toBe("2026-09-16");
    expect(all.find((r) => r.ticker === "247540")?.sentiment).toBeNull();

    const positive = { ...base, sentiment: { min: 40, max: null } };
    const q = buildQuery(positive);
    const rows = db.prepare(q.sql).all(...q.args) as Array<{ ticker: string }>;
    expect(rows.map((r) => r.ticker)).toEqual(["005930"]); // 감성이 없는 종목은 조건에서 빠진다
  });

  it("업종과 감성을 함께 걸어도 인자 순서가 맞는다", () => {
    const f = {
      ...defaultFilters("KR"),
      universe_only: false,
      market: "KOSPI" as const,
      sector: "반도체",
      sentiment: { min: 0, max: 100 },
    };
    const { sql, args } = buildQuery(f);
    expect((sql.match(/\?/g) ?? []).length).toBe(args.length);
    const rows = db.prepare(sql).all(...args) as Array<{ ticker: string }>;
    expect(rows.map((r) => r.ticker)).toEqual(["005930"]);
  });
});

/**
 * 성과 지표 기준일도 **나라별**이다 (2026-09-22, docs/infra.md 25.112).
 *
 * 유니버스 스냅샷은 나라별로 잡으면서 `performance_metrics` 만 전체 MAX 였다.
 * 나라마다 `jobs/metrics` 가 따로 도는데(지금은 미국이 쉰다 — 25.14) 전체에서 잡으면
 * 늦은 쪽 종목의 CAGR·MDD·샤프가 **통째로 NULL** 이다. LEFT JOIN 이라 행은 남아
 * 화면은 "지표가 아직 없나 보다" 로 보인다 — 25.0 의 「잃고서 초록으로 알린다」.
 */
describe("성과 지표 기준일", () => {
  let db2: DatabaseSync;

  beforeAll(() => {
    db2 = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS)
      .filter((f) => f.endsWith(".sql"))
      .sort()) {
      db2.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    }
    db2.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't'),
             (3, 'AAPL', 'NASDAQ', 'US', NULL, 'Apple Inc.', 'USD', 'active', 't', 't');
      INSERT INTO universe_members (snapshot_date, stock_id, included, market_cap, avg_turnover_20d, currency, created_at)
      VALUES ('2026-09-22', 1, 1, 1000, 100, 'KRW', 't'),
             ('2026-09-18', 3, 1, 2000, 200, 'USD', 't');
      -- 국내는 오늘, 미국은 나흘 전. 전체 MAX 로 잡으면 미국 지표가 통째로 빠진다
      INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, sharpe, data_points, calc_version, created_at)
      VALUES (1, '2026-09-22', '3Y', 0.11, -0.22, 0.9, 700, 2, 't'),
             (3, '2026-09-18', '3Y', 0.33, -0.44, 1.5, 700, 2, 't');
    `);
  });

  function 돌리기(country: string) {
    const { sql, args } = buildQuery(
      screenerFilterSchema.parse({ country, metric_window: "3Y" }),
    );
    return db2.prepare(sql).all(...args) as Array<Record<string, unknown>>;
  }

  it("미국 종목도 제 기준일의 지표가 보인다", () => {
    const rows = 돌리기("US");
    expect(rows).toHaveLength(1);
    expect(rows[0].cagr).toBeCloseTo(0.33);
    expect(rows[0].sharpe).toBeCloseTo(1.5);
  });

  it("국내는 그대로다", () => {
    const rows = 돌리기("KR");
    expect(rows[0].cagr).toBeCloseTo(0.11);
  });

  // 국내 두 종목을 더한다. 두 테스트가 함께 쓰므로 **어느 쪽이 먼저 돌아도** 같게 OR IGNORE (25.779, 교차검증 — 순서 의존)
  function 국내_둘_더하기() {
    db2.exec(`
      INSERT OR IGNORE INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (2, '000660', 'KOSPI', 'KR', 'SK하이닉스', NULL, 'KRW', 'active', 't', 't'),
             (4, '035420', 'KOSPI', 'KR', 'NAVER', NULL, 'KRW', 'active', 't', 't');
      INSERT OR IGNORE INTO universe_members (snapshot_date, stock_id, included, market_cap, avg_turnover_20d, currency, created_at)
      VALUES ('2026-09-22', 2, 1, 900, 90, 'KRW', 't'), ('2026-09-22', 4, 1, 800, 80, 'KRW', 't');
      INSERT OR IGNORE INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, sharpe, data_points, calc_version, created_at)
      VALUES (2, '2026-09-22', '3Y', 0.05, -0.30, 0.4, 700, 2, 't'),
             (4, '2026-09-22', '3Y', 0.07, -0.25, 0.6, 700, 2, 't'),
             (2, '2026-09-23', '3Y', 0.06, -0.31, 0.5, 700, 2, 't'),
             (1, '2026-09-23', '1Y', 0.01, -0.10, 0.1, 250, 2, 't');
    `);
  }

  it("한 종목·한 기간만 새로 계산해도 나머지 종목의 지표가 비지 않는다 (25.775)", () => {
    // 국내 한 종목만 다음 날짜로 3Y 를 다시 계산했다 — 예전에는 이 날짜가 기준일이 되어 나머지가 NULL
    국내_둘_더하기();
    const rows = 돌리기("KR");
    expect(rows.every((r) => r.cagr !== null)).toBe(true);
    expect(rows[0].metrics_asof).toBe("2026-09-22");
  });

  it("MDD 조건은 낙폭의 크기로 받는다 (25.770)", () => {
    const 걸기 = (max: number) => {
      const { sql, args } = buildQuery(
        screenerFilterSchema.parse({ country: "KR", metric_window: "3Y", mdd: { min: null, max } }),
      );
      return db2.prepare(sql).all(...args) as Array<Record<string, unknown>>;
    };
    국내_둘_더하기(); // 낙폭 22%·30%·25% 세 종목
    expect(걸기(30)).toHaveLength(3); // 모두 "30% 이내"
    expect(걸기(24)).toHaveLength(1); // 22% 하나만
    expect(걸기(20)).toHaveLength(0); // "20% 이내" 에는 없다 — 예전엔 mdd*100 <= 20 이라 늘 통과
  });

  it("한 종목이 두 번 나오지 않는다 (판이 둘이어도)", () => {
    // 계산 판을 올리면 같은 (종목, 기준일, 창)에 두 행이 남는다 (25.102)
    db2
      .prepare(
        `INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, sharpe, data_points, calc_version, created_at)
       VALUES (3, '2026-09-18', '3Y', 0.99, -0.11, 2.0, 700, 3, 't')`,
      )
      .run();
    const rows = 돌리기("US");
    expect(rows).toHaveLength(1);
    expect(rows[0].cagr).toBeCloseTo(0.99);
  });
});

describe("성과 지표 기준일 — 지금 유니버스의 절반 이상을 계산한 날 (docs/infra.md 25.779·25.782)", () => {
  function 새_db(metrics: string, 편입: number[] = [1, 2, 4]): DatabaseSync {
    const d = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS)
      .filter((f) => f.endsWith(".sql"))
      .sort()) {
      d.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    }
    d.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't'),
             (2, '000660', 'KOSPI', 'KR', 'SK하이닉스', NULL, 'KRW', 'active', 't', 't'),
             (4, '035420', 'KOSPI', 'KR', 'NAVER', NULL, 'KRW', 'active', 't', 't'),
             (5, '005380', 'KOSPI', 'KR', '현대차', NULL, 'KRW', 'active', 't', 't'),
             (6, '051910', 'KOSPI', 'KR', 'LG화학', NULL, 'KRW', 'active', 't', 't');
      INSERT INTO universe_members (snapshot_date, stock_id, included, market_cap, avg_turnover_20d, currency, created_at)
      VALUES ${[1, 2, 4, 5, 6].map((id) => `('2026-09-29', ${id}, ${편입.includes(id) ? 1 : 0}, 1000, 100, 'KRW', 't')`).join(", ")};
      INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, sharpe, data_points, calc_version, created_at)
      VALUES ${metrics};
    `);
    return d;
  }
  function 기준일(d: DatabaseSync): unknown {
    const { sql, args } = buildQuery(screenerFilterSchema.parse({ country: "KR", metric_window: "3Y", universe_only: false }));
    return (d.prepare(sql).all(...args) as Array<Record<string, unknown>>)[0]?.metrics_asof;
  }
  const 행 = (id: number, d: string, v = 3) => `(${id}, '${d}', '3Y', 0.1, -0.2, 0.5, 700, ${v}, 't')`;

  it("같은 날 새 판으로 다시 돌린 날(행이 두 배)이 뒤의 날을 막지 않는다", () => {
    // 09-22 는 판 2·3 이 세 종목씩(6행), 09-29 는 두 종목. 행 수로 세면 2×2 < 6 으로 09-22 에 묶였다(25.775)
    const d = 새_db([행(1, "2026-09-22", 2), 행(2, "2026-09-22", 2), 행(4, "2026-09-22", 2), 행(1, "2026-09-22"),
      행(2, "2026-09-22"), 행(4, "2026-09-22"), 행(1, "2026-09-29"), 행(2, "2026-09-29")].join(", "));
    expect(기준일(d)).toBe("2026-09-29");
  });

  it("한 종목을 두 판으로 계산한 날(두 행)을 넓은 날로 세지 않는다 — 종목 수로 센다", () => {
    // 세 종목 유니버스. 09-29 는 한 종목을 판 2·3 으로(두 행). 행 수로 세면 2×2 ≥ 3 으로 넓은 날이 된다
    const d = 새_db([행(1, "2026-09-22"), 행(2, "2026-09-22"), 행(4, "2026-09-22"), 행(1, "2026-09-29", 2),
      행(1, "2026-09-29")].join(", "));
    expect(기준일(d)).toBe("2026-09-22");
  });

  it("유니버스가 줄면 옛 넓은 날에 묶이지 않는다", () => {
    // 06-01 에 세 종목, 지금 유니버스는 한 종목. 전 기간 최대(3)와 견주면 영구히 06-01 이었다
    const d = 새_db([행(1, "2026-06-01"), 행(2, "2026-06-01"), 행(4, "2026-06-01"), 행(1, "2026-09-29")].join(", "), [1]);
    expect(기준일(d)).toBe("2026-09-29");
  });

  it("배치가 40일 넘게 멈춘 뒤 한 종목만 돌린 날은 기준일이 아니다 (25.782, 교차검증)", () => {
    // 25.779 는 마지막 날부터 40일 안에서만 견줘 09-29(한 종목)를 골랐다 — 나머지 두 종목의 지표가 비었다
    const d = 새_db([행(1, "2026-08-10"), 행(2, "2026-08-10"), 행(4, "2026-08-10"), 행(1, "2026-09-29")].join(", "));
    expect(기준일(d)).toBe("2026-08-10");
  });

  it("절반을 넘긴 날이 없으면(유니버스가 크게 늘었다) 가장 넓게 계산된 날", () => {
    const d = 새_db([행(1, "2026-08-10"), 행(1, "2026-09-01"), 행(2, "2026-09-01"), 행(1, "2026-09-29")].join(", "), [1, 2, 4]);
    // 세 종목 유니버스에 두 종목이면 절반을 넘는다 — 넘는 날 중 가장 새 날
    expect(기준일(d)).toBe("2026-09-01");
    // 다섯 종목 유니버스에 두 종목(08-10)·한 종목(09-29) — 절반을 넘는 날이 없어 더 넓은 08-10
    const e = 새_db([행(1, "2026-08-10"), 행(2, "2026-08-10"), 행(1, "2026-09-29")].join(", "), [1, 2, 4, 5, 6]);
    expect(기준일(e)).toBe("2026-08-10");
  });
});

/**
 * 비율은 **배치 값(factors.raw_json)의 단위만 바꾼 것**이다 (docs/infra.md 25.490, docs/screener.md 6장).
 *
 * 예전에 여기 있던 "부채총계가 비면 자산 − 자본"(25.239)·"연결이 없으면 별도"(25.314) 검사는 지웠다 —
 * 웹이 financials 를 더는 나누지 않고, 그 두 규칙은 배치(scoring.quality_inputs, db.FINANCIAL_BASIS_*)의 pytest 가 지킨다.
 */
describe("비율은 배치 값을 읽는다 (docs/infra.md 25.490)", () => {
  it("PER=1/ep · PBR=1/bp · 나머지는 × 100, 기준일이 붙는다", () => {
    const r = run({ ...defaultFilters("KR"), universe_only: false }).find((x) => x.ticker === "247540")!;
    expect(r.per).toBeCloseTo(20); // 1 / 0.05
    expect(r.pbr).toBeCloseTo(2); // 1 / 0.5
    expect(r.roe).toBeCloseTo(10);
    expect(r.debt_ratio).toBeCloseTo(50); // 대체식은 배치가 이미 적용한 값
    expect(r.operating_margin).toBeCloseTo(12);
    expect(r.revenue_growth).toBeCloseTo(20);
    expect(r.operating_income_growth).toBeCloseTo(-10);
    expect(r.factors_asof).toBe("2026-09-16");
    expect(r.fiscal_year).toBe(2025);
  });

  it("ep ≤ 0(적자)이면 PER 이 비고 PBR 은 남는다. 팩터가 없는 칸은 NULL", () => {
    const r = run({ ...defaultFilters("KR"), universe_only: false }).find((x) => x.ticker === "005930")!;
    expect(r.per).toBeNull();
    expect(r.pbr).toBeCloseTo(1.25);
    expect(r.roe).toBeNull(); // quality 행이 없다
    expect(r.revenue_growth).toBeNull();
  });

  it("비율 조건과 정렬이 배치 값으로 걸린다", () => {
    const 걸면 = run({ ...defaultFilters("KR"), universe_only: false, debt_ratio: { min: null, max: 100 } });
    expect(걸면.map((r) => r.ticker)).toEqual(["247540"]);
    const per순 = run({ ...defaultFilters("KR"), universe_only: false, sort_by: "per", sort_desc: false });
    expect(per순.map((r) => r.ticker)).toEqual(["247540", "005930"]); // PER 없는 쪽이 뒤
  });

  it("재발 검사: 조회 SQL 에 financials 나눗셈이 없다", () => {
    const { sql } = buildQuery(screenerFilterSchema.parse({}));
    expect(sql).not.toMatch(/\/\s*(f|prev)\./); // `/ f.net_income`, `/ prev.revenue` 같은 것
    expect(sql).not.toMatch(/CAST\(u\.market_cap/);
    expect(sql).not.toMatch(/financials\s+prev\b/);
  });
});

describe("팩터 기준일·판 (docs/infra.md 25.490)", () => {
  let db3: DatabaseSync;

  beforeAll(() => {
    db3 = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
      db3.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    }
    db3.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't'),
             (2, '000660', 'KOSPI', 'KR', 'SK하이닉스', NULL, 'KRW', 'active', 't', 't'),
             (3, 'AAPL', 'NASDAQ', 'US', NULL, 'Apple Inc.', 'USD', 'active', 't', 't');
      INSERT INTO universe_members (snapshot_date, stock_id, included, market_cap, avg_turnover_20d, currency, created_at)
      VALUES ('2026-09-16', 1, 1, 1000, 100, 'KRW', 't'),
             ('2026-09-16', 2, 1, 900, 90, 'KRW', 't'),
             ('2026-09-15', 3, 1, 2000, 200, 'USD', 't');
      -- 삼성전자: 옛 날짜(09-15)와 새 날짜(09-16), 새 날짜에 판 1·2. 판 2 의 값을 써야 한다
      INSERT INTO factors (stock_id, as_of_date, factor, raw_json, peer_group, peer_size, calc_version, created_at)
      VALUES (1, '2026-09-15', 'value', '{"ep": 0.5}', 'g', 1, 1, 't'),
             (1, '2026-09-15', 'quality', '{"roe": 0.5}', 'g', 1, 1, 't'),
             (1, '2026-09-16', 'value', '{"ep": 0.25}', 'g', 1, 1, 't'),
             (1, '2026-09-16', 'quality', '{"roe": 0.25}', 'g', 1, 1, 't'),
             (1, '2026-09-16', 'value', '{"ep": 0.1}', 'g', 1, 2, 't'),
             (1, '2026-09-16', 'quality', '{"roe": 0.07}', 'g', 1, 2, 't'),
             (1, '2026-09-16', 'growth', '{"revenue_growth": 0.03}', 'g', 1, 2, 't'),
             -- 미국이 더 늦은 날짜·더 높은 판이어도 국내 기준일·판을 가리지 않는다 (나라별)
             (3, '2026-09-20', 'value', '{"ep": 0.04}', 'g', 1, 9, 't');
    `);
  });

  function 돌리기(country: "KR" | "US") {
    const { sql, args } = buildQuery(screenerFilterSchema.parse({ country, universe_only: false }));
    return db3.prepare(sql).all(...args) as Array<Record<string, unknown>>;
  }

  it("두 판·두 날짜가 있어도 한 종목은 한 행이고 가장 새 날짜·판 값을 쓴다", () => {
    const rows = 돌리기("KR");
    expect(rows.filter((r) => r.ticker === "005930")).toHaveLength(1);
    const r = rows.find((x) => x.ticker === "005930")!;
    expect(r.per).toBeCloseTo(10); // 1 / 0.1 (판 2)
    expect(r.roe).toBeCloseTo(7);
    expect(r.revenue_growth).toBeCloseTo(3);
    expect(r.factors_asof).toBe("2026-09-16");
  });

  it("팩터 행이 없는 종목은 비율 열이 NULL 이고 행은 남는다", () => {
    const r = 돌리기("KR").find((x) => x.ticker === "000660")!;
    for (const 칸 of ["per", "pbr", "roe", "debt_ratio", "operating_margin", "revenue_growth", "operating_income_growth"]) {
      expect(r[칸], 칸).toBeNull();
    }
  });

  it("미국은 제 기준일·판을 쓴다", () => {
    const r = 돌리기("US")[0];
    expect(r.per).toBeCloseTo(25);
    expect(r.factors_asof).toBe("2026-09-20");
  });

  it("팩터 기준일·판 하위질의는 상관이 아니다 (docs/infra.md 25.428)", () => {
    const { sql, args } = buildQuery(screenerFilterSchema.parse({}));
    const 줄 = (db3.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args) as Array<{ detail: string }>).map((r) => r.detail);
    // 팩터 표를 훑는 줄(fx·fc)과 CTE 를 읽는 줄마다, 바로 위의 하위질의 머리가 CORRELATED 가 아니어야 한다
    const 대상 = 줄
      .map((l, i) => [l, i] as const)
      .filter(([l]) => /^(SEARCH|SCAN) (fx|fc|factors_asof|factors_ver)\b/.test(l));
    expect(대상.length).toBeGreaterThan(0);
    for (const [l, i] of 대상) {
      let j = i - 1;
      while (j >= 0 && !/SUBQUERY/.test(줄[j])) j--;
      if (j >= 0) expect(줄[j], l).not.toMatch(/CORRELATED/);
    }
    // 이름을 바꿔 다시 상관으로 짜도 걸리게: 상관 하위질의 바로 아랫줄이 팩터 색인을 타면 안 된다
    줄.forEach((l, i) => {
      if (/^CORRELATED/.test(l)) expect(줄[i + 1] ?? "", l).not.toMatch(/factors/);
    });
    // 세 팩터 줄은 UNIQUE 색인으로 한 행씩 찾는다
    for (const a of ["fv", "fq", "fg"]) {
      expect(줄.some((l) => l.startsWith(`SEARCH ${a} USING INDEX sqlite_autoindex_factors_1`)), a).toBe(true);
    }
  });
});

describe("화면이 고를 수 있는 시장은 스키마도 받는다 (docs/infra.md 25.259)", () => {
  it("모든 시장 선택지가 통과한다", () => {
    for (const country of ["KR", "US"] as const) {
      for (const [code] of MARKETS_BY_COUNTRY[country]) {
        expect(
          screenerFilterSchema.safeParse({ country, market: code }).success,
          code,
        ).toBe(true);
      }
    }
  });
});
