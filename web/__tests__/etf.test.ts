/**
 * ETF 장기 적립 조회 테스트.
 *
 * 질의는 migrations/ 를 적용한 SQLite 에 실제로 돌린다(recommendSql.test.ts 와 같은
 * 방식). 열 이름이나 조인이 틀리면 배포 뒤 500 으로야 드러나기 때문이다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  buildExcludedQuery,
  buildPickedQuery,
  formatAssets,
  formatKrw,
  formatPct,
  formatUsd,
  groupByBucket,
  groupByMarket,
  overlapText,
  parseOverlap,
  type EtfRow,
} from "@/lib/etf";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

let db: DatabaseSync;

function pick(
  id: number,
  asOf: string,
  passed: number,
  bucket: string | null,
  score: number | null,
  reason: string | null,
  version = 2,
) {
  db.prepare(
    `INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, excluded_reason, score,
       rank_in_category, category_size, rationale_text, rationale_data, calc_version, created_at)
     VALUES (?, ?, ?, ?, ?, ?, 1, 1, '문장', '{"criteria":[]}', ?, ?)`,
  ).run(id, asOf, bucket, passed, reason, score, version, asOf);
}

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  const etfs: Array<[number, string, number]> = [
    [1, "VOO", 1.7e12],
    [2, "SPLG", 9.7e10],
    [3, "QQQ", 4.8e11],
    [4, "XLK", 1.2e11],
  ];
  for (const [id, symbol, assets] of etfs) {
    db.prepare(
      `INSERT INTO etfs (id, symbol, country, name, yahoo_symbol, source, fetched_at)
       VALUES (?, ?, 'US', ?, ?, 'test', '2026-09-17')`,
    ).run(id, symbol, `${symbol} ETF`, symbol);
    for (const asOf of ["2026-08-03", "2026-09-17"]) {
      db.prepare(
        `INSERT INTO etf_profiles (etf_id, as_of_date, category, expense_ratio, total_assets, currency,
           source, fetched_at)
         VALUES (?, ?, 'Large Blend', 0.0003, ?, 'USD', 'test', ?)`,
      ).run(id, asOf, assets, asOf);
    }
  }
  // 지난달 판정. 이번 달 조회에 섞이면 안 된다
  pick(4, "2026-08-03", 1, "미국 주식", 99, null);
  // 같은 기준일의 옛 계산 버전. 규칙을 바꿔 재판정하면 생긴다. 섞이면 안 된다
  pick(3, "2026-09-17", 1, "미국 주식", 88, null, 1);
  pick(1, "2026-09-17", 1, "미국 주식", 33.3, null);
  pick(2, "2026-09-17", 1, "미국 주식", 66.7, null);
  pick(3, "2026-09-17", 0, null, null, "좁거나 기운 지수 (분류 Large Growth)");
  pick(4, "2026-09-17", 0, null, null, "좁거나 기운 지수 (분류 Technology)");
});

describe("질의 (실제 스키마)", () => {
  it("가장 최근 판정의 통과분만 읽는다", () => {
    const { sql, args } = buildPickedQuery();
    const rows = db.prepare(sql).all(...args) as unknown as EtfRow[];
    expect(rows.map((r) => r.symbol).sort()).toEqual(["SPLG", "VOO"]);
    expect(rows.every((r) => r.as_of_date === "2026-09-17")).toBe(true);
    expect(rows[0].expense_ratio).toBeCloseTo(0.0003);
  });

  it("국내는 국내 기준일로 따로 읽는다", () => {
    db.prepare(
      `INSERT INTO etfs (id, symbol, country, name, yahoo_symbol, source, fetched_at)
       VALUES (10, '069500', 'KR', 'KODEX 200', '069500.KS', 'test', '2026-09-16')`,
    ).run();
    db.prepare(
      `INSERT INTO etf_profiles (etf_id, as_of_date, category, total_assets, currency, premium_abs_avg,
         days_observed, listed_3y_ago, source, fetched_at)
       VALUES (10, '2026-09-16', '코스피 200', 24715731480706, 'KRW', 0.0004, 20, 1, 't', 't')`,
    ).run();
    pick(10, "2026-09-16", 1, "국내 주식", 100, null);

    const { sql, args } = buildPickedQuery();
    const rows = db.prepare(sql).all(...args) as unknown as EtfRow[];
    const kr = rows.filter((r) => r.country === "KR");
    expect(kr.map((r) => r.symbol)).toEqual(["069500"]);
    expect(kr[0].premium_abs_avg).toBeCloseTo(0.0004);
    // 미국은 미국 기준일(09-17) 그대로
    expect(
      rows
        .filter((r) => r.country === "US")
        .map((r) => r.symbol)
        .sort(),
    ).toEqual(["SPLG", "VOO"]);
  });

  it("뺀 것은 순자산 큰 순이다", () => {
    const { sql, args } = buildExcludedQuery("US", 10);
    const rows = db.prepare(sql).all(...args) as unknown as EtfRow[];
    expect(rows.map((r) => r.symbol)).toEqual(["QQQ", "XLK"]);
    expect(rows[0].excluded_reason).toContain("Large Growth");
  });
});

function row(partial: Partial<EtfRow>): EtfRow {
  return {
    etf_id: 1,
    symbol: "X",
    name: "X",
    country: "US",
    bucket: "미국 주식",
    passed: 1,
    excluded_reason: null,
    score: 50,
    rank_in_category: 1,
    category_size: 1,
    rationale_text: "",
    rationale_data: null,
    as_of_date: "2026-09-17",
    category: "Large Blend",
    family: null,
    expense_ratio: 0.001,
    total_assets: 1e10,
    inception_date: null,
    turnover_est: null,
    premium_abs_avg: null,
    ...partial,
  };
}

describe("묶음", () => {
  it("묶음 순서를 지키고, 분류 안 순위로 자른다", () => {
    const groups = groupByBucket(
      [
        row({
          etf_id: 1,
          bucket: "채권",
          category: "Intermediate Core Bond",
          rank_in_category: 1,
        }),
        row({
          etf_id: 2,
          bucket: "미국 주식",
          category: "Large Blend",
          rank_in_category: 3,
        }),
        row({
          etf_id: 3,
          bucket: "미국 주식",
          category: "Large Blend",
          rank_in_category: 1,
        }),
        row({
          etf_id: 4,
          bucket: "미국 주식",
          category: "Large Blend",
          rank_in_category: 2,
        }),
        row({
          etf_id: 5,
          bucket: "미국 주식",
          category: "Small Blend",
          rank_in_category: 1,
          score: 100,
        }),
      ],
      2,
    );
    expect(groups.map((g) => g.bucket)).toEqual(["미국 주식", "채권"]);
    const us = groups[0];
    expect(us.total).toBe(4);
    // 선택지가 많은 분류가 먼저
    expect(us.categories.map((c) => c.category)).toEqual([
      "Large Blend",
      "Small Blend",
    ]);
    expect(us.categories[0].rows.map((r) => r.etf_id)).toEqual([3, 4]);
    expect(us.categories[0].total).toBe(3);
  });

  it("다른 분류의 100점이 대형주 1위를 밀어내지 않는다", () => {
    // 2026-09-17 시험 실행에서 IWM(혼자라 100점)이 VTI 위에 섰던 경우
    const groups = groupByBucket([
      row({
        etf_id: 1,
        symbol: "IWM",
        category: "Small Blend",
        score: 100,
        rank_in_category: 1,
      }),
      row({
        etf_id: 2,
        symbol: "VTI",
        category: "Large Blend",
        score: 77.8,
        rank_in_category: 1,
      }),
      row({
        etf_id: 3,
        symbol: "VOO",
        category: "Large Blend",
        score: 66.7,
        rank_in_category: 2,
      }),
    ]);
    expect(groups[0].categories[0].rows[0].symbol).toBe("VTI");
  });
});

describe("추천 종목 겹침", () => {
  it("계산하지 않았으면 0% 가 아니라 그 사유를 적는다", () => {
    const overlap = parseOverlap(
      JSON.stringify({
        overlap: {
          available: false,
          basis: "상위 10개 보유종목",
          as_of: null,
          note: "미국 추천 종목이 아직 없어 계산하지 않았습니다",
        },
      }),
    );
    const text = overlapText(overlap);
    expect(text).toContain("아직 없어");
    expect(text).not.toContain("0%");
  });

  it("겹친 종목과 하한임을 적는다", () => {
    const overlap = parseOverlap(
      JSON.stringify({
        overlap: {
          available: true,
          basis: "상위 10개 보유종목",
          as_of: "2026-09-16",
          matched: [
            { symbol: "NVDA", name: "NVIDIA", pct: 0.0755, horizon: "short" },
          ],
          total_pct: 0.0755,
        },
      }),
    );
    expect(overlapText(overlap)).toBe(
      "상위 10개 보유종목 중 추천 종목 7.55% 이상: NVDA 7.55% (단기) (2026-09-16 신호 기준)",
    );
  });

  it("월 1회 계산이라 '오늘' 이라 하지 않고 신호 기준일을 붙인다 (docs/infra.md 25.353)", () => {
    const overlap = parseOverlap(
      JSON.stringify({
        overlap: {
          available: true,
          basis: "상위 10개 보유종목",
          as_of: "2026-08-29",
          matched: [],
        },
      }),
    );
    const text = overlapText(overlap) ?? "";
    expect(text).not.toContain("오늘");
    expect(text).toContain("2026-08-29 신호 기준");
  });

  it("깨진 값은 null", () => {
    expect(parseOverlap("not json")).toBeNull();
    expect(parseOverlap(null)).toBeNull();
    expect(overlapText(null)).toBeNull();
  });
});

describe("표시", () => {
  it("시장 통화로 순자산을 적는다", () => {
    expect(formatKrw(24_715_731_480_706)).toBe("24.72조원");
    expect(formatKrw(100_000_000_000)).toBe("1,000억원");
    expect(formatAssets(1e9, "US")).toBe("$1B");
    expect(formatAssets(1e12, "KR")).toBe("1조원");
  });

  it("시장별로 나누고 판정이 없는 시장도 자리를 남긴다", () => {
    const markets = groupByMarket([row({ etf_id: 1, country: "US" })]);
    expect(markets.map((m) => m.country)).toEqual(["KR", "US"]);
    expect(markets[0].total).toBe(0);
    expect(markets[1].buckets[0].bucket).toBe("미국 주식");
  });

  it("보수와 순자산을 읽기 쉽게", () => {
    expect(formatPct(0.0003)).toBe("0.03%");
    expect(formatPct(null)).toBe("-");
    expect(formatUsd(1.76e12)).toBe("$1.76T");
    expect(formatUsd(1e9)).toBe("$1B");
    expect(formatUsd(5_200_000)).toBe("$5.20M");
  });
});

describe("배치 서식과 반올림이 갈리는 곳 (docs/infra.md 25.282)", () => {
  it("딱 반인 값은 위로 올린다 — 배치(파이썬)는 짝수 쪽이라 '2억원' 이다", () => {
    // 주석이 "배치와 같은 규칙" 이라고만 해서 차이를 몰랐다. 알고 둔 차이임을 여기 박아 둔다
    expect(formatKrw(250_000_000)).toBe("3억원");
  });
});
