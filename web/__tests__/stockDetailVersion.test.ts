/**
 * 종목 상세의 신호는 그 나라·그 기준일의 가장 새 판만 (docs/infra.md 25.466).
 * 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import { type Exec, lastSignalDate, loadSection } from "@/lib/stockDetail";

let db: DatabaseSync;
const exec: Exec = async (sql, args = []) => db.prepare(sql).all(...args) as Record<string, unknown>[];

function 신호(sid: number, d: string, h: string, v: number): string {
  return `(${sid}, '${d}', '${h}', 'x', 1, 2, 'KRW', '[]', 3, 1, 1, 't', '{}', ${v}, 't')`;
}

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort())
    db.exec(readFileSync(join(dir, file), "utf-8"));
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES
      (1, 'A', 'KOSPI', 'KR', '가', 'KRW', 'active', 't', 't'),
      (2, 'B', 'KOSPI', 'KR', '나', 'KRW', 'active', 't', 't');
    INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan,
      target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version, created_at) VALUES
      ${신호(1, "2026-09-10", "short", 1)}, ${신호(2, "2026-09-10", "short", 1)},
      ${신호(1, "2026-09-16", "short", 1)}, ${신호(2, "2026-09-16", "short", 1)},
      ${신호(1, "2026-09-16", "short", 2)};
  `);
});

describe("옛 판 신호", () => {
  it("새 판이 걸러 낸 종목은 그날 신호가 없고, 마지막 신호일은 옛 판이 유일하던 날이다", async () => {
    const 걸러짐 = await loadSection(exec, "signals", 2, { country: "KR" });
    expect(걸러짐.rows).toEqual([]);
    expect(걸러짐.last_signal_date).toBe("2026-09-10");
    expect(걸러짐.reason).toBe("2026-09-16 기준 신호 없음 (마지막 신호 2026-09-10)");
  });

  it("새 판에 남은 종목은 새 판 행 하나", async () => {
    const r = await loadSection(exec, "signals", 1, { country: "KR" });
    const rows = r.rows as Array<{ calc_version: number }>;
    expect(rows.map((x) => x.calc_version)).toEqual([2]);
    expect(await lastSignalDate(exec, 1, "KR")).toBe("2026-09-16");
  });
});

describe("매수 스냅샷의 신호 종류", () => {
  it("새 판이 걸러 낸 종목의 옛 판 신호 종류를 얼리지 않는다", async () => {
    const { SNAPSHOT_AT } = await import("@/lib/portfolio");
    db.exec(`INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json,
      calc_version, created_at) VALUES (1, '2026-09-16', 70, '{}', 0, '{}', 1, 't'), (2, '2026-09-16', 60, '{}', 0, '{}', 1, 't')`);
    const 걸러짐 = db.prepare(SNAPSHOT_AT).get("short", 2, "2026-09-17") as Record<string, unknown>;
    expect(걸러짐.as_of_date).toBe("2026-09-16");
    expect(걸러짐.signal_type).toBeNull();
    const 남음 = db.prepare(SNAPSHOT_AT).get("short", 1, "2026-09-17") as Record<string, unknown>;
    expect(남음.signal_type).toBe("x");
  });
});

describe("매수 스냅샷의 판 세기 (docs/infra.md 25.467)", () => {
  it("그날 가장 새 판을 한 번만 센다 — 신호 전부를 두 번 읽지 않는다", async () => {
    const { SNAPSHOT_AT } = await import("@/lib/portfolio");
    const plan = db.prepare(`EXPLAIN QUERY PLAN ${SNAPSHOT_AT}`).all("short", 1, "2026-09-17") as Array<{ detail: string }>;
    const 판세기 = plan.filter((r) => /SEARCH c USING|SCAN c\b/.test(r.detail));
    expect(판세기).toHaveLength(1);
  });
});

describe("센티먼트 가중치 표시 (docs/infra.md 25.483)", () => {
  it("이미 퍼센트인 가중치(10)는 10% — 비율용 formatPct 로 적으면 1000% 였다", async () => {
    const { formatPercentNumber, formatPct } = await import("@/lib/stockDetail");
    expect(formatPercentNumber(10)).toBe("10%");
    expect(formatPercentNumber(null)).toBe("-");
    expect(formatPct(10, 0)).toBe("1000%"); // 예전 모양 — 이 함수로는 적지 않는다
    const { readFileSync } = await import("node:fs");
    const 화면 = readFileSync("components/StockDetail.tsx", "utf-8");
    expect(화면).toContain("formatPercentNumber(score.sentiment_weight_used)");
    expect(화면).not.toContain("formatPct(score.sentiment_weight_used");
  });
});

describe("가격 차트의 선 설명 (docs/infra.md 25.483)", () => {
  it("못 읽은 것을 없는 것으로, 보유 중을 보유 안 함으로 말하지 않는다", async () => {
    const { overlayNote } = await import("@/lib/stockDetail");
    const 가격 = (v: unknown) => String(v);
    expect(overlayNote([], "error", null, null, "KRW", 가격)).toContain("읽지 못해");
    expect(overlayNote([], "loading", null, null, "KRW", 가격)).toContain("읽는 중");
    expect(overlayNote([], "ok", "2026-09-25", null, "KRW", 가격)).toBe(
      "겹쳐 그린 선 없음 — 2026-09-25 기준 신호 없음, 보유하지 않은 종목입니다");
    expect(overlayNote([], "ok", "2026-09-25", { quantity: 3 }, "KRW", 가격)).toContain("보유 중이지만");
    expect(overlayNote([{ price: 100, label: "목표", kind: "target" }], "ok", null, null, "KRW", 가격)).toBe("선: 목표 100");
    expect(overlayNote([{ price: 90, label: "내 평균 단가", kind: "cost" }], "error", null, { quantity: 1 }, "KRW", 가격))
      .toContain("읽지 못해");
  });
});

describe("통과 0개인 날의 판정 기준일 (docs/infra.md 25.483)", () => {
  it("ETF: 통과가 없으면 탈락 행의 기준일", async () => {
    const { groupByMarket } = await import("@/lib/etf");
    const 탈락 = { KR: [{ country: "KR", as_of_date: "2026-09-25" }] } as unknown as Parameters<typeof groupByMarket>[2];
    const kr = groupByMarket([], undefined, 탈락).find((m) => m.country === "KR");
    expect(kr?.asOf).toBe("2026-09-25");
  });

  it("장기 적립 종목: 탈락 집계 질의가 기준일을 함께 읽는다", async () => {
    const { buildAccumulationFunnelQuery } = await import("@/lib/accumulation");
    const q = buildAccumulationFunnelQuery("KR");
    const rows = db.prepare(q.sql).all(...q.args) as Array<Record<string, unknown>>;
    expect(Array.isArray(rows)).toBe(true);
    expect(q.sql).toContain("MAX(p.as_of_date) AS as_of");
  });
});

describe("가격 조정 표기와 배당 출처 (docs/infra.md 25.485)", () => {
  it("미국은 분할 반영, 국내는 원자료", async () => {
    const { priceAdjustLabel } = await import("@/lib/stockDetail");
    expect(priceAdjustLabel("US")).toContain("옛 행은 조정 전일 수 있음");
    expect(priceAdjustLabel("KR")).toBe("원자료(수정주가 아님)");
  });

  it("배당 출처는 가장 늦게 받은 행에서", async () => {
    const { dividendSourceLine } = await import("@/lib/stockDetail");
    const 줄 = dividendSourceLine(
      [{ source: "dart_opendart", fetched_at: "2026-04-06T00:10:00+00:00" }, { source: "old", fetched_at: "2025-04-06T00:00:00+00:00" }],
      (t) => t.slice(0, 16),
    );
    expect(줄).toBe("출처 dart_opendart · 받은 시각 2026-04-06T00:10 KST");
    expect(dividendSourceLine([], (t) => t)).toBeNull();
  });
});

describe("ETF 탭의 빈 시장 문장 (docs/infra.md 25.486)", () => {
  it("돌았는데 0건과 안 돌았음을 가른다", async () => {
    const { emptyMarketNote } = await import("@/lib/etf");
    expect(emptyMarketNote("09/28 08:40")).toContain("판정은 돌았는데(09/28 08:40 KST)");
    expect(emptyMarketNote(null)).toContain("돈 적이 없습니다");
  });
});

describe("스크리너 행의 사업연도 (docs/infra.md 25.486)", () => {
  it("미국 행에도 붙는다", async () => {
    const { fiscalYearLabel } = await import("@/lib/screener");
    expect(fiscalYearLabel("KR", 2025)).toBe("2025년");
    expect(fiscalYearLabel("US", 2025)).toBe("2025년 결산");
    expect(fiscalYearLabel("US", null)).toBe("");
    const { readFileSync } = await import("node:fs");
    expect(readFileSync("components/ScreenerForm.tsx", "utf-8")).not.toContain("kr && row.fiscal_year");
  });
});

describe("스크리너 팩터 기준일 찾기 (docs/infra.md 25.491)", () => {
  // 조회 계획 검사는 뺐다 — 통계 없는 SQLite 에서는 옛 MAX() 식도 같은 색인을 타서 되돌려도 통과했다 (25.497, 교차검증).
  // 여기서는 모양만 지킨다. 계획은 엔진·통계에 달려 있어 단위 테스트로 못 잡는다
  it("기준일은 DESC LIMIT 1 로 찾는다 — MAX() 로 되돌리지 않는다", async () => {
    const { buildQuery, defaultFilters } = await import("@/lib/screener");
    const q = buildQuery({ ...defaultFilters("KR") } as never);
    expect(q.sql).toContain("ORDER BY fx.as_of_date DESC LIMIT 1");
    expect(q.sql).not.toMatch(/MAX\(fx\.as_of_date\)/);
  });
});

describe("리포트 발송 모름 표시 (docs/infra.md 25.493)", () => {
  it("보냈는지 모르면 못 보냄이라 단정하지 않는다", async () => {
    const { deliveryLabel } = await import("@/lib/reports");
    expect(deliveryLabel({ sent_at: null, status: "partial", warnings_json: '["텔레그램 응답 시간 초과로 발송 여부를 알 수 없습니다"]' }))
      .toContain("보냈는지 모름");
    expect(deliveryLabel({ sent_at: null, status: "failed", warnings_json: null })).toBe("만들었지만 텔레그램으로 못 보냄");
  });
});
