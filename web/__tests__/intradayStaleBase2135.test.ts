/**
 * 감시 목록이 묵으면 기업행위 첫날 가드가 prices 최근 행으로 전일 종가를 다시 잡는다 (docs/infra.md 25.2135·25.2137).
 * 25.2135 감사 재현: 목록 prev_close 10,000(3거래일 전), 어제 종가 9,600 = KIS 기준가, 현재가 9,200, 보유 손절 9,300 —
 * |9,600/10,000 − 1| = 4% > 2% 라 첫날 의심으로 손절을 지웠다. 묵은 날수만큼 날마다 빠졌다.
 * 25.2137(25.2135 교차검증 [필수] 1): 묵은 사이 분할·권리락이 끼면 최신 종가(행위 뒤) = 기준가라 가드를 지나 행위 전 손절로
 * 거짓 "손절선 터치" 가 났다 — 그 사이 기업행위가 있으면 목록 값 그대로 둔다.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { actionBaseClose, actionSuspect } from "@/lib/intraday";

/** 새것부터 (close, change_pct) — 등락률은 그날 종가 ÷ 전날 종가 그대로(기업행위 없음) */
const 행 = (...closes: number[]) =>
  closes.map((c, i) => ({ close: c, change_pct: i + 1 < closes.length ? (c / closes[i + 1] - 1) * 100 : null }));

describe("묵은 목록의 기준가 가드 (25.2135·25.2137)", () => {
  it("묵은 목록이면 목록을 만든 뒤의 최신 종가로 견줘 진짜 손절을 지우지 않는다", () => {
    expect(actionSuspect("KR", 9_200, 10_000, 9_600, true)).toBe(true); // 예전 — 목록 값으로 견줌
    expect(actionSuspect("KR", 9_200, actionBaseClose(10_000, 행(9_600, 9_800, 9_900, 10_000), false), 9_600, true)).toBe(false);
  });

  it("묵은 사이 분할(1:5)이 끼면 목록 값 그대로 — 행위 전 손절로 거짓 손절을 내지 않는다", () => {
    // 첫날 종가 19,600(등락률은 기준가 20,000 대비 −2%), 목록 prev 100,000
    const rows = [{ close: 19_600, change_pct: -2 }, { close: 100_000, change_pct: 0 }];
    expect(actionBaseClose(100_000, rows, false)).toBe(100_000);
    expect(actionSuspect("KR", 19_500, actionBaseClose(100_000, rows, false), 19_600, true)).toBe(true);
  });

  it("묵은 사이 권리락(계수 0.9)이 끼면 목록 값 그대로", () => {
    // 권리락 첫날: 종가 9,000 이 기준가 9,000 그대로(등락률 0) — 종가 비 0.9 와 어긋남
    const rows = [{ close: 8_950, change_pct: -0.56 }, { close: 9_000, change_pct: 0 }, { close: 10_000, change_pct: 0 }];
    expect(actionBaseClose(10_000, rows, false)).toBe(10_000);
    expect(actionSuspect("KR", 8_950, actionBaseClose(10_000, rows, false), 9_000, true)).toBe(true);
  });

  it("목록 값을 행에서 못 찾거나(창보다 오래 묵음) 행이 없으면 목록 값 그대로, 이번 장 목록도 그대로", () => {
    expect(actionBaseClose(10_000, 행(9_600, 9_800), false)).toBe(10_000);
    expect(actionBaseClose(10_000, [], false)).toBe(10_000);
    expect(actionBaseClose(10_000, undefined, false)).toBe(10_000);
    expect(actionBaseClose(10_000, 행(9_600, 10_000), true)).toBe(10_000);
    expect(actionBaseClose(null, 행(9_600, 10_000), false)).toBeNull();
  });

  it("장중 경로가 묵은 날만 종목별 최근 행을 묶어 읽고, 깨지면 목록 값으로 물러난다", () => {
    const route = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf8");
    expect(route).toContain("if (!이번장 && market === \"KR\" && targets.length)");
    expect(route).toContain("actionBaseClose(target.prev_close, 최근행.get(target.stock_id), 이번장)");
    expect(route).toContain("묵은 목록 기준가 행을 읽지 못했다");
  });
});
