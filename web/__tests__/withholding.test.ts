import { describe, expect, it } from "vitest";
import { estimateWithholding, withholdingRate } from "@/lib/portfolio";

/**
 * 배당 원천징수 채워 넣기 (docs/infra.md 25.127).
 *
 * **있었던 일.** 설정 화면에 세율 칸이 넷인데 **읽는 코드가 하나뿐**이었다
 * (`kr_transaction_pct`). 사용자가 "배당소득세 15.4" 를 적고 저장해도 바뀌는 숫자가
 * 하나도 없었다 — 25.108 에서 본 "상수는 있는데 판정이 안 본다" 와 같은 모양이다.
 *
 * 더 나쁜 것은 배당 입력 칸이었다. 매매 폼은 **"세금 (비우면 설정 비율)"** 이라고
 * 적어 두고 실제로 그렇게 도는데, 배당 폼은 아무 말 없이 **비우면 0** 으로 저장했다.
 * 세전 금액이 그대로 실수령이 되어 총손익과 TWR 지수에 최대 15.4% 부풀려 들어간다.
 */
describe("세율 고르기", () => {
  const taxes = { kr_dividend_pct: 15.4, us_dividend_pct: 15 };

  it("통화에 맞는 세율을 고른다", () => {
    expect(withholdingRate("KRW", taxes)).toBe(15.4);
    expect(withholdingRate("USD", taxes)).toBe(15);
  });

  it("설정이 비어 있으면 null 이다", () => {
    // **0 이 아니다.** 0 으로 돌려주면 "세금이 없다" 는 사실로 둔갑한다
    expect(withholdingRate("KRW", { kr_dividend_pct: null })).toBeNull();
    expect(withholdingRate("KRW", null)).toBeNull();
    expect(withholdingRate("USD", taxes as never)).toBe(15);
  });

  it("0 을 적었으면 0 이다", () => {
    // 비과세 계좌가 있다. "안 넣었다" 로 읽어 되돌리지 않는다
    expect(withholdingRate("KRW", { kr_dividend_pct: 0 })).toBe(0);
  });
});

describe("채워 넣을 금액", () => {
  it("원화는 원 단위로 반올림한다", () => {
    expect(estimateWithholding(100_000, 15.4, "KRW")).toBe(15_400);
    expect(estimateWithholding(12_345, 15.4, "KRW")).toBe(1_901); // 1901.13
  });

  it("외화는 센트까지 둔다", () => {
    expect(estimateWithholding(123.45, 15, "USD")).toBe(18.52); // 18.5175
  });

  it("세율이 없으면 채우지 않는다", () => {
    // 0 을 채우면 사용자가 "세금 없음" 을 확인한 것처럼 보인다. 빈칸이어야 한다
    expect(estimateWithholding(100_000, null, "KRW")).toBeNull();
  });

  it("세전 금액이 없거나 0 이면 채우지 않는다", () => {
    expect(estimateWithholding(null, 15.4, "KRW")).toBeNull();
    expect(estimateWithholding(0, 15.4, "KRW")).toBeNull();
    expect(estimateWithholding(Number.NaN, 15.4, "KRW")).toBeNull();
  });

  it("세율 0 은 0 을 채운다", () => {
    // 비과세 계좌다. 빈칸으로 두면 "못 채웠다" 와 구별이 안 된다 — 0 을 보여 줘야 확인이 된다
    expect(estimateWithholding(100_000, 0, "KRW")).toBe(0);
  });
});

describe("배당 폼이 실제로 쓴다", () => {
  /**
   * **부르는 곳이 없으면 함수를 둘 더 만든 것일 뿐이다.** 위 검사들은 순수 함수만 본다 —
   * `PortfolioView` 에서 호출을 지워도 하나도 안 깨진다. 이 저장소에 컴포넌트를 그려 보는
   * 장치가 없으므로(모든 web 검사가 lib 검사다) **소스를 읽어 배선을 확인한다.**
   * `proxy.test.ts` 가 크론 경로를 확인하는 것과 같은 방식이다.
   */
  it("세전 금액 칸이 세금 칸을 채우도록 이어져 있다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 본문 = readFileSync(join(process.cwd(), "components", "PortfolioView.tsx"), "utf-8");

    expect(본문).toContain("withholdingRate");
    expect(본문).toContain("estimateWithholding");
    // 세전 금액을 고칠 때 불려야 한다. onChange 가 setGross 로 되돌아가면 채우기가 죽는다
    expect(본문).toMatch(/세전 금액[^\n]*onChange=\{\(e\) => 세전바뀜\(/);
    // 사용자가 세금 칸을 손대면 그 뒤로는 덮어쓰지 않는다
    expect(본문).toContain("손댔나.current = true");
  });

  it("세율이 없으면 0 으로 기록된다고 화면이 말한다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 본문 = readFileSync(join(process.cwd(), "components", "PortfolioView.tsx"), "utf-8");

    // docs/portfolio.md: "설정도 비어 있으면 0 으로 두고 경고한다". 매매는 지켰고 배당은 안 지켰다
    expect(본문).toContain("0 으로 기록");
  });
});
