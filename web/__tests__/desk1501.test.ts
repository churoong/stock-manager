/**
 * 운용 상황실(첫 화면) — 배치 요약 읽기·고르기·글 (docs/desk.md 2장, docs/infra.md 25.1501).
 * 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import {
  DESK_LATEST,
  EMPTY_DESK,
  allChecks,
  countsText,
  fillLine,
  kisOffText,
  kisStatusText,
  orderLine,
  pctText,
  pickDesk,
  ppText,
  type DeskMarket,
} from "@/lib/desk";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

const market = (over: Partial<DeskMarket> = {}): DeskMarket => ({
  run_at: "2026-10-15T23:40:00+00:00", trade_date: "2026-10-15", session_date: "2026-10-16",
  fills_h: [], fills_by_account: {}, orders_h: [], orders_by_account: {}, kis_orders: [], kis_step: {}, checks: [],
  ...over,
});

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
});

describe("요약 고르기", () => {
  it("시장마다 가장 최근 칸 — 아침 국내 배치가 새 줄을 열어도 어젯밤 미국 칸이 보인다", () => {
    const ins = db.prepare("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, ?)");
    ins.run("2026-10-15", JSON.stringify({ leaderboard: { window_start: "x", rows: [] }, markets: { KR: market({ trade_date: "old" }), US: market({ trade_date: "us" }) } }), "a");
    ins.run("2026-10-16", JSON.stringify({ leaderboard: { window_start: "2026-10-16", rows: [] }, mirror_account: "H", markets: { KR: market() } }), "b");
    ins.run("2026-10-14", "{", "c"); // 셋째 줄은 읽지 않는다(LIMIT 2)
    const rows = db.prepare(DESK_LATEST).all() as Array<{ as_of: string; summary_json: string; updated_at: string }>;
    expect(rows.map((r) => r.as_of)).toEqual(["2026-10-16", "2026-10-15"]);
    const v = pickDesk(rows);
    expect(v.as_of).toBe("2026-10-16");
    expect(v.leaderboard?.window_start).toBe("2026-10-16");
    expect(v.markets.KR?.as_of).toBe("2026-10-16");
    expect(v.markets.KR?.trade_date).toBe("2026-10-15");
    expect(v.markets.US?.as_of).toBe("2026-10-15");
    expect(v.markets.US?.trade_date).toBe("us");
  });

  it("빈 표·깨진 줄은 지어내지 않고 빈 보기", () => {
    expect(pickDesk([])).toEqual(EMPTY_DESK);
    expect(pickDesk([{ as_of: "d", summary_json: "{", updated_at: null }])).toEqual(EMPTY_DESK);
    const v = pickDesk([{ as_of: "d", summary_json: "{", updated_at: null }, { as_of: "c", summary_json: JSON.stringify({ markets: {} }), updated_at: "u" }]);
    expect(v.as_of).toBe("c");
  });
});

describe("확인할 일 합치기", () => {
  it("높음이 먼저, 두 시장에 같은 글은 한 번", () => {
    const halt = { level: "high" as const, text: "멈춤 스위치" };
    const c = allChecks({
      KR: { ...market({ checks: [{ level: "normal", text: "경고 줄" }, halt] }), as_of: "d" },
      US: { ...market({ checks: [halt, { level: "high", text: "KIS 단계 실패" }] }), as_of: "d" },
    });
    expect(c.map((x) => x.text)).toEqual(["멈춤 스위치", "KIS 단계 실패", "경고 줄"]);
    expect(c.map((x) => x.market)).toEqual(["KR", "US", "KR"]);
  });
});

describe("글", () => {
  it("수익률·%p·체결·주문·건수", () => {
    expect(pctText(1.234)).toBe("+1.23%");
    expect(pctText(-0.5)).toBe("-0.50%");
    expect(pctText(null)).toBe("-");
    expect(ppText(2.4417)).toBe("+2.44%p");
    expect(fillLine({ side: "buy", name: "삼성전자", ticker: "005930", quantity: 3, price: 9800, currency: "KRW" })).toBe("매수 삼성전자 3주 @ 9,800원");
    expect(fillLine({ side: "sell", name: "Apple", ticker: "AAPL", quantity: 2, price: 180.5, currency: "USD" })).toBe("매도 Apple 2주 @ $180.50");
    const o = { side: "buy", name: "삼성전자", ticker: "005930", quantity: 3, order_type: "limit", limit_price: 9800, tranche: 2, currency: "KRW", reason: "r" };
    expect(orderLine(o)).toBe("매수 삼성전자 3주 · 지정가 9,800원 · 2차");
    expect(orderLine({ ...o, order_type: "market", limit_price: null, tranche: 1 })).toBe("매수 삼성전자 3주 · 시장가");
    expect(countsText({ H: 2, A: 1, B: 0 })).toBe("A 1 · H 2");
    expect(countsText({})).toBe("없음");
  });

  it("KIS 상태 건수·꺼짐 까닭", () => {
    const k = (status: string) => ({ side: "buy", name: "x", quantity: 1, order_type: "limit", limit_price: 1, status, reason: null, message: "" });
    expect(kisStatusText([k("accepted"), k("accepted"), k("rejected")])).toBe("접수 2 · 거부 1");
    expect(kisStatusText([])).toBe("낸 주문 없음");
    expect(kisOffText({ off: "KIS모의투자 키 없음" })).toBe("KIS모의투자 키 없음");
    expect(kisOffText({ sent: { accepted: 1 } })).toBeNull();
  });
});
