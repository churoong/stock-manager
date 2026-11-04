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
    // 권리락 첫날: 종가 9,000 이 기준가 9,000 그대로(등락률 0) — 종가 비 0.9 와 어긋남. 오늘 기준가 = 어제 종가 8,950
    const rows = [{ close: 8_950, change_pct: -0.56 }, { close: 9_000, change_pct: 0 }, { close: 10_000, change_pct: 0 }];
    expect(actionBaseClose(10_000, rows, false)).toBe(10_000);
    expect(actionSuspect("KR", 8_900, actionBaseClose(10_000, rows, false), 8_950, true)).toBe(true);
  });

  it("그 사이 등락률이 빈 행(야후 대체)이 있으면 목록 값 그대로 — 기업행위를 가를 수 없다 (25.2138 [필수])", () => {
    const rows = [{ close: 8_950, change_pct: null }, { close: 10_000, change_pct: 0 }];
    expect(actionBaseClose(10_000, rows, false)).toBe(10_000);
    expect(actionSuspect("KR", 8_900, actionBaseClose(10_000, rows, false), 8_950, true)).toBe(true);
    // 목록 날 행 자신의 등락률(그 전날 대비)은 보지 않는다
    expect(actionBaseClose(10_000, [{ close: 9_600, change_pct: -4 }, { close: 10_000, change_pct: null }], false)).toBe(9_600);
  });

  it("같은 종가가 여러 행이면 가장 오래된 행부터 본다 — 권리락 뒤 우연히 옛 종가로 돌아와도 놓치지 않는다 (25.2138)", () => {
    // 새것부터: 9,900 · 10,000(회복) · 9,000(권리락 첫날, 등락 0) · 10,000(목록 날). 새것부터 첫 10,000 을 목록 날로 보면
    // 9,900 을 돌려 권리락을 놓친다
    const rows = [{ close: 9_900, change_pct: -1 }, { close: 10_000, change_pct: 11.11 }, { close: 9_000, change_pct: 0 },
      { close: 10_000, change_pct: 0 }];
    expect(actionBaseClose(10_000, rows, false)).toBe(10_000);
  });

  it("묵은 목록에서도 오늘 기업행위 첫날은 잡는다", () => {
    const 기준 = actionBaseClose(10_500, 행(10_000, 10_200, 10_500), false);
    expect(기준).toBe(10_000);
    expect(actionSuspect("KR", 7_100, 기준, 7_140, true)).toBe(true);
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
    // 종목별 색인 질의 — 최근 행부터(DESC) 21행(25.2138 교차검증: ASC 로 바꿔도 통과했다)
    expect(route).toContain("SELECT close, change_pct FROM prices WHERE stock_id = ? AND close IS NOT NULL AND close > 0 ORDER BY date DESC LIMIT ?");
    expect(route).toContain("args: [t.stock_id, ACTION_GUARD_ROWS + 1]");
  });
});
