/** 웹의 보유 수량을 배치와 같은 규칙으로 (docs/infra.md 25.793, 매매 입력 감사 #4) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { TRADES_FOR_HELD, deleteShortfallWarnings, heldOn, laterShortfall } from "@/lib/portfolio";

const 행 = (id: number, side: string, trade_date: string, quantity: number) => ({ id, side, trade_date, quantity });

describe("보유 수량 — 배치와 같게", () => {
  it("짝 없는 매도가 있어도 화면 [보유] 와 같은 수량을 판다", () => {
    // 9/10 매도 10주(매수는 지웠다), 9/20 매수 10주 — 배치 보유 10주. 예전 단순 합은 0주라 9/25 매도를 거절했다
    const 기록 = [행(1, "sell", "2026-09-10", 10), 행(2, "buy", "2026-09-20", 10)];
    expect(heldOn(기록, "2026-09-25")).toBe(10);
    expect(laterShortfall(기록, { trade_date: "2026-09-25", quantity: 10 })).toBeNull();
  });

  it("같은 날은 매수 먼저, 그날까지만 센다", () => {
    const 기록 = [행(2, "buy", "2026-09-20", 10), 행(1, "sell", "2026-09-20", 4), 행(3, "buy", "2026-09-26", 5)];
    expect(heldOn(기록, "2026-09-20")).toBe(6);
    expect(heldOn(기록, "2026-09-25")).toBe(6);
  });

  it("과거로 끼워 넣은 매도가 뒤 매도를 새로 모자라게 하면 알린다 — 이미 짝 없던 매도는 탓하지 않는다", () => {
    const 기록 = [행(1, "buy", "2026-09-01", 10), 행(2, "sell", "2026-09-20", 8), 행(3, "sell", "2026-09-22", 5)];
    // 3번은 이미 3주 모자라다(10 − 8 = 2 < 5). 9/10 에 2주를 끼우면 3번이 **2주 더** 모자라진다 — 늘어난 만큼만 말한다
    expect(laterShortfall(기록, { trade_date: "2026-09-10", quantity: 2 })).toEqual({ d: "2026-09-22", short: 2 });
    expect(laterShortfall([행(1, "buy", "2026-09-01", 10), 행(2, "sell", "2026-09-20", 5)], { trade_date: "2026-09-10", quantity: 5 })).toBeNull();
  });

  it("이미 짝 없던 매도는 매수를 지울 때 탓하지 않는다 (25.795, 교차검증)", () => {
    // 9/10 매도 10주(매수는 이미 지움), 9/20 매수 10주, 9/21 매수 5주 — 9/21 매수를 지워도 9/10 매도는 원래 짝이 없었다
    const 기록 = [행(1, "sell", "2026-09-10", 10), 행(2, "buy", "2026-09-20", 10), 행(3, "buy", "2026-09-21", 5)];
    expect(deleteShortfallWarnings(기록, 3)).toEqual([]);
    // 뒤 매도가 있으면 새로 모자라는 만큼 말한다
    const 뒤 = [...기록, 행(4, "sell", "2026-09-25", 12)];
    expect(deleteShortfallWarnings(뒤, 3)[0]).toContain("2026-09-25 매도가 보유보다 2주 많아집니다");
  });

  it("질의가 배치 순서대로 준다", () => {
    const db = new DatabaseSync(":memory:");
    for (const f of readdirSync(join(process.cwd(), "..", "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
      db.exec(readFileSync(join(process.cwd(), "..", "migrations", f), "utf-8"));
    }
    db.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't');
      INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at) VALUES
        (5, 1, 'sell', '2026-09-20', 100, 4, 'KRW', 1, 'none', 't', 't'),
        (9, 1, 'buy', '2026-09-20', 100, 10, 'KRW', 1, 'none', 't', 't'),
        (2, 1, 'buy', '2026-09-01', 100, 1, 'KRW', 1, 'none', 't', 't');
    `);
    const rows = db.prepare(TRADES_FOR_HELD).all(1) as Array<{ id: number }>;
    expect(rows.map((r) => r.id)).toEqual([2, 9, 5]);
  });

  it("매매 저장 경로가 이 규칙을 쓴다", () => {
    const 글 = readFileSync("app/api/trades/route.ts", "utf-8");
    expect(글).toContain("heldOn(기록, input.trade_date)");
    expect(글).toContain("laterShortfall(기록,");
  });
});
