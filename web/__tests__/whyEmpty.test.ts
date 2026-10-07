import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MOMENTUM_DAYS, RISK_DAYS, missingInputs, whyNoSignals, type 재료 } from "@/lib/whyEmpty";

/**
 * 추천이 **왜 비어 있는지** (docs/infra.md 25.86).
 *
 * 2026-09-21 까지 국내 쪽은 신호가 0건이면 무조건 이렇게 말했다.
 *
 * > 아직 신호를 계산한 적이 없습니다. **매수 신호 배치를 먼저 돌리세요**
 *
 * 그런데 따라잡기 7단계가 신호 배치를 **매일 돌리고 있었다.** 돌았지만 재료가 없어
 * 0건이었을 뿐이다. 화면이 오진하고, 사용자에게 **해도 소용없는 일**을 시켰다.
 */

const 있음: 재료 = { signalRuns: 12, priceDays: 250, financials: 861, sectors: 2701 };

describe("모자란 재료", () => {
  it("다 있으면 빈 목록", () => {
    expect(missingInputs(있음)).toEqual([]);
  });

  it("시세가 모자라면 숫자까지 말한다", () => {
    expect(missingInputs({ ...있음, priceDays: 88 })).toEqual(["시세 88/200거래일"]);
  });

  it("재무·업종이 비면 적는다", () => {
    expect(missingInputs({ ...있음, financials: 0, sectors: 0 })).toEqual(["재무 없음", "업종 없음"]);
  });

  it("**못 읽은 것은 빈 것이 아니다**", () => {
    // null 을 0 으로 치면 있는데 없다고 말하게 된다 (25.74)
    expect(missingInputs({ signalRuns: null, priceDays: null, financials: null, sectors: null })).toEqual([]);
  });

  it("2026-09-19 실측 그대로", () => {
    // 시세 88거래일 · 재무 0 · 업종 0 — 이 항목이 태어난 자리다
    const 그날: 재료 = { signalRuns: 3, priceDays: 88, financials: 0, sectors: 0 };

    expect(missingInputs(그날)).toEqual(["시세 88/200거래일", "재무 없음", "업종 없음"]);
  });
});

describe("무엇이라 말하나", () => {
  it("정말 안 돌았으면 돌리라고 한다", () => {
    const 글 = whyNoSignals({ ...있음, signalRuns: 0 });

    expect(글).toContain("매수 신호 배치를 먼저 돌리세요");
  });

  it("**돌았는데 재료가 없으면 돌리라고 하지 않는다**", () => {
    const 글 = whyNoSignals({ signalRuns: 3, priceDays: 88, financials: 0, sectors: 0 });

    expect(글, "해도 소용없는 일을 시킨다").not.toContain("돌리세요");
    expect(글).toContain("재료가 아직 모자랍니다");
    expect(글).toContain("시세 88/200거래일");
    expect(글).toContain("고장이 아닙니다");
  });

  it("재료가 다 있으면 조건을 못 맞춘 것이라고 한다", () => {
    const 글 = whyNoSignals(있음);

    expect(글).toContain("조건을 만족한 종목이 없습니다");
    expect(글).not.toContain("돌리세요");
  });

  it("실행 기록을 못 읽으면 **모른다고 한다**", () => {
    const 글 = whyNoSignals({ signalRuns: null, priceDays: null, financials: null, sectors: null });

    expect(글).toContain("읽지 못해");
    expect(글, "모르면서 정상이라고 단정한다").not.toContain("조건을 만족한 종목이 없습니다");
  });

  it("시세만 모자라도 말해 준다", () => {
    expect(whyNoSignals({ ...있음, priceDays: 190 })).toContain("시세 190/200거래일");
  });
});

describe("경로가 이것을 쓴다", () => {
  const 글 = readFileSync(join(process.cwd(), "..", "web/app/api/recommend/route.ts"), "utf-8");

  it("국내 빈 화면이 whyNoSignals 를 쓴다", () => {
    expect(글).toContain("whyNoSignals(");
  });

  it("옛 문구를 경로에 그대로 두지 않는다", () => {
    // 남아 있으면 어느 쪽이 나가는지 알 수 없다
    expect(글).not.toContain("아직 신호를 계산한 적이 없습니다");
  });

  it("**신호가 0건인 날에만 재료를 읽는다**", () => {
    // 읽기가 Turso 를 잠근 적이 있다(24절). 정상인 날에 왕복이 늘면 안 된다.
    // 정의(`async function 재료읽기`)는 빼고 **부르는 자리**만 센다
    const 부름 = (글.match(/(?<!function )재료읽기\(/g) ?? []).length;
    // 바깥 `else if` 로 자른다 — 안쪽에도 `} else if` 가 있어 먼저 걸린다
    const 빈경우 = 글.split("if (rows.length === 0)")[1]?.split("else if (rows.every")[0] ?? "";

    expect(부름, "부르는 곳이 하나가 아니다").toBe(1);
    expect(빈경우).toContain("재료읽기(");
  });

  it("한 질의로 묶는다", () => {
    const 몸통 = 글.split("async function 재료읽기")[1].split("export async function POST")[0];

    expect((몸통.match(/await execute\(/g) ?? []).length).toBe(1);
  });

  it("못 읽으면 null 로 돌려준다", () => {
    const 몸통 = 글.split("async function 재료읽기")[1].split("export async function POST")[0];

    expect(몸통).toContain("signalRuns: null");
  });
});

describe("문턱", () => {
  it("파이썬과 같은 값이다", () => {
    // 단일 정의처는 파이썬이다. 여기가 갈라지면 tests/test_recommend_reason.py 가 깨진다
    expect(MOMENTUM_DAYS).toBe(126);
    expect(RISK_DAYS).toBe(200);
  });
});


describe("안내 글자는 **평문**이다", () => {
  /**
   * 화면은 `notes` 를 `{note}` 로 그대로 찍는다(`RecommendList.tsx`). 그래서
   * `**굵게**` 나 `[글](주소)` 를 넣으면 **별표와 괄호가 그대로 보인다.**
   *
   * 2026-09-21 에 실제로 그렇게 썼다 — 25.86 을 쓰며 `**낼 것이 없었습니다**` 를 넣었다.
   * 문구를 다듬다 보면 마크다운이 손에 붙는다. 그래서 글자 자체를 검사한다.
   */
  const 모든문구 = [
    whyNoSignals({ signalRuns: 0, priceDays: 0, financials: 0, sectors: 0 }),
    whyNoSignals({ signalRuns: 3, priceDays: 88, financials: 0, sectors: 0 }),
    whyNoSignals({ signalRuns: 3, priceDays: 250, financials: 861, sectors: 2701 }),
    whyNoSignals({ signalRuns: null, priceDays: null, financials: null, sectors: null }),
  ];

  it.each(모든문구)("굵게 표시가 없다: %s", (글) => {
    expect(글, "화면에 별표가 그대로 보인다").not.toContain("**");
  });

  it.each(모든문구)("마크다운 링크가 없다: %s", (글) => {
    expect(글, "화면에 대괄호와 괄호가 그대로 보인다").not.toMatch(/\[[^\]]+\]\(/);
  });

  it("스크리너로 가는 길은 **화면**이 진짜 링크로 단다", () => {
    const 화면 = readFileSync(join(process.cwd(), "..", "web/components/RecommendList.tsx"), "utf-8");

    expect(화면).toContain('href="/screener"');
    expect(화면).toContain("종목 찾기");
  });

  it("빈 화면에서만 그 길을 보여 준다", () => {
    const 화면 = readFileSync(join(process.cwd(), "..", "web/components/RecommendList.tsx"), "utf-8");
    const 빈경우 = 화면.split("groups.length === 0 ?")[1].split(") : (")[0];

    expect(빈경우).toContain('href="/screener"');
  });
});
