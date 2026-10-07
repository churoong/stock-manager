import { describe, expect, it } from "vitest";
import {
  dividendInputSchema,
  isRealDate,
  recalcPending,
  tradeInputSchema,
} from "@/lib/portfolio";

/** 달력에 없는 날짜가 저장되어 포트폴리오 배치가 매번 죽던 길 (docs/infra.md 25.311). */
describe("isRealDate", () => {
  it("달력에 없는 날짜를 거절한다", () => {
    expect(isRealDate("2026-02-30")).toBe(false);
    expect(isRealDate("2025-13-45")).toBe(false);
    expect(isRealDate("2024-02-29")).toBe(true);
    expect(isRealDate("2026-09-27")).toBe(true);
  });

  it("매매·배당 스키마가 그것을 쓴다", () => {
    const 매매 = {
      stock_id: 1,
      side: "buy",
      trade_date: "2026-02-30",
      price: 1000,
      quantity: 1,
    };
    expect(tradeInputSchema.safeParse(매매).success).toBe(false);
    const 배당 = {
      stock_id: 1,
      pay_date: "2025-13-45",
      gross_amount: 100,
      tax: 0,
    };
    expect(dividendInputSchema.safeParse(배당).success).toBe(false);
  });
});

describe("금액 상한 (docs/infra.md 25.316)", () => {
  it("1e308 같은 값은 받지 않는다 — 배치에서 inf 가 되어 JSON 이 깨진다", () => {
    const 매매 = { stock_id: 1, side: "buy", trade_date: "2026-09-25", price: 1e308, quantity: 10 };
    expect(tradeInputSchema.safeParse(매매).success).toBe(false);
    expect(tradeInputSchema.safeParse({ ...매매, price: 70_000 }).success).toBe(true);
    const 배당 = { stock_id: 1, pay_date: "2026-09-25", gross_amount: 1e300, tax: 0 };
    expect(dividendInputSchema.safeParse(배당).success).toBe(false);
  });
});

describe("recalcPending (docs/infra.md 25.329)", () => {
  it("마지막 매매를 지워 0건이 돼도 지난 계산이 있으면 '계산 중' 을 보인다", () => {
    expect(recalcPending({ stale: true, has_trades: false, as_of: "2026-09-25" })).toBe(true);
  });
  it("처음(계산도 매매도 없음)에는 보이지 않는다", () => {
    expect(recalcPending({ stale: true, has_trades: false, as_of: null })).toBe(false);
    expect(recalcPending({ stale: false, has_trades: true, as_of: "2026-09-25" })).toBe(false);
  });
});

describe("매매 입력의 남은 구멍 (docs/infra.md 25.554, 감사 재현)", () => {
  const 기본 = { stock_id: 1, side: "sell" as const, trade_date: "2026-09-01", price: 100, quantity: 1 };
  it("매도 수수료 + 세금 합이 체결금액을 넘으면 받지 않는다", async () => {
    const { tradeInputSchema } = await import("@/lib/portfolio");
    expect(tradeInputSchema.safeParse({ ...기본, fee: 60, tax: 60 }).success).toBe(false);
    expect(tradeInputSchema.safeParse({ ...기본, fee: 40, tax: 60 }).success).toBe(true);
  });
  it("매수 세금은 받지 않는다", async () => {
    const { tradeInputSchema } = await import("@/lib/portfolio");
    expect(tradeInputSchema.safeParse({ ...기본, side: "buy", tax: 1 }).success).toBe(false);
    expect(tradeInputSchema.safeParse({ ...기본, side: "buy", tax: 0 }).success).toBe(true);
  });
  it("연도 오타(0026년)는 받지 않는다", async () => {
    const { tradeInputSchema } = await import("@/lib/portfolio");
    expect(tradeInputSchema.safeParse({ ...기본, side: "buy", trade_date: "0026-09-01" }).success).toBe(false);
    expect(tradeInputSchema.safeParse({ ...기본, side: "buy", trade_date: "1995-01-03" }).success).toBe(true);
  });
});

describe("재계산이 멈추면 말한다 (docs/infra.md 25.554·25.555)", () => {
  const now = new Date("2026-09-28T10:00:00Z");
  const 계산 = "2026-09-28T09:00:00Z";
  it("저장 뒤 10분 안쪽은 계산 중, 넘으면 멈춤", async () => {
    const { recalcStuckNote } = await import("@/lib/portfolio");
    expect(recalcStuckNote(true, "2026-09-28T09:55:00Z", null, now, 계산)).toBeNull();
    expect(recalcStuckNote(false, "2026-09-28T09:40:00Z", null, now, 계산)).toBeNull();
    expect(recalcStuckNote(true, "2026-09-28T09:40:00Z", null, now, 계산)).toContain("깨어나지 않았거나");
    // 진행 중이면 기다린다
    expect(recalcStuckNote(true, "2026-09-28T09:40:00Z", { status: "running", started_at: "2026-09-28T09:41:00Z", error_text: null }, now, 계산)).toBeNull();
  });
  it("저장·계산 뒤에 실패한 실행이면 그 까닭, 그 전의 실패는 무관", async () => {
    const { recalcStuckNote } = await import("@/lib/portfolio");
    const 실패 = { status: "failed", started_at: "2026-09-28T09:41:00Z", error_text: "환율 없음" };
    expect(recalcStuckNote(true, "2026-09-28T09:40:00Z", 실패, now, 계산)).toContain("재계산이 실패했습니다: 환율 없음");
    expect(recalcStuckNote(true, "2026-09-28T09:40:00Z", { ...실패, started_at: "2026-09-28T08:00:00Z" }, now, 계산))
      .toContain("깨어나지 않았거나");
  });
  it("running 이 30분 넘게 남으면 멈춤 (25.561, 교차검증)", async () => {
    const { recalcStuckNote } = await import("@/lib/portfolio");
    const 굳음 = { status: "running", started_at: "2026-09-28T09:01:00Z", error_text: null };
    expect(recalcStuckNote(true, "2026-09-28T09:00:30Z", 굳음, now, 계산)).toContain("59분째 끝나지 않았습니다");
    expect(recalcStuckNote(true, "2026-09-28T09:00:30Z", { ...굳음, started_at: "2026-09-28T09:50:00Z" }, now, 계산)).toBeNull();
  });
  it("지운 뒤(남은 행이 옛 시각)에는 시간으로 멈춤이라 하지 않는다 (25.555, 교차검증)", async () => {
    const { recalcStuckNote } = await import("@/lib/portfolio");
    expect(recalcStuckNote(true, "2026-09-25T01:00:00Z", { status: "running", started_at: "2026-09-28T09:59:00Z", error_text: null }, now, 계산)).toBeNull();
    expect(recalcStuckNote(true, "2026-09-25T01:00:00Z", null, now, 계산)).toBeNull();
  });
  it("경로가 그 말을 싣는다", async () => {
    const { readFileSync } = await import("node:fs");
    const 경로 = readFileSync("app/api/portfolio/route.ts", "utf8");
    expect(경로).toContain("recalc_stuck: stuck");
    // 못 읽으면 멈춤이 아니라 계산 중 (25.555)
    expect(경로).toMatch(/catch \{[\s\S]{0,200}stuck = null;/);
  });
});
