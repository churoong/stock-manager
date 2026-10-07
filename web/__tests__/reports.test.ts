/**
 * 일일 리포트 이력 (docs/reports.md). 질의가 실제 마이그레이션 스키마에 맞는지, 표시 논리가 맞는지 고정한다.
 */
import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  LATEST_REPORT, REPORT_BY_DATE, REPORT_HISTORY, REPORT_ITEMS, missedReports, deliveryLabel, parseItems, parseWarnings,
  reportQuerySchema,
} from "@/lib/reports";

function db(): DatabaseSync {
  const conn = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    conn.exec(readFileSync(join(dir, file), "utf-8"));
  }
  return conn;
}

describe("질의", () => {
  it("실제 스키마에서 돌아가고 최근·날짜·이력·항목을 읽는다", () => {
    const conn = db();
    conn.exec(
      "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, name_ko)" +
        " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '종목1')",
    );
    conn.exec(
      "INSERT INTO daily_reports (id, market, trade_date, status, generated_at, sent_at, summary_text, warnings_json)" +
        " VALUES (1, 'KR', '2026-09-15', 'success', '2026-09-16T08:30', '2026-09-16T08:31', '첫째', '[]')," +
        " (2, 'KR', '2026-09-16', 'partial', '2026-09-17T08:30', NULL, '둘째', '[\"환율 없음\"]')," +
        " (3, 'US', '2026-09-16', 'success', '2026-09-17T22:30', '2026-09-17T22:31', '미국', '[]')",
    );
    conn.exec(
      "INSERT INTO report_items (report_id, section, stock_id, rank, payload_json, rationale_text)" +
        " VALUES (2, 'recommend', 1, 1, '{\"ticker\":\"000001\",\"horizon\":\"mid\",\"total_score\":70}', '매출 18.2%')," +
        " (2, 'notice', NULL, 1, '{\"kind\":\"warning\",\"text\":\"환율 없음\"}', NULL)",
    );
    const latest = conn.prepare(LATEST_REPORT).get("KR") as { id: number; summary_text: string };
    expect(latest.id).toBe(2);
    expect(latest.summary_text).toBe("둘째");
    const byDate = conn.prepare(REPORT_BY_DATE).get("KR", "2026-09-15") as { id: number };
    expect(byDate.id).toBe(1);
    const history = conn.prepare(REPORT_HISTORY).all("KR") as Array<{ trade_date: string }>;
    expect(history.map((h) => h.trade_date)).toEqual(["2026-09-16", "2026-09-15"]);
    const items = parseItems(conn.prepare(REPORT_ITEMS).all(2) as never);
    expect(items).toHaveLength(2);
    const rec = items.find((i) => i.section === "recommend")!;
    expect(rec.name).toBe("종목1");
    expect(rec.payload.total_score).toBe(70);
  });

  it("날짜는 YYYY-MM-DD 만 받는다", () => {
    expect(reportQuerySchema.safeParse({ market: "KR", date: "2026-9-1" }).success).toBe(false);
    expect(reportQuerySchema.parse({}).market).toBe("KR");
  });
});

describe("표시", () => {
  it("못 보낸 리포트는 그렇다고 말한다", () => {
    expect(deliveryLabel({ sent_at: null, status: "success" })).toContain("못 보냄");
    expect(deliveryLabel({ sent_at: "x", status: "partial" })).toBe("보냄 (경고 있음)");
    // 일부만 나간 것은 따로 말한다 (docs/infra.md 25.427)
    expect(
      deliveryLabel({ sent_at: "x", status: "partial", warnings_json: '["텔레그램 리포트 3조각 가운데 1조각만 보냈습니다"]' }),
    ).toBe("일부만 보냄 (나머지는 이 화면에)");
  });

  describe("빠진 리포트는 달력 날짜가 아니라 정규장으로 센다 (25.235)", () => {
    // 국내 정규장 09:00 KST = 00:00 UTC
    const kr = (d: string) => ({ date: d, open_utc: `${d}T00:00:00Z` });

    it("월요일 아침에 막 나온 리포트(거래일 금요일)는 빠진 것이 없다", () => {
      // 예전 `ageDays` 는 여기서 3 을 돌려줘 "3일 지난 리포트" 를 띄웠다
      const 뒤 = [kr("2026-10-19"), kr("2026-10-20")];
      expect(missedReports(뒤, new Date("2026-10-18T23:40:00Z"))).toBe(0); // 월 08:40 KST
      expect(missedReports(뒤, new Date("2026-10-19T06:00:00Z"))).toBe(0); // 월 15:00 KST
    });

    it("다음 정규장이 열렸는데 새 리포트가 없으면 하나 빠졌다", () => {
      const 뒤 = [kr("2026-10-19"), kr("2026-10-20")];
      expect(missedReports(뒤, new Date("2026-10-20T00:30:00Z"))).toBe(1);
    });

    it("미국 리포트는 다음날 UTC 자정을 넘어도 빠진 것이 아니다", () => {
      // 화요일 리포트(거래일 월) — 수요일 00:00 UTC 에 예전 계산은 2일이 됐다
      const us = [
        { date: "2026-10-20", open_utc: "2026-10-20T13:30:00Z" },
        { date: "2026-10-21", open_utc: "2026-10-21T13:30:00Z" },
      ];
      expect(missedReports(us, new Date("2026-10-21T01:00:00Z"))).toBe(0);
      expect(missedReports(us, new Date("2026-10-21T14:00:00Z"))).toBe(1);
    });

    it("달력을 모르면 모른다고 한다", () => {
      expect(missedReports([], new Date())).toBeNull();
    });
  });

  it("깨진 JSON 은 빈 값으로", () => {
    expect(parseWarnings("{")).toEqual([]);
    expect(parseItems([{ id: 1, section: "notice", stock_id: null, rank: 1, payload_json: "{", rationale_text: null, ticker: null, name: null }])[0].payload).toEqual({});
  });
});
