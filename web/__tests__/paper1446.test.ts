/**
 * 모의 투자 화면·입금 (docs/paper.md 3·5장, docs/infra.md 25.1446). 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it } from "vitest";
import {
  CANCEL_INSERT,
  DEPOSIT_INSERT,
  FLOW_BY_ID,
  PAPER_CASH,
  PAPER_FILLS,
  PAPER_FLOWS,
  PAPER_HISTORY,
  PAPER_INVESTED,
  PAPER_LATEST_DAILY,
  PAPER_OPEN_BUYS,
  PAPER_OPEN_ORDERS,
  PAPER_STOCK_NAMES,
  accountView,
  cancelBlock,
  depositSchema,
  kstToday,
  reservedByAccount,
  type DailyRow,
} from "@/lib/paper";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  db.exec(
    "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at) VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')",
  );
});

function invested(): number {
  return Number((db.prepare(PAPER_INVESTED).get() as { invested: number }).invested);
}

describe("입금 장부", () => {
  it("같은 요청 키는 한 번만 들어간다 (두 번 누름)", () => {
    const ins = db.prepare(DEPOSIT_INSERT);
    expect(ins.run("2026-10-15", 1_000_000, null, "2026-10-15T00:00:00.000Z", "key-aaaaaaaa").changes).toBe(1);
    expect(ins.run("2026-10-15", 1_000_000, null, "2026-10-15T00:00:01.000Z", "key-aaaaaaaa").changes).toBe(0);
    expect(invested()).toBe(1_000_000);
  });

  it("취소는 음수 행을 더하고 한 번만 된다 — 원래 행은 남는다", () => {
    db.prepare(DEPOSIT_INSERT).run("2026-10-15", 500_000, "첫 입금", "t", "key-aaaaaaaa");
    const can = db.prepare(CANCEL_INSERT);
    expect(can.run("2026-10-16", -500_000, 1, "입금 #1 취소", "t", "key-bbbbbbbb").changes).toBe(1);
    // 다른 키로 같은 입금을 또 취소 — cancels_id 유일이 막는다
    expect(can.run("2026-10-16", -500_000, 1, "입금 #1 취소", "t", "key-cccccccc").changes).toBe(0);
    expect(invested()).toBe(0);
    const f = db.prepare(FLOW_BY_ID).get(1) as { kind: string; cancelled: number };
    expect(f.kind).toBe("deposit");
    expect(Number(f.cancelled)).toBe(1);
    const 목록 = db.prepare(PAPER_FLOWS).all() as Array<{ id: number; cancelled_by: number | null }>;
    expect(목록.find((r) => r.id === 1)?.cancelled_by).toBe(2);
  });

  it("스키마가 엉뚱한 행을 막는다 — 음수 입금, 원래 행 없는 취소", () => {
    expect(() => db.prepare("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at) VALUES ('d', -1, 'deposit', 't')").run()).toThrow();
    expect(() => db.prepare("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at) VALUES ('d', -1, 'cancel', 't')").run()).toThrow();
  });

  it("입금 검증 — 범위·정수·요청 키", () => {
    expect(depositSchema.safeParse({ amount_krw: 9_999, request_key: "key-aaaaaaaa" }).success).toBe(false);
    expect(depositSchema.safeParse({ amount_krw: 10_000.5, request_key: "key-aaaaaaaa" }).success).toBe(false);
    expect(depositSchema.safeParse({ amount_krw: 1_000_000_001, request_key: "key-aaaaaaaa" }).success).toBe(false);
    expect(depositSchema.safeParse({ amount_krw: 10_000 }).success).toBe(false);
    expect(depositSchema.safeParse({ amount_krw: 10_000, request_key: "key-aaaaaaaa" }).success).toBe(true);
  });

  it("입금 날짜는 한국 날짜 — UTC 15시는 다음 날", () => {
    expect(kstToday(new Date("2026-10-15T15:30:00Z"))).toBe("2026-10-16");
  });
});

describe("현금·예약·취소", () => {
  it("장부 현금 = 입금 합 + 체결 현금 영향 (배치와 같은 식)", () => {
    db.prepare(DEPOSIT_INSERT).run("2026-10-15", 1_000_000, null, "t", "key-aaaaaaaa");
    db.exec(
      "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency, decided_on, fill_session, tranche, reason, rule_version, status, created_at)" +
        " VALUES (1, 'A', 'KR', 1, 'buy', 'limit', 10000, 4, 'KRW', '2026-10-15', '2026-10-16', 1, '2부 배분', 1, 'filled', 't')," +
        " (2, 'A', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-10-16', '2026-10-17', 2, '2부 배분', 1, 'open', 't')",
    );
    db.exec(
      "INSERT INTO paper_fills (order_id, account_id, stock_id, side, fill_date, price, quantity, currency, fx_rate, fee, tax, slippage, cash_krw, created_at)" +
        " VALUES (1, 'A', 1, 'buy', '2026-10-16', 10000, 4, 'KRW', 1, 6, 0, 0, -40006, 't')",
    );
    const 현금 = Object.fromEntries((db.prepare(PAPER_CASH).all() as Array<{ account_id: string; cash_krw: number }>).map((r) => [r.account_id, r.cash_krw]));
    expect(현금).toEqual({ A: 959_994, B: 1_000_000 });
    const 예약 = reservedByAccount(db.prepare(PAPER_OPEN_BUYS).all() as never, null);
    expect(예약).toEqual({ A: Math.round(9800 * 3 * 1.01) });
    // 일지·미체결 질의가 실제 스키마에서 돈다
    expect((db.prepare(PAPER_FILLS).all() as unknown[]).length).toBe(1);
    expect((db.prepare(PAPER_OPEN_ORDERS).all() as unknown[]).length).toBe(1);
    expect((db.prepare(PAPER_STOCK_NAMES).all(JSON.stringify([1])) as Array<{ name: string }>)[0].name).toBe("삼성전자");
  });

  it("달러 예약은 환율 여유까지, 환율을 모르면 null — 모르는 것을 0 으로 치지 않는다", () => {
    const buys = [{ account_id: "A", limit_price: 100, quantity: 4, currency: "USD" }];
    expect(reservedByAccount(buys, 1_400)).toEqual({ A: Math.round(400 * 1.01 * 1_400 * 1.03) });
    expect(reservedByAccount(buys, null)).toEqual({ A: null });
  });

  it("취소 규칙 — 배치 cancellable 과 같다", () => {
    const 입금 = { amount_krw: 500_000, kind: "deposit", cancelled: 0 };
    expect(cancelBlock(undefined, {})).toBe("없는 입금입니다");
    expect(cancelBlock({ ...입금, kind: "cancel" }, {})).toBe("취소 행은 다시 취소할 수 없습니다");
    expect(cancelBlock({ ...입금, cancelled: 1 }, {})).toBe("이미 취소한 입금입니다");
    expect(cancelBlock(입금, { A: 600_000, B: 400_000 })).toContain("계좌 B");
    expect(cancelBlock(입금, { A: 600_000, B: null })).toContain("계좌 B");
    expect(cancelBlock(입금, { A: 500_000, B: 500_000 })).toBeNull();
  });
});

describe("계좌 요약", () => {
  const 평가: DailyRow = {
    account_id: "A", as_of: "2026-10-16", invested_krw: 1_000_000, cash_krw: 959_994, holdings_krw: 44_000, equity_krw: 1_003_994,
    pnl_krw: 3_994, twr_index: 1.004, kospi_krw: 1_010_000, sp500_krw: null, recon_ok: 1, recon_note: "맞음", updated_at: "t",
  };

  it("총 입금액·현금은 지금 장부, 보유는 마지막 평가 — 평가 뒤 입금은 까닭을 적는다", () => {
    const v = accountView("A", 1_500_000, 1_459_994, 0, 평가);
    expect(v.equity).toBe(1_459_994 + 44_000);
    expect(v.pnl).toBe(1_459_994 + 44_000 - 1_500_000);
    expect(v.notes[0]).toContain("+500,000원");
    expect(v.kospiPct).toBeCloseTo(1.0);
    expect(v.sp500Pct).toBeNull();
    expect(v.twrPct).toBeCloseTo(0.4);
  });

  it("검산이 어긋나면 붉은 줄", () => {
    const v = accountView("A", 1_000_000, 959_994, 0, { ...평가, recon_ok: 0, recon_note: "장부 현금 ≠" });
    expect(v.reconOk).toBe(false);
    expect(v.notes.some((n) => n.startsWith("⚠️ 장부 검산 어긋남"))).toBe(true);
  });

  it("평가 전이면 현금만 — 보유·손익을 지어내지 않는다", () => {
    const v = accountView("A", 1_000_000, 1_000_000, 0, undefined);
    expect(v.holdings).toBeNull();
    expect(v.pnl).toBeNull();
    expect(v.notes[0]).toContain("다음 아침 리포트");
  });

  it("최신 평가·기록 질의가 실제 스키마에서 돈다", () => {
    db.exec(
      "INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw, twr_index, positions_json, recon_ok, updated_at)" +
        " VALUES ('A', '2026-10-15', 1, 1, 0, 1, 0, 1, '[]', 1, 't'), ('A', '2026-10-16', 1, 1, 0, 1, 0, 1, '[]', 1, 't')",
    );
    expect((db.prepare(PAPER_LATEST_DAILY).all() as Array<{ as_of: string }>).map((r) => r.as_of)).toEqual(["2026-10-16"]);
    expect((db.prepare(PAPER_HISTORY).all("2026-10-01") as unknown[]).length).toBe(2);
  });
});

describe("실제 투자와 비교 (25.1448)", () => {
  it("같은 기간으로 — 배치 compare_real 과 같은 결과", async () => {
    const { compareReal, windowChange, REAL_TWR, PAPER_TWR } = await import("@/lib/paper");
    const c = compareReal(
      { A: [["2026-10-15", 1.0], ["2026-10-21", 1.05]], B: [["2026-10-15", 1.0], ["2026-10-21", 1.02]] },
      [["2026-09-01", 1.0], ["2026-10-14", 1.1], ["2026-10-20", 1.21]],
    );
    expect(c?.start).toBe("2026-10-14");
    expect(c?.end).toBe("2026-10-20");
    expect(c?.realPct).toBeCloseTo(10);
    expect(c?.paperPct.A).toBeCloseTo(0);
    expect(compareReal({}, [["2026-10-01", 1]])).toBeNull();
    expect(windowChange([["2026-10-01", 1], ["2026-10-05", 1.2]], "2026-10-02", "2026-09-30")).toBeNull();
    // 질의가 실제 스키마에서 돈다
    expect(db.prepare(REAL_TWR).all("2026-10-01")).toEqual([]);
    expect(db.prepare(PAPER_TWR).all()).toEqual([]);
  });
});

describe("성과 분해 칸 (25.1450)", () => {
  it("표본이 적으면 참고, 없으면 표본 없음", async () => {
    const { attrCellText } = await import("@/lib/paper");
    expect(attrCellText({ n: 40, avg: 1.234, hit: 55 })).toBe("+1.2%p (n=40, 이긴 비율 55%)");
    expect(attrCellText({ n: 3, avg: -2, hit: 0 })).toBe("-2.0%p (n=3, 이긴 비율 0%, 참고)");
    expect(attrCellText({ n: 2, avg: 0.5 }, "%")).toBe("+0.50% (n=2, 참고)");
    expect(attrCellText({ n: 0, avg: null })).toBe("표본 없음");
    expect(attrCellText(undefined)).toBe("표본 없음");
  });
});

describe("멈춤 스위치 (25.1451)", () => {
  it("설정 키에 쓰고 배치와 같은 규칙으로 읽는다", async () => {
    const { HALT_UPSERT, HALT_READ, haltOn, haltSchema } = await import("@/lib/paper");
    db.prepare(HALT_UPSERT).run(JSON.stringify({ on: true, at: "t", why: "" }), "t");
    expect(haltOn((db.prepare(HALT_READ).get() as { value: string }).value)).toBe(true);
    db.prepare(HALT_UPSERT).run(JSON.stringify({ on: false }), "t2");
    expect(haltOn((db.prepare(HALT_READ).get() as { value: string }).value)).toBe(false);
    expect(haltOn(null)).toBe(false);
    expect(haltOn("깨진 값")).toBe(false);
    expect(haltSchema.safeParse({ on: "yes" }).success).toBe(false);
  });
});
