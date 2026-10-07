/** 밸류 분모(시가총액)를 어느 날 값에서 어떻게 옮겼는지 (docs/factors.md 3.1, docs/infra.md 25.954). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MARKET_CAP_KEY, marketCapNote } from "@/lib/stockDetail";

const 뿌리 = join(process.cwd(), "..");

describe("marketCapNote", () => {
  it("옮겼으면 배수와 날짜를, 아니면 사유를 적는다", () => {
    const 옮김 = marketCapNote({ [MARKET_CAP_KEY]: { cap: 1_000_000_000, cap_date: "2026-09-26", factor: 1.0659, scaled: true } }, "KRW");
    expect(옮김).toBe("1,000,000,000원 (2026-09-26 값) × 가격 배수 1.0659 — 기준일 가격으로 옮김");
    const 그대로 = marketCapNote({ [MARKET_CAP_KEY]: { cap: 5e8, cap_date: "2026-09-16", scaled: false, reason: "시총 날짜≠스냅샷 날짜" } }, "USD");
    expect(그대로).toBe("500,000,000달러 (2026-09-16 값) 그대로 — 옮기지 않음: 시총 날짜≠스냅샷 날짜");
  });

  it("옛 판(키 없음)은 지어내지 않는다", () => {
    expect(marketCapNote({ ep: 0.1 })).toContain("날짜 미상");
    expect(marketCapNote(null)).toContain("분모 시점 규칙 전 계산");
  });

  it("예약 키가 파이썬과 같다", () => {
    const py = readFileSync(join(뿌리, "batch", "services", "scoring.py"), "utf8");
    expect(py).toContain(`MARKET_CAP_KEY = "${MARKET_CAP_KEY}"`);
  });

  it("종목 상세가 시가총액(분모) 행을 그린다", () => {
    const src = readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf-8");
    expect(src).toContain("시가총액(분모)");
    expect(src).toContain("marketCapNote(");
  });
});
