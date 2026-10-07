/**
 * 장기 적립 종목 조회 테스트. 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  buildAccumulationFunnelQuery,
  buildAccumulationGoneQuery,
  buildAccumulationPassedQuery,
  dividendSummary,
  displayName,
  orderFunnel,
} from "@/lib/accumulation";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

const DATA = JSON.stringify({
  criteria: [
    { label: "G10 배당 연속성", display: "5년", threshold: "5/5년", source: "stock_dividends", as_of: "2026-03-12", passed: true },
    { label: "배당 (참고)", display: "FY2025 주당 1,140원", threshold: "참고", source: "stock_dividends", as_of: "2026-03-12", passed: null },
  ],
});

function pick(stockId: number, asOf: string, passed: number, gate: string | null, rank: number | null, version = 1) {
  db.prepare(
    `INSERT INTO stock_accum_picks (stock_id, as_of_date, fiscal_year_to, passed, first_failed_gate, score,
       rank_in_group, group_size, long_signal_on, thresholds_json, rationale_text, rationale_data, calc_version, created_at)
     VALUES (?, ?, 2025, ?, ?, ?, ?, 2, 0, '{}', '문장', ?, ?, 't')`,
  ).run(stockId, asOf, passed, gate, passed ? 70 : null, rank, DATA, version);
}

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  for (let id = 1; id <= 6; id++) {
    db.prepare(
      `INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
       VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 'krx_openapi', '2026-09-16')`,
    ).run(id, `00000${id}`, `종목${id}`);
  }
  // 옛 판정일 — 보이면 안 된다
  pick(1, "2026-08-01", 1, null, 1);
  // 최신 판정일
  pick(1, "2026-09-17", 1, null, 2);
  pick(2, "2026-09-17", 1, null, 1);
  pick(3, "2026-09-17", 0, "G2", null);
  pick(4, "2026-09-17", 0, "G10", null);
  pick(5, "2026-09-17", 0, "G2", null);
  pick(6, "2026-09-17", 0, "G9", null);
});

describe("통과 목록", () => {
  it("최신 판정일만, 순위 순", () => {
    const q = buildAccumulationPassedQuery("KR");
    const rows = db.prepare(q.sql).all(...q.args) as Array<{ stock_id: number; as_of_date: string; name_ko: string }>;
    expect(rows.map((r) => r.stock_id)).toEqual([2, 1]);
    expect(new Set(rows.map((r) => r.as_of_date))).toEqual(new Set(["2026-09-17"]));
    expect(rows[0].name_ko).toBe("종목2");
  });
});

describe("판정 뒤 활성이 아닌 통과 종목 (25.912)", () => {
  it("통과 목록·퍼널 어디에도 없는 그 종목을 따로 센다", () => {
    const q = buildAccumulationGoneQuery("KR");
    const 셈 = () => (db.prepare(q.sql).get(...q.args) as { n: number }).n;
    expect(셈()).toBe(0);
    db.exec("UPDATE stocks SET status = 'delisted' WHERE id = 2");
    try {
      expect(셈()).toBe(1);
      const p = buildAccumulationPassedQuery("KR");
      expect((db.prepare(p.sql).all(...p.args) as Array<{ stock_id: number }>).map((r) => r.stock_id)).toEqual([1]);
    } finally {
      db.exec("UPDATE stocks SET status = 'active' WHERE id = 2");
    }
  });
  it("판정 수에 더하고 안내를 붙인다", () => {
    const 글 = readFileSync("app/api/etf/stocks/route.ts", "utf-8");
    expect(글).toContain("rows.length + 빠진통과 +");
  });
});

describe("퍼널", () => {
  it("게이트 번호 순으로 (G10 이 G2 앞에 오지 않는다)", () => {
    const q = buildAccumulationFunnelQuery("KR");
    const raw = db.prepare(q.sql).all(...q.args) as Array<{ gate: string; n: number }>;
    const funnel = orderFunnel(raw);
    expect(funnel.map((s) => [s.gate, s.failed])).toEqual([
      ["G2", 2],
      ["G9", 1],
      ["G10", 1],
    ]);
    expect(funnel[0].label).toBe("상장 10년");
  });

  it("미국은 이름이 다른 게이트가 있다", () => {
    expect(orderFunnel([{ gate: "G9", n: 1 }], "US")[0].label).toBe("시총 500위 이내");
    expect(orderFunnel([{ gate: "G9", n: 1 }], "KR")[0].label).toBe("시총 400위 이내");
    expect(orderFunnel([{ gate: "G7", n: 1 }], "US")[0].label).toBe("부채비율 ≤ 100%");
  });

  it("미국 조회에 국내 종목이 섞이지 않는다", () => {
    const q = buildAccumulationPassedQuery("US");
    expect(db.prepare(q.sql).all(...q.args)).toEqual([]);
    const f = buildAccumulationFunnelQuery("US");
    expect(db.prepare(f.sql).all(...f.args)).toEqual([]);
  });

  it("게이트가 비어 있는 행은 버린다", () => {
    expect(orderFunnel([{ gate: null, n: 3 }])).toEqual([]);
  });

  it("관문이 아닌 칸이 섞여도 순서가 안 무너진다", () => {
    // **한 줄이 잘못 놓이는 문제가 아니었다** (2026-09-23, docs/infra.md 25.175).
    // 정렬 키가 `Number(gate.slice(1))` 이라 `근거` 에서 NaN 이 나왔고, NaN 이 낀 비교
    // 함수는 늘 NaN 을 돌려줘 **표 전체가 입력 순서 그대로** 남았다 — G10 이 G2 앞에.
    const funnel = orderFunnel([
      { gate: "G10", n: 1 },
      { gate: "근거", n: 2 },
      { gate: "G2", n: 3 },
    ]);

    expect(funnel.map((s) => s.gate)).toEqual(["G2", "G10", "근거"]);
    expect(funnel[2].label, "관문이 아닌 칸도 사람이 읽는 말이어야 한다").toContain("근거표");
  });

  it("관문이 아닌 칸이 여럿이어도 자리가 정해진다", () => {
    // 비교 함수가 0 을 돌려주면 엔진마다 순서가 달라질 수 있다. 늘 같은 순서여야 한다
    const funnel = orderFunnel([
      { gate: "나중", n: 1 },
      { gate: "근거", n: 2 },
      { gate: "G1", n: 3 },
    ]);

    expect(funnel.map((s) => s.gate)).toEqual(["G1", "근거", "나중"]);
  });
});

describe("표시 이름", () => {
  it("나스닥 이름 꼬리를 뗀다", () => {
    expect(displayName("Acme Company - Common Stock")).toBe("Acme Company");
    expect(displayName("Snap-On Incorporated Common Stock")).toBe("Snap-On Incorporated");
    expect(displayName("삼성전자")).toBe("삼성전자");
    expect(displayName(null)).toBeNull();
  });
});

describe("배당 요약", () => {
  it("참고 행의 값을 그대로", () => {
    expect(dividendSummary(DATA)).toBe("FY2025 주당 1,140원");
  });
  it("없거나 깨졌으면 null", () => {
    expect(dividendSummary(null)).toBeNull();
    expect(dividendSummary("{")).toBeNull();
    expect(dividendSummary('{"criteria":[]}')).toBeNull();
  });
});

/**
 * 한쪽 시장만 다시 판정해도 다른 쪽이 안 사라진다 (2026-09-22, docs/infra.md 25.111).
 *
 * 기준일은 나라별로 잡으면서 **계산 버전만 전체에서** `MAX` 로 잡고 있었다.
 * 국내가 새 버전으로 다시 돌고 미국이 못 돌면(실패·건너뜀) 미국은 제 기준일의 옛 버전
 * 행만 있는데 조건은 새 버전을 요구한다 — **한 줄도 안 나온다.**
 * 25.107 이 바로 그 상황을 만들 뻔했다(미국 판정이 파라미터 한도로 죽는다).
 */
describe("계산 버전은 그 나라·그 기준일 안에서 고른다", () => {
  let db2: DatabaseSync;

  beforeAll(() => {
    db2 = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
      db2.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    }
    const 종목 = (id: number, country: string) =>
      db2.prepare(
        `INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
         VALUES (?, ?, ?, ?, ?, 'KRW', 'active', 't', 't')`,
      ).run(id, `T${id}`, country === "KR" ? "KOSPI" : "NASDAQ", country, `종목${id}`);
    const 판정 = (stockId: number, asOf: string, version: number, rank: number) =>
      db2.prepare(
        `INSERT INTO stock_accum_picks (stock_id, as_of_date, fiscal_year_to, passed, score,
           rank_in_group, group_size, long_signal_on, thresholds_json, rationale_text, rationale_data, calc_version, created_at)
         VALUES (?, ?, 2025, 1, 70, ?, 2, 0, '{}', '문장', '{}', ?, 't')`,
      ).run(stockId, asOf, rank, version);

    종목(11, "KR");
    종목(12, "US");
    판정(11, "2026-10-06", 2, 1); // 국내는 새 버전으로 다시 돌았다
    판정(12, "2026-09-06", 1, 1); // 미국은 못 돌아 옛 버전·옛 기준일 그대로다
  });

  it("미국이 제 기준일·제 버전으로 보인다", () => {
    const q = buildAccumulationPassedQuery("US");
    const rows = db2.prepare(q.sql).all(...q.args) as Array<{ stock_id: number; as_of_date: string }>;
    expect(rows.map((r) => r.stock_id)).toEqual([12]);
    expect(rows[0].as_of_date).toBe("2026-09-06");
  });

  it("국내는 새 버전만 보인다", () => {
    const q = buildAccumulationPassedQuery("KR");
    const rows = db2.prepare(q.sql).all(...q.args) as Array<{ stock_id: number }>;
    expect(rows.map((r) => r.stock_id)).toEqual([11]);
  });

  it("같은 날 같은 나라에 두 버전이 있으면 새 것만 본다", () => {
    // 규칙을 바꿔 재판정하면 같은 기준일에 버전만 다른 행이 생긴다. 둘 다 보이면 중복이다
    db2.prepare(
      `INSERT INTO stock_accum_picks (stock_id, as_of_date, fiscal_year_to, passed, score,
         rank_in_group, group_size, long_signal_on, thresholds_json, rationale_text, rationale_data, calc_version, created_at)
       VALUES (12, '2026-09-06', 2025, 1, 80, 1, 2, 0, '{}', '새 문장', '{}', 2, 't')`,
    ).run();
    const q = buildAccumulationPassedQuery("US");
    const rows = db2.prepare(q.sql).all(...q.args) as Array<{ stock_id: number; rationale_text: string }>;
    expect(rows).toHaveLength(1);
    expect(rows[0].rationale_text).toBe("새 문장");
  });
});
