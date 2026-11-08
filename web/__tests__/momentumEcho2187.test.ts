/** 에코 모멘텀 근거 줄 — 두 가격 행(c−126·c−273) (docs/factors.md 3.4, docs/infra.md 25.2187, 점수 판 12). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { METRIC_LABELS, MOMENTUM_POINTS_KEY, momentumEchoNote } from "@/lib/stockDetail";

const 뿌리 = join(process.cwd(), "..");

describe("momentumEchoNote", () => {
  it("두 기준점의 날짜·수정종가로 값을 다시 낼 수 있게 적는다", () => {
    const raw = {
      momentum_echo: 0.6,
      [MOMENTUM_POINTS_KEY]: { p126: { date: "2026-05-04", close: 80 }, p273: { date: "2025-09-30", close: 50 } },
    };
    expect(momentumEchoNote(raw)).toBe("에코 60.0% = 2026-05-04 수정종가 80.00 ÷ 2025-09-30 수정종가 50.00 − 1");
  });

  it("판 11 행(키 없음)·기준점 빠짐은 지어내지 않는다", () => {
    expect(momentumEchoNote({ momentum_vol_adjusted: 1.2 })).toBe("");
    expect(momentumEchoNote({ [MOMENTUM_POINTS_KEY]: { p126: { date: "2026-05-04", close: 80 } } })).toBe("");
    expect(momentumEchoNote(null)).toBe("");
  });

  it("예약 키가 파이썬과 같고, 화면 모멘텀 줄이 그린다", () => {
    const py = readFileSync(join(뿌리, "batch", "services", "scoring.py"), "utf8");
    expect(py).toContain(`MOMENTUM_POINTS_KEY = "${MOMENTUM_POINTS_KEY}"`);
    const src = readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf-8");
    expect(src).toContain('key === "momentum" && f && momentumEchoNote(f.raw)');
    expect(METRIC_LABELS.momentum_echo).toBeTruthy();
  });
});
