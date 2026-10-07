import { describe, expect, it } from "vitest";
import {
  VALUE_FILTERS,
  screenerFilterSchema,
  usedValueFilters,
  whyEmpty,
  withoutValueFilters,
  type ScreenerRow,
} from "@/lib/screener";

/**
 * 스크리너가 **0건일 때 무엇이라 말하나** (docs/infra.md 25.149).
 *
 * **있었던 일.** 한 건도 없으면 화면이 늘 한 문장이었다.
 *
 * > 조건에 맞는 종목이 없습니다. **범위를 넓혀 보세요.**
 *
 * 그런데 `WHERE per <= 10` 은 `per` 이 `NULL` 인 종목을 **전부 버린다**. 재무가 아직
 * 안 채워졌으면 범위를 아무리 넓혀도 0건이다. 화면은 사용자에게 **해도 소용없는 일**을
 * 시킨 셈이고, 25.86 이 추천 화면에서 고친 것과 똑같은 오진이다.
 *
 * 게다가 경로가 가진 안내들(`성과 지표가 아직…`·`재무가 아직…`)은 전부
 * `rows.length > 0` 뒤에 있었다 — **정작 한 건도 없을 때만 입을 다물었다.**
 */

const 기본 = screenerFilterSchema.parse({});

function 행(값: Partial<ScreenerRow>): ScreenerRow {
  return {
    stock_id: 1,
    ticker: "005930",
    name: "삼성전자",
    market: "KOSPI",
    sector: null,
    market_cap: null,
    avg_turnover_20d: null,
    close: null,
    price_date: null,
    fiscal_year: null,
    per: null,
    pbr: null,
    roe: null,
    debt_ratio: null,
    operating_margin: null,
    revenue_growth: null,
    operating_income_growth: null,
    cagr: null,
    mdd: null,
    sharpe: null,
    volatility_ann: null,
    data_points: null,
    sentiment: null,
    sentiment_date: null,
    ...값,
  };
}

describe("세 가지를 가른다", () => {
  it("조건을 다 빼도 종목이 없으면 조건 탓이 아니다", () => {
    const 말 = whyEmpty(
      screenerFilterSchema.parse({ per: { min: null, max: 10 } }),
      [],
    );
    expect(말[0]).toContain("조건이 좁아서가 아닙니다");
    expect(말[0]).not.toContain("범위를 넓혀");
  });

  it("업종을 골랐으면 마스터 탓으로 돌리지 않는다 (25.682·25.686)", () => {
    // 진단 조회도 업종을 그대로 건다 — 그 업종에 편입 종목이 없는 것이지 마스터가 빈 것이 아니다
    const 말 = whyEmpty(
      screenerFilterSchema.parse({ sector: "없는업종", per: { min: null, max: 10 } }),
      [],
    );
    expect(말[0]).toContain("업종 없는업종 의 유니버스 편입 종목이 없습니다");
    expect(말[0]).toContain("[유니버스만]");
    expect(말[0]).not.toContain("종목 마스터");
  });

  it("미국 작은 거래소만 고른 0건은 고장으로 안내하지 않는다 (25.708)", () => {
    const 말 = whyEmpty(screenerFilterSchema.parse({ country: "US", market: "IEX" }), []);
    expect(말[0]).toContain("작은 거래소는 편입 종목이 없을 수 있습니다");
    expect(말[0]).not.toContain("종목 마스터");
  });

  it("시장만 골랐는데 편입이 0 이면 여전히 고장을 의심한다 (25.686, 교차검증)", () => {
    const 말 = whyEmpty(screenerFilterSchema.parse({ market: "KOSPI" }), []);
    expect(말[0]).toContain("종목 마스터나 유니버스");
  });

  it("건 조건의 값이 하나도 없으면 '넓혀도 안 나온다' 고 말한다", () => {
    // **이 검사가 이 파일의 이유다.** 재무가 없는 날 PER 조건을 걸면 늘 이 자리다
    const 조건 = screenerFilterSchema.parse({ per: { min: null, max: 10 } });
    const 말 = whyEmpty(조건, [
      행({ market_cap: 1_000 }),
      행({ market_cap: 2_000 }),
    ]);

    expect(말[0]).toContain("PER");
    expect(말[0]).toContain("2종목 모두 그 값이 비어 있습니다");
    expect(말[0]).toContain("범위를 넓혀도 나오지 않습니다");
  });

  it("값이 있는데 0건이면 그때만 '넓혀 보세요'", () => {
    const 조건 = screenerFilterSchema.parse({ per: { min: null, max: 1 } });
    const 말 = whyEmpty(조건, [행({ per: 12.5 }), 행({ per: 30 })]);

    expect(말[0]).toContain("값은 있는데 조건이 좁아");
    expect(말[0]).toContain("범위를 넓혀 보세요");
  });

  it("빈 칸이 여럿이면 다 짚는다", () => {
    const 조건 = screenerFilterSchema.parse({
      per: { min: null, max: 10 },
      roe: { min: 5, max: null },
    });
    const 말 = whyEmpty(조건, [행({ market_cap: 1 })]);

    expect(말[0]).toContain("PER");
    expect(말[0]).toContain("ROE");
  });

  it("한 칸이라도 값이 있으면 그 조건은 짚지 않는다", () => {
    // 값이 있는 조건을 "자료가 없다" 고 하면 그것도 오진이다
    const 조건 = screenerFilterSchema.parse({ per: { min: null, max: 10 } });
    const 말 = whyEmpty(조건, [행({ per: null }), 행({ per: 8 })]);

    expect(말[0]).not.toContain("비어 있습니다");
  });

  it("값 조건을 하나도 안 걸었으면 아무 말도 하지 않는다", () => {
    // 조건 없이 0건이면 위 첫 갈래(후보 0)가 이미 말한다. 여기서 또 말하면 겹친다
    expect(whyEmpty(기본, [행({ per: 1 })])).toEqual([]);
  });
});

describe("무엇을 값 조건으로 보나", () => {
  it("읽어 냈다", () => {
    expect(VALUE_FILTERS.length).toBeGreaterThanOrEqual(10);
  });

  it("건 것만 골라낸다", () => {
    const 조건 = screenerFilterSchema.parse({
      per: { min: null, max: 10 },
      sharpe: { min: 0.5, max: null },
    });
    expect(
      usedValueFilters(조건)
        .map((f) => f.key)
        .sort(),
    ).toEqual(["per", "sharpe"]);
    expect(usedValueFilters(기본)).toEqual([]);
  });

  it("값 조건을 빼면 나라·시장·업종만 남는다", () => {
    const 조건 = screenerFilterSchema.parse({
      country: "US",
      market: "NASDAQ",
      per: { min: 1, max: 10 },
      exclude_loss_making: true,
    });
    const 맨 = withoutValueFilters(조건);

    expect(맨.country).toBe("US");
    expect(맨.market).toBe("NASDAQ");
    expect(맨.per).toEqual({ min: null, max: null });
    expect(맨.exclude_loss_making, "적자 제외도 값 조건이다").toBe(false);
    expect(
      맨.universe_only,
      "유니버스 편입은 값이 아니라 범위다 — 그대로 둔다",
    ).toBe(조건.universe_only);
  });

  it("표의 field 가 실제 결과 칸 이름이다", () => {
    // 이름이 하나라도 틀리면 `undefined === null` 이 거짓이라 **늘 '값이 있다'** 로 읽힌다
    const 한행 = 행({});
    for (const f of VALUE_FILTERS) {
      expect(Object.prototype.hasOwnProperty.call(한행, f.field), f.field).toBe(
        true,
      );
    }
  });
});

describe("경로가 실제로 그 판정을 쓴다", () => {
  it("0건일 때 값 조건을 뺀 조회를 한 번 더 한다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 글 = readFileSync(
      join(process.cwd(), "app", "api", "screener", "route.ts"),
      "utf-8",
    );

    expect(글).toContain("withoutValueFilters(");
    expect(글).toContain("whyEmpty(");
    expect(글, "0건일 때만 더 조회해야 한다").toContain("rows.length === 0");
  });

  it("화면이 '범위를 넓혀 보세요' 를 조건 없이 말하지 않는다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 글 = readFileSync(
      join(process.cwd(), "components", "ScreenerForm.tsx"),
      "utf-8",
    );

    expect(글).toContain("notes.length === 0");
  });
});

describe("상한에 걸린 후보로는 '넓혀도 안 나온다' 고 단정하지 않는다 (docs/infra.md 25.363)", () => {
  const 필터 = screenerFilterSchema.parse({ sentiment: { min: 90, max: null }, limit: 3 });

  it("앞 N개가 모두 비어 있어도 뒤쪽에는 있을 수 있다", () => {
    const 후보 = [
      행({ stock_id: 1 }),
      행({ stock_id: 2 }),
      행({ stock_id: 3 }),
    ];
    const [말] = whyEmpty(필터, 후보);
    expect(말).not.toContain("넓혀도 나오지 않습니다");
    expect(말).toContain("정렬 뒤쪽");
  });

  it("상한보다 적으면 전체를 본 것이라 단정해도 된다", () => {
    const [말] = whyEmpty(필터, [행({ stock_id: 1 })]);
    expect(말).toContain("넓혀도 나오지 않습니다");
  });
});

/** 상한에 걸려 잘린 결과를 알린다 (docs/infra.md 25.771, 스크리너 감사) */
describe("상한 안내", () => {
  it("결과가 상한만큼이면 잘렸을 수 있다고 말한다", async () => {
    const { limitNote } = await import("@/lib/screener");
    expect(limitNote(100, 100)).toContain("상한 100종목에 걸렸습니다");
    expect(limitNote(99, 100)).toBeNull();
    expect(limitNote(0, null)).toBeNull();
  });
  it("화면이 안내를 보인다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync("components/ScreenerForm.tsx", "utf8");
    expect(src).toContain("limitNote(rows.length, 조회상한.current)");
    expect(src).toContain("조회상한.current = current.limit;");
  });
});

/** 틀린 "텔레그램도 같은 조건" 문구와 성과 지표 기간 표시 (docs/infra.md 25.772, 스크리너 감사) */
describe("스크리너 안내 정정", () => {
  it("텔레그램이 같은 조건이라고 말하지 않고, 성과 열에 기간을 붙인다", async () => {
    const { readFileSync } = await import("node:fs");
    const page = readFileSync("app/screener/page.tsx", "utf8");
    expect(page).not.toContain("텔레그램으로 오는 목록도 같은 조건에서");
    const form = readFileSync("components/ScreenerForm.tsx", "utf8");
    expect(form).toContain("label: `CAGR${기간}`");
    expect(form).toContain("resultColumns(kr, money, 조회기간.current)");
  });
});


/** MDD 조건에 음수를 받지 않는다 (docs/infra.md 25.773, 교차검증) */
describe("MDD 음수 입력", () => {
  it("거부한다", async () => {
    const { screenerFilterSchema } = await import("@/lib/screener");
    expect(screenerFilterSchema.safeParse({ country: "KR", mdd: { min: null, max: -30 } }).success).toBe(false);
    expect(screenerFilterSchema.safeParse({ country: "KR", mdd: { min: null, max: 30 } }).success).toBe(true);
  });
});

/** 옛 음수 MDD 프리셋을 낙폭 크기로 옮겨 읽는다 (docs/infra.md 25.774, 교차검증) */
describe("옛 MDD 프리셋", () => {
  it("음수 조건은 경계를 뒤집어 크기로 옮기고 나머지 조건은 그대로 쓴다", async () => {
    const { migrateOldMdd, parsePreset } = await import("@/lib/screener");
    expect(migrateOldMdd({ min: -30, max: null })).toEqual({ min: null, max: 30 });
    expect(migrateOldMdd({ min: -50, max: -20 })).toEqual({ min: 20, max: 50 });
    expect(migrateOldMdd({ min: -90, max: 0 })).toEqual({ min: 0, max: 90 });
    expect(migrateOldMdd({ min: 10, max: 30 })).toEqual({ min: 10, max: 30 });
    // 25.776: 부호가 섞인 옛 값(≤ 20 은 늘 참) · 옛 {max:0}(값 있는 모든 종목)
    expect(migrateOldMdd({ min: -10, max: 20 })).toEqual({ min: null, max: 10 });
    expect(migrateOldMdd({ min: null, max: 0 })).toEqual({ min: 0, max: null });
    const p = parsePreset({
      id: 1, name: "옛", country: "KR", created_at: "t", last_used_at: null,
      filters_json: JSON.stringify({ mdd: { min: -30, max: null }, per: { min: null, max: 10 } }),
    } as never);
    expect(p?.mdd).toEqual({ min: null, max: 30 });
    expect(p?.per).toEqual({ min: null, max: 10 });
  });
});

/** CSV 성과 열에도 기간을 붙인다 (docs/infra.md 25.777) */
describe("CSV 기간", () => {
  it("CAGR·MDD·샤프·변동성 열 이름에 기간", async () => {
    const { toCsv } = await import("@/lib/screener");
    const head = toCsv([], "3Y").replace("﻿", "").split("\n")[0];
    expect(head).toContain("CAGR(3Y)(%)");
    expect(head).toContain("MDD(3Y)(%)");
    expect(head).toContain("샤프(3Y)");
    expect(head).toContain("변동성(3Y)(%)");
    expect(toCsv([]).includes("CAGR(%)")).toBe(true); // 기간을 모르면 예전 그대로
  });
  it("성과 지표 기준일 열이 있다 (25.779, 교차검증)", async () => {
    const { toCsv } = await import("@/lib/screener");
    expect(toCsv([]).split("\n")[0]).toContain("성과 지표 기준일");
  });
});

describe("성과 지표가 빈 까닭 (25.779)", () => {
  it("기준일이 없으면 그 기간이 한 번도 계산되지 않았다고 말한다", async () => {
    const { metricsEmptyNote } = await import("@/lib/screener");
    const 없음 = metricsEmptyNote([{ cagr: null, data_points: null, metrics_asof: null }], "5Y");
    expect(없음).toContain("5Y 성과 지표는 이 나라에서 아직 한 번도 계산되지 않았습니다");
    const 모자람 = metricsEmptyNote([{ cagr: null, data_points: 120, metrics_asof: "2026-09-29" }], "3Y");
    expect(모자람).toContain("표본이 모자랍니다 (현재 120 거래일)");
    expect(metricsEmptyNote([{ cagr: 0.1, data_points: 700, metrics_asof: "2026-09-29" }], "3Y")).toBeNull();
    expect(metricsEmptyNote([], "3Y")).toBeNull();
  });
});
