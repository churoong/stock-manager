import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FX_MAX_STALE_DAYS, decideFx } from "@/lib/portfolio";

/**
 * 기록에 쓸 **환율을 정하는 규칙** (docs/infra.md 25.72).
 *
 * 규칙(사용자 결정 2026-09-17): 원화는 1 · 미국은 입력값(증권사 체결 환율) 우선 ·
 * 없으면 그날 USDKRW 종가 · 쓸 만한 종가도 없으면 **거절**.
 *
 * 2026-09-21 까지 이 규칙이 **매매 기록과 배당 수령 두 경로에 똑같이 베껴져** 있었다.
 * 지금은 같지만 한쪽만 고쳐지면 같은 날 같은 종목의 두 기록이 **서로 다른 환율**로 남고,
 * 환차손익이 조용히 어긋난다. 스키마가 `fx_rate NOT NULL CHECK (> 0)` 으로 막아 주는 것은
 * "비었는가" 뿐이고 **"맞는 값인가" 가 아니다.**
 */

const 뿌리 = join(process.cwd(), "..");
const 읽기 = (경로: string) => readFileSync(join(뿌리, 경로), "utf-8");
const 오늘 = "2026-09-18";

describe("환율 고르기", () => {
  it("원화는 언제나 1", () => {
    expect(decideFx("KRW", null, undefined, 오늘)).toEqual({ rate: 1, source: "none" });
  });

  it("원화면 입력값이 있어도 무시한다", () => {
    // 원화 종목에 환율을 넣는 것은 뜻이 없다. 1 이 아닌 값으로 원화 손익을 뒤틀지 않는다
    expect(decideFx("KRW", 1300, { date: 오늘, rate: 1400 }, 오늘)).toEqual({ rate: 1, source: "none" });
  });

  it("입력값이 저장된 종가보다 **먼저**다", () => {
    // 증권사 체결 환율이 실제로 치른 값이다
    expect(decideFx("USD", 1310.5, { date: 오늘, rate: 1400 }, 오늘)).toEqual({
      rate: 1310.5,
      source: "manual",
    });
  });

  it("입력값이 없으면 그날 종가", () => {
    expect(decideFx("USD", null, { date: 오늘, rate: 1400 }, 오늘)).toEqual({ rate: 1400, source: "auto" });
  });

  it("저장된 종가가 없으면 거절한다", () => {
    // 없는 환율로 환차손익을 계산하지 않는다
    expect(decideFx("USD", null, undefined, 오늘)).toEqual({ error: "no-rate" });
  });

  it("너무 오래된 종가는 거절한다", () => {
    const 오래됨 = new Date(Date.parse(오늘) - (FX_MAX_STALE_DAYS + 1) * 86_400_000).toISOString().slice(0, 10);

    expect(decideFx("USD", null, { date: 오래됨, rate: 1400 }, 오늘)).toEqual({ error: "no-rate" });
  });

  it("한도 안의 오래된 종가는 받는다", () => {
    const 아슬아슬 = new Date(Date.parse(오늘) - FX_MAX_STALE_DAYS * 86_400_000).toISOString().slice(0, 10);

    expect(decideFx("USD", null, { date: 아슬아슬, rate: 1400 }, 오늘)).toEqual({ rate: 1400, source: "auto" });
  });

  it("기록 날짜보다 **나중** 종가는 거절한다", () => {
    // 그날 몰랐던 값으로 그날을 기록하면 안 된다 (look-ahead)
    const 나중 = "2026-09-19";

    expect(decideFx("USD", null, { date: 나중, rate: 1400 }, 오늘)).toEqual({ error: "no-rate" });
  });

  it("0 은 입력값으로 치지 않는다", () => {
    // 스키마가 CHECK (fx_rate > 0) 이다. 0 을 manual 로 받으면 INSERT 가 터진다
    expect(decideFx("USD", 0, { date: 오늘, rate: 1400 }, 오늘)).toEqual({ rate: 1400, source: "auto" });
  });
});

describe("두 경로가 같은 규칙을 쓴다", () => {
  const 경로들 = ["web/app/api/trades/route.ts", "web/app/api/dividends/route.ts"];

  it.each(경로들)("%s 가 decideFx 를 부른다", (경로) => {
    expect(읽기(경로)).toContain("decideFx(stock.currency, input.fx_rate, fx,");
  });

  it.each(경로들)("%s 가 제 규칙을 다시 쓰지 않는다", (경로) => {
    const 글 = 읽기(경로);

    expect(글, `${경로} 에 환율 규칙 사본이 남아 있다`).not.toMatch(/fxSource = "manual"/);
    expect(글).not.toMatch(/fxUsable\(/);
  });

  it.each(경로들)("%s 는 원화면 환율을 찾지 않는다", (경로) => {
    // 읽기 예산도 아낀다 (docs/infra.md 24절)
    expect(읽기(경로)).toContain('stock.currency === "KRW"');
  });
});
