/** 저장한 조건과 CSV (docs/screener.md 2·3장). 질의는 실제 스키마에 돌린다. */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import {
  CSV_COLUMNS, PRESET_DELETE, PRESET_LIST, PRESET_TOUCH, PRESET_UPSERT, csvFileName, defaultFilters, parsePreset,
  presetInputSchema, toCsv, type PresetRow, type ScreenerRow,
} from "@/lib/screener";

function db(): DatabaseSync {
  const conn = new DatabaseSync(":memory:");
  const dir = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(dir).filter((x) => x.endsWith(".sql")).sort()) conn.exec(readFileSync(join(dir, f), "utf-8"));
  return conn;
}

describe("저장한 조건", () => {
  it("넣고, 같은 이름은 덮어쓰고, 나라별로 읽고, 지운다", () => {
    const conn = db();
    const f = { ...defaultFilters("KR"), per: { min: null, max: 12 } };
    conn.prepare(PRESET_UPSERT).run("싼 것", "KR", JSON.stringify(f), "t1");
    conn.prepare(PRESET_UPSERT).run("싼 것", "KR", JSON.stringify({ ...f, per: { min: null, max: 8 } }), "t2");
    conn.prepare(PRESET_UPSERT).run("미국", "US", JSON.stringify(defaultFilters("US")), "t3");
    const kr = conn.prepare(PRESET_LIST).all("KR") as unknown as PresetRow[];
    expect(kr).toHaveLength(1);
    expect(parsePreset(kr[0])?.per.max).toBe(8);
    conn.prepare(PRESET_TOUCH).run("t9", kr[0].id);
    expect((conn.prepare(PRESET_LIST).get("KR") as unknown as PresetRow).last_used_at).toBe("t9");
    conn.prepare(PRESET_DELETE).run(kr[0].id);
    expect(conn.prepare(PRESET_LIST).all("KR")).toHaveLength(0);
  });

  it("깨졌거나 옛 저장분은 기본값으로 메우거나 null", () => {
    const base: PresetRow = { id: 1, name: "x", country: "KR", filters_json: "{", created_at: "t", last_used_at: null };
    expect(parsePreset(base)).toBeNull();
    // 항목이 모자란 옛 저장분은 기본값으로 메워 쓴다
    const old = { ...base, filters_json: JSON.stringify({ per: { min: null, max: 5 } }) };
    expect(parsePreset(old)?.per.max).toBe(5);
    expect(parsePreset(old)?.country).toBe("KR");
  });

  it("이름은 1~30자", () => {
    expect(presetInputSchema.safeParse({ name: " ", filters: defaultFilters("KR") }).success).toBe(false);
    expect(presetInputSchema.safeParse({ name: "a".repeat(31), filters: defaultFilters("KR") }).success).toBe(false);
    expect(presetInputSchema.safeParse({ name: " 좋은 것 ", filters: defaultFilters("KR") }).success).toBe(true);
  });
});

describe("CSV", () => {
  const row: ScreenerRow = {
    stock_id: 1, ticker: "005930", name: "삼성전자, 보통주", market: "KOSPI", sector: null, market_cap: 1e14,
    avg_turnover_20d: null, close: 70000, price_date: "2026-09-16", fiscal_year: 2025, per: 12.3, pbr: 1.2, roe: 10,
    debt_ratio: 30, operating_margin: 15, revenue_growth: 8, operating_income_growth: null, cagr: 0.123, mdd: -0.3,
    sharpe: 0.8, volatility_ann: 0.25, data_points: 1200, sentiment: 45, sentiment_date: "2026-09-16",
  };
  it("BOM 과 머리글, 쉼표가 든 값은 따옴표, 빈 값은 빈 칸", () => {
    const csv = toCsv([row]);
    expect(csv.startsWith("﻿")).toBe(true);
    const lines = csv.trim().split("\n");
    expect(lines[0].replace("﻿", "")).toBe(CSV_COLUMNS.map(([, l]) => l).join(","));
    expect(lines[1]).toContain('"삼성전자, 보통주"');
    expect(lines[1].split(",").length).toBe(CSV_COLUMNS.length + 1); // 따옴표 안의 쉼표 하나
    expect(lines[1].endsWith("45,2026-09-16")).toBe(true); // 감성과 기준일이 마지막 두 열
    expect(lines[1]).toContain(",,"); // sector null
    // SQL 이 주는 비율(0.123)을 열 이름 "(%)" 에 맞춰 12.3 으로 — 화면과 같다 (25.577, 감사: 100배 작게 나갔다)
    expect(lines[1]).toContain(",12.3,-30,0.8,25,");
  });
  it("파일 이름에 나라와 날짜", () => {
    expect(csvFileName("KR", new Date(Date.UTC(2026, 8, 17)))).toBe("screener-KR-2026-09-17.csv");
    // 09-28 08:00 KST = 09-27 23:00 UTC — 사용자 날짜(09-28)가 붙는다 (docs/infra.md 25.483)
    expect(csvFileName("KR", new Date(Date.UTC(2026, 8, 27, 23)))).toBe("screener-KR-2026-09-28.csv");
  });
});

describe("조회 질의 (docs/infra.md 25.577, 감사)", () => {
  it("묵은 감성은 쓰지 않고, 정렬에 동점 기준(종목 번호)이 있다", async () => {
    const { buildQuery, defaultFilters } = await import("@/lib/screener");
    const { sql } = buildQuery(defaultFilters("KR"));
    expect(sql).toContain("ss.as_of_date >= date(COALESCE((SELECT d FROM factors_asof), date('now')), '-3 days')");
    expect(sql).toMatch(/NULLS LAST, stock_id\s+LIMIT \?/);
  });
  it("늦게 온 조회 응답은 버린다", async () => {
    const { readFileSync } = await import("node:fs");
    const 화면 = readFileSync("components/ScreenerForm.tsx", "utf8");
    expect(화면).toContain("if (번호 !== 조회번호.current) return;");
  });
  it("저장 조건 목록도 늦은 응답은 버리고, 나라를 바꾸면 비우고, 다른 나라 조건은 돌리지 않는다 (25.579)", async () => {
    const { readFileSync } = await import("node:fs");
    const 화면 = readFileSync("components/ScreenerForm.tsx", "utf8");
    expect(화면).toContain("if (번호 !== 목록번호.current) return;");
    expect(화면).toMatch(/setRows\(\[\]\);[\s\S]{0,200}setSaved\(\[\]\);[\s\S]{0,80}void search\(fresh\)/);
    expect(화면).toContain("if (p.filters.country !== country) {");
    expect(화면, "읽기 실패는 접힌 칸 밖에 보여야 한다").toContain("{presetAlert ? (");
    // 저장·삭제 뒤 다시 읽을 때는 지금 보이는 나라 (25.582) — 요청을 보낼 때의 나라면 탭을 바꾼 사이 다른 나라 목록이 실렸다
    expect(화면).toContain("void loadPresets(지금나라.current);");
    expect(화면).not.toContain("void loadPresets(country);");
    // 버튼(접힌 칸 밖)에서 난 알림은 밖에 보인다
    expect(화면).toContain('setPresetAlert(`"${p.name}" 은 다른 나라의 조건입니다`);');
  });
});
