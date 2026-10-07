import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { pct, pctSuffix, pnlClass, ratioOf, signedWon } from "@/lib/portfolio";

/**
 * **모르는 것을 0 으로 보여 주지 않는다** (docs/infra.md 25.71).
 *
 * 돈이 보이는 화면에서 가장 위험한 거짓말이다. 서식 함수들은 이미 `null` 을 `-` 로 그리게
 * 해 두었는데(`그릴수있나`), **부르는 쪽에서 미리 0 으로 바꿔 넘기면** 그 장치가 무력해진다.
 *
 * 2026-09-21 에 실제로 그랬다. 보유 종목 줄이 이랬다.
 *
 * ```tsx
 * 평가손익 {signedWon(p.unrealized_pnl_krw)}
 * {p.cost_krw ? ` (${pct((p.unrealized_pnl_krw ?? 0) / p.cost_krw)})` : ""}
 * ```
 *
 * 현재가를 못 받아 `unrealized_pnl_krw` 가 비면 화면에 **`평가손익 - (0.0%)`** 가 뜬다.
 * 금액은 모른다면서 비율은 0% 라고 말하는 셈이고, 보는 사람은 **"본전"** 으로 읽는다.
 *
 * 25.64(편입 0 이 멀쩡한 스냅샷을 덮음)와 같은 모양이다 —
 * **모르는 것을 아는 것처럼 다루면 안 된다.**
 */

const 뿌리 = join(process.cwd(), "..");
const 읽기 = (경로: string) => readFileSync(join(뿌리, 경로), "utf-8");

describe("비(比) 계산", () => {
  it("둘 다 있으면 나눈다", () => {
    expect(ratioOf(300, 1000)).toBeCloseTo(0.3, 10);
  });

  it.each([
    ["분자가 null", null, 1000],
    ["분자가 undefined", undefined, 1000],
    ["분모가 null", 300, null],
    ["분모가 0", 300, 0],
    ["분모가 NaN", 300, Number.NaN],
    ["분자가 Infinity", Number.POSITIVE_INFINITY, 1000],
  ])("%s 이면 null", (_이름, a, b) => {
    expect(ratioOf(a as number | null, b as number | null)).toBeNull();
  });

  it("음수 원가는 나눈다 — 판단은 부르는 쪽 몫이다", () => {
    // 0 만 막는다. 있을 법하지 않은 값을 여기서 삼키면 이상한 데이터가 조용히 숨는다
    expect(ratioOf(300, -1000)).toBeCloseTo(-0.3, 10);
  });
});

describe("꼬리표", () => {
  it("알면 괄호로 붙인다", () => {
    expect(pctSuffix(300, 1000)).toBe(" (+30.0%)");
  });

  it("손실도 부호가 붙는다", () => {
    expect(pctSuffix(-300, 1000)).toBe(" (-30.0%)");
  });

  it("**모르면 아무것도 안 붙인다** — 0% 로 꾸미지 않는다", () => {
    expect(pctSuffix(null, 1000)).toBe("");
    expect(pctSuffix(300, null)).toBe("");
    expect(pctSuffix(300, 0)).toBe("");
  });

  it("고치기 전이었다면 이렇게 나왔다", () => {
    // 이 줄이 이 파일이 있는 이유다. 회귀하면 여기가 아니라 위 테스트가 깨진다
    const 모르는값: number | null = null;
    expect(pct((모르는값 ?? 0) / 1000)).toBe("0.0%");
    expect(`평가손익 ${signedWon(null)}${pctSuffix(null, 1000)}`).toBe("평가손익 -");
  });
});

describe("손익 색", () => {
  it("이익은 붉게, 손실은 푸르게", () => {
    expect(pnlClass(1000)).toContain("rose");
    expect(pnlClass(-1000)).toContain("blue");
  });

  it("**모르면 칠하지 않는다**", () => {
    // 붉은색은 "올랐다" 는 뜻이다. 모르는 것에 칠하면 거짓말이다
    expect(pnlClass(null)).toBe("");
    expect(pnlClass(undefined)).toBe("");
    expect(pnlClass(Number.NaN)).toBe("");
  });

  it("0 도 칠하지 않는다", () => {
    expect(pnlClass(0)).toBe("");
  });

  it("부호는 **보이는 숫자**를 따른다", () => {
    // 0.4원 이익은 signedWon 이 "0원" 으로 적는다. 그것을 붉게 칠하면 앞뒤가 안 맞는다
    expect(signedWon(0.4)).toBe("0원");
    expect(pnlClass(0.4)).toBe("");
    expect(signedWon(-0.4)).toBe("0원");
    expect(pnlClass(-0.4)).toBe("");
  });
});

describe("화면이 서식 함수의 장치를 우회하지 않는다", () => {
  const 화면들 = ["web/components/PortfolioView.tsx", "web/components/BacktestView.tsx", "web/components/RecommendList.tsx"];

  it.each(화면들)("%s 가 `?? 0` 을 pct 에 넣지 않는다", (경로) => {
    const 글 = 읽기(경로);

    expect(글, `${경로} 가 모르는 값을 0 으로 바꿔 비율을 낸다`).not.toMatch(/pct\([^)]*\?\?\s*0/);
  });

  it("보유 줄이 공용 도우미를 쓴다", () => {
    const 글 = 읽기("web/components/PortfolioView.tsx");

    expect(글).toContain("pctSuffix(p.unrealized_pnl_krw, p.cost_krw)");
    expect(글).toContain("pnlClass(p.unrealized_pnl_krw)");
  });
});

describe("근거가 없으면 추천처럼 보이지 않는다", () => {
  const 글 = 읽기("web/components/CriteriaTable.tsx");

  it("0줄이면 표가 아니라 경고를 그린다", () => {
    expect(글).toMatch(/rows\.length === 0/);
    expect(글).toContain("이 추천을 믿지 마세요");
  });

  it("경고가 눈에 띄는 색이다", () => {
    const 조각 = 글.split("rows.length === 0", 1)[1] ?? 글.split("rows.length === 0")[1];
    expect(조각.slice(0, 600)).toMatch(/border-red|bg-red|text-red/);
  });

  it("근거표를 그리는 곳은 모두 이 컴포넌트를 쓴다", () => {
    // 어딘가 제 표를 따로 그리면 그 화면만 경고가 없다
    for (const 경로 of [
      "web/components/RecommendList.tsx",
      "web/components/EtfList.tsx",
      "web/components/AccumulationStocks.tsx",
      "web/components/SatelliteList.tsx",
      "web/components/StockDetail.tsx",
    ]) {
      expect(읽기(경로), `${경로}`).toContain("CriteriaTable");
    }
  });
});
