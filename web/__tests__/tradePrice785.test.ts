/**
 * 체결가 크기 검사 (docs/infra.md 25.785, 매매 입력 감사 #2).
 * AAPL 체결가 칸에 원화 350,000 을 넣으면 그대로 저장돼 거래일마다 거짓 손절 알림이 나갔다.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { TRADE_PRICE_BASIS, tradePriceCheck } from "@/lib/portfolio";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

describe("체결가 크기 검사", () => {
  const 그날 = { date: "2026-09-29", close: 230, high: 233, low: 227 };

  it("통화를 바꿔 넣은 값(100배 넘게)은 막는다", () => {
    expect(tradePriceCheck(350_000, 그날, "2026-09-29", "USD").error).toContain("달러 단위");
    // 원화 종목에 달러로 — 1/100 아래
    expect(tradePriceCheck(45, { date: "2026-09-29", close: 60_000, high: 61_000, low: 59_000 }, "2026-09-29", "KRW").error)
      .toContain("원 단위");
  });

  it("분할 비율 안쪽(최대 50:1)은 막지 않고 경고만 — 정당한 분할 전 매매를 못 넣게 하지 않는다", () => {
    const r = tradePriceCheck(230 * 20, 그날, "2026-09-29", "USD");
    expect(r.error).toBeNull();
    expect(r.warning).toContain("그날 가격 범위");
  });

  it("체결금액을 단가 칸에 넣은 10배는 저장하되 경고한다", () => {
    const r = tradePriceCheck(2_300, 그날, "2026-09-29", "USD");
    expect(r.error).toBeNull();
    expect(r.warning).toContain("체결금액");
  });

  it("그날 범위 ±10%(시간외 단일가) 안은 조용하다", () => {
    expect(tradePriceCheck(250, 그날, "2026-09-29", "USD")).toEqual({ error: null, warning: null });
    expect(tradePriceCheck(205, 그날, "2026-09-29", "USD")).toEqual({ error: null, warning: null });
  });

  it("그날 시세가 없으면 앞 종가의 절반~두 배 밖을 경고한다", () => {
    const 앞날 = { date: "2026-09-25", close: 230, high: null, low: null };
    expect(tradePriceCheck(240, 앞날, "2026-09-29", "USD").warning).toBeNull();
    expect(tradePriceCheck(500, 앞날, "2026-09-29", "USD").warning).toContain("절반~두 배");
  });

  it("시세를 모르면 보지 않는다", () => {
    expect(tradePriceCheck(350_000, undefined, "2026-09-29", "USD")).toEqual({ error: null, warning: null });
  });
});

describe("체결가 기준 시세 질의", () => {
  const db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
    VALUES (1, 'AAPL', 'NASDAQ', 'US', NULL, 'Apple', 'USD', 'active', 't', 't');
    INSERT INTO prices (stock_id, date, close, high, low, currency, source, fetched_at)
    VALUES (1, '2026-09-10', 200, 202, 198, 'USD', 't', 't'), (1, '2026-09-25', 230, 233, 227, 'USD', 't', 't'),
           (1, '2026-09-30', 240, 241, 238, 'USD', 't', 't');
  `);
  const 기준 = (d: string) => db.prepare(TRADE_PRICE_BASIS).get(1, d, d) as { date: string } | undefined;

  it("체결일 이전 10일 안의 마지막 시세를 고른다 — 뒤 날은 보지 않는다", () => {
    expect(기준("2026-09-29")?.date).toBe("2026-09-25");
    expect(기준("2026-09-30")?.date).toBe("2026-09-30");
  });

  it("10일보다 오래된 시세로는 재지 않는다", () => {
    expect(기준("2026-09-24")).toBeUndefined();
  });

  it("매매 저장 경로가 검사를 부른다", () => {
    const 글 = readFileSync("app/api/trades/route.ts", "utf-8");
    expect(글).toContain("tradePriceCheck(input.price, 시세, input.trade_date, stock.currency)");
    expect(글).toContain("가격검사.warning");
  });
});

describe("저장 재시도 중복 (25.785, 매매 입력 감사 #3)", () => {
  it("재계산 요청은 시간 제한을 건다 — 늘어지면 화면이 실패로 보여 다시 누르게 된다", async () => {
    const { requestRecalc, RECALC_TIMEOUT_MS } = await import("@/lib/portfolio");
    const { vi } = await import("vitest");
    vi.stubEnv("GH_DISPATCH_TOKEN", "x");
    vi.stubEnv("GH_REPO", "owner/repo");
    const fake = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await requestRecalc(fake as unknown as typeof fetch);
    const init = fake.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(RECALC_TIMEOUT_MS).toBeLessThanOrEqual(5_000);
    // 제한에 걸린 호출(TimeoutError)은 실패로 돌아온다 — 기록은 이미 들어갔고 다음 배치가 반영한다
    const 멈춤 = vi.fn().mockRejectedValue(new DOMException("signal timed out", "TimeoutError"));
    const r = await requestRecalc(멈춤 as unknown as typeof fetch);
    vi.unstubAllEnvs();
    expect(r.dispatched).toBe(false);
  });

  it("방금 같은 매매를 찾는 질의 — 10분 안, 다섯 칸이 모두 같을 때만", async () => {
    const { RECENT_SAME_TRADE } = await import("@/lib/portfolio");
    const d = new DatabaseSync(":memory:");
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
      d.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
    }
    d.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't');
      INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1, 'buy', '2026-09-30', 60000, 10, 'KRW', 1, 'krw', '2026-09-30T09:00:00.000Z', '2026-09-30T09:00:00.000Z');
    `);
    const 찾기 = (qty: number, 이후: string) => d.prepare(RECENT_SAME_TRADE).get(1, "buy", "2026-09-30", 60000, qty, 이후);
    expect(찾기(10, "2026-09-30T08:55:00.000Z")).toBeTruthy();
    expect(찾기(11, "2026-09-30T08:55:00.000Z")).toBeUndefined();
    expect(찾기(10, "2026-09-30T09:05:00.000Z")).toBeUndefined();
  });

  it("저장 경로가 확인 없이 같은 매매를 두 번 넣지 않고, 화면이 한 번 묻는다", () => {
    const 경로 = readFileSync("app/api/trades/route.ts", "utf-8");
    expect(경로).toContain("if (!input.confirm_duplicate)");
    expect(경로).toContain("duplicate: true");
    expect(경로.indexOf("RECENT_SAME_TRADE, [")).toBeLessThan(경로.indexOf("await execute(TRADE_INSERT"));
    const 화면 = readFileSync("components/PortfolioView.tsx", "utf-8");
    expect(화면).toContain("return submit(new Set([...confirmed, 물음]));"); // 확인 루프 하나 (25.945)
    // 25.789 (교차검증): 매도 재시도도 중복 확인에 먼저 닿는다, 취소면 목록을 다시 읽는다, 100배 넘는 체결가는 묻고 저장할 수 있다
    expect(경로.indexOf("RECENT_SAME_TRADE, [")).toBeLessThan(경로.indexOf("TRADES_FOR_HELD, ["));
    expect(화면).toContain("onReload?.();"); // 목록만 — "저장했습니다" 띠 없이 (25.790). 25.894 부터 폼은 TradeForm, 목록 다시 읽기는 고를 수 있다
    // 중복 확인이 체결가 확인보다 먼저 (25.790)
    expect(경로.indexOf("RECENT_SAME_TRADE, [")).toBeLessThan(경로.indexOf("tradePriceCheck(input.price"));
    expect(경로).toContain("if (가격검사.error && !input.confirm_price)");
    expect(경로).toContain('confirm_needed: "price_unit"');
  });
});
