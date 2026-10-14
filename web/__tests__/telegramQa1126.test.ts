/** 텔레그램 질의응답 감사 (docs/infra.md 25.1126) — 환차 섞인 손익률, 폐지 보유 못 찾음, 전체 읽기, /보유 머리, 신호 판, @봇이름 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import type { ResultSet, SqlValue } from "@/lib/db";
import { answer, parseQuery, type Exec } from "@/lib/telegramQa";

const ROOT = join(process.cwd(), "..");

function memExec(): { db: DatabaseSync; exec: Exec; sqls: string[] } {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  const sqls: string[] = [];
  const exec: Exec = async (sql: string, args: SqlValue[] = []) => {
    sqls.push(sql);
    const st = db.prepare(sql);
    const rows = st.all(...(args as Array<string | number | null>)) as Array<Record<string, unknown>>;
    const columns = st.columns().map((c) => c.name);
    return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 } satisfies ResultSet;
  };
  return { db, exec, sqls };
}

const 종목 = `INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at) VALUES`;

describe("해외 보유 손익은 주가와 환차를 나눈다", () => {
  it("주가 −10% 가 환율 덕에 +로 읽히지 않는다", async () => {
    const { db, exec } = memExec();
    db.exec(`${종목} (1, 'AAPL', 'NASDAQ', 'US', NULL, 'Apple Inc', 'USD', 'active', 't', 't')`);
    // 평균 200·종가 180 ×10주, 환율 1300 → 1500: 원가 2,600,000원, 평가 2,700,000원
    db.exec(`INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,
             unrealized_pnl, unrealized_pnl_krw, unrealized_price_pnl_krw, unrealized_fx_pnl_krw, price_date, updated_at)
             VALUES (1, 10, 'USD', 200, 1300, 2000, 2600000, '2026-01-02', -200, 100000, -300000, 400000, '2026-10-09', 't')`);
    const text = await answer({ kind: "stock", q: "AAPL" }, exec);
    expect(text).toContain("평균 $200");
    expect(text).toContain("주가 -10.0%");
    expect(text).toContain("환차 +400,000원");
    expect(text).toContain("원화 합계 +3.8%");
    const 보유 = await answer({ kind: "holdings" }, exec);
    expect(보유).toContain("Apple Inc +3.8% (주가 -10.0%)");
  });
});

describe("찾기", () => {
  it("폐지된 보유 종목도 이름·티커로 찾는다", async () => {
    const { db, exec } = memExec();
    db.exec(`${종목} (1, '123450', 'KOSDAQ', 'KR', '정리매매중', NULL, 'KRW', 'delisted', 't', 't'),
                    (2, '123460', 'KOSDAQ', 'KR', '폐지만됨', NULL, 'KRW', 'delisted', 't', 't')`);
    db.exec(`INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
             VALUES (1, 'buy', '2026-01-02', 1000, 10, 'KRW', 1, 'none', 't', 't')`);
    expect(await answer({ kind: "stock", q: "정리매매중" }, exec)).toContain("정리매매중 (123450)");
    expect(await answer({ kind: "stock", q: "123450" }, exec)).toContain("정리매매중 (123450)");
    // 거래한 적 없는 폐지 종목은 예전처럼 찾지 않는다(앱 검색과 같은 목록)
    expect(await answer({ kind: "stock", q: "폐지만됨" }, exec)).toContain("맞는 종목이 없습니다");
  });

  it("종목 표 전체를 질문마다 읽지 않는다 — 목록은 한 번", async () => {
    const { db, exec, sqls } = memExec();
    db.exec(`${종목} (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't')`);
    await answer({ kind: "stock", q: "삼성전자" }, exec);
    await answer({ kind: "stock", q: "005930" }, exec);
    expect(sqls.filter((q) => q.includes("FROM stocks WHERE status = 'active' OR id IN")).length).toBe(1);
    expect(sqls.some((q) => /UPPER\(ticker\)/.test(q))).toBe(false);
  });

  it("명령 뒤에 붙은 봇 이름을 지운다", () => {
    expect(parseQuery("/종목@MyBot AAPL")).toEqual({ kind: "stock", q: "AAPL" });
    expect(parseQuery("/stock@my_bot aapl")).toEqual({ kind: "stock", q: "aapl" });
    expect(parseQuery("/보유@mybot")).toEqual({ kind: "holdings" });
  });
});

describe("/보유 머리 줄", () => {
  it("전체 수와 평가 기준일을 말한다", async () => {
    const { db, exec } = memExec();
    for (let i = 1; i <= 25; i += 1) {
      db.exec(`${종목} (${i}, 'T${i}', 'KOSPI', 'KR', '종목${i}', NULL, 'KRW', 'active', 't', 't')`);
      db.exec(`INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,
               unrealized_pnl, unrealized_pnl_krw, price_date, updated_at)
               VALUES (${i}, 1, 'KRW', 100, 1, 100, 100, '2026-01-02', ${i}, ${i}, '${i === 1 ? "2026-10-08" : "2026-10-09"}', 't')`);
    }
    const text = await answer({ kind: "holdings" }, exec);
    expect(text.split("\n")[0]).toBe("보유 25종목 중 손익률 상위 20 · 평가 2026-10-08~2026-10-09 종가");
    expect(text.split("\n").length).toBe(21);
  });
});

describe("신호는 그날 가장 새 판만", () => {
  it("새 판이 걸러 낸 종목의 옛 판 신호를 보이지 않는다", async () => {
    const { db, exec } = memExec();
    db.exec(`${종목} (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't'),
                    (2, '000660', 'KOSPI', 'KR', 'SK하이닉스', NULL, 'KRW', 'active', 't', 't')`);
    const 신호 = `INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,
      tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at) VALUES`;
    db.exec(`${신호} (1, '2026-10-08', 'long', 'buy', 50000, 52000, 'KRW', '[]', 1, 't', '{}', 1, 't'),
                     (1, '2026-10-09', 'long', 'buy', 60000, 62000, 'KRW', '[]', 1, 't', '{}', 1, 't'),
                     (2, '2026-10-09', 'long', 'buy', 1, 2, 'KRW', '[]', 1, 't', '{}', 2, 't'),
                     (1, '2026-10-08', 'mid', 'buy', 51000, 53000, 'KRW', '[]', 1, 't', '{}', 1, 't')`);
    const text = await answer({ kind: "stock", q: "삼성전자" }, exec);
    // 10-09 는 판 2 가 삼성전자를 걸러 냈다 → 판 1 행(60,000~)은 나가지 않고, 판이 맞는 10-08 신호를 보인다
    expect(text).not.toContain("60,000");
    expect(text).toContain("매수 신호 장기 (2026-10-08): 구간 50,000원~52,000원");
    expect(text).toContain("매수 신호 중기 (2026-10-08)");
  });
});
