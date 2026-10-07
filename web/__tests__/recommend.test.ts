/**
 * 추천 조회 테스트.
 *
 * 화면이 조건을 정하지 않는 대신, 어떤 행을 읽을지는 질의가 정한다.
 * 과거 신호가 섞이지 않는지와 안전하게 푸는지를 여기서 고정한다.
 */

import { describe, expect, it } from "vitest";
import {
  STALE_AFTER_DAYS,
  buildQuery,
  daysSince,
  formatKst,
  groupByHorizon,
  isStale,
  parseCriteria,
  parseFactors,
  parseTranches,
  recommendQuerySchema,
  reductionNote,
  type RecommendRow,
} from "@/lib/recommend";
import { diffSignals, outcomeLine, signalKey } from "@/lib/recommend";

const base = recommendQuerySchema.parse({});

describe("질의", () => {
  it("기준일은 부르는 쪽이 정해 넘긴다", () => {
    // **전에는 여기서 `signals` 의 MAX 를 박았다** (docs/infra.md 25.145).
    // 한 건도 안 걸린 날에는 그 표에 새 행이 없어 **어제 신호가 오늘 것처럼** 나왔다
    const { sql } = buildQuery(base, "2026-09-16");
    expect(sql).toContain("sg.as_of_date = ?");
    expect(sql).not.toContain("MAX(sg2.as_of_date)");
  });

  it("조건 없이도 돈다", () => {
    const { args } = buildQuery(base, "2026-09-16");
    // 나라·날짜 두 번: 판 하위질의가 인자로 받는다 (25.428). 기본 500 + 잘렸는지 볼 한 건 (25.377)
    expect(args).toEqual(["KR", "2026-09-16", "KR", "2026-09-16", 501]);
  });

  it("시장과 기간을 거를 수 있다", () => {
    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({
        market: "KOSPI",
        horizon: "long",
        limit: 5,
      }),
      "2026-09-16",
    );
    expect(sql).toContain("s.market = ?");
    expect(sql).toContain("sg.horizon = ?");
    expect(args).toEqual(["KR", "2026-09-16", "KR", "2026-09-16", "KOSPI", "long", 6]);
  });

  it("인자는 전부 바인딩으로 넘긴다", () => {
    const { sql } = buildQuery(
      recommendQuerySchema.parse({ market: "KOSDAQ" }),
      "2026-09-16",
    );
    expect(sql).not.toContain("KOSDAQ");
  });

  it("상한을 넘는 요청은 거부한다", () => {
    expect(recommendQuerySchema.safeParse({ limit: 501 }).success).toBe(false);
    // 시장은 그 나라의 것만 (docs/infra.md 25.315)
    expect(
      recommendQuerySchema.safeParse({ country: "US", market: "KOSPI" })
        .success,
    ).toBe(false);
    expect(
      recommendQuerySchema.safeParse({ country: "US", market: "NASDAQ" })
        .success,
    ).toBe(true);
    expect(
      recommendQuerySchema.safeParse({ country: "KR", market: "NYSE" }).success,
    ).toBe(false);
  });
});

describe("안전하게 풀기", () => {
  it("분할 계획을 푼다", () => {
    const plan = parseTranches(
      '[{"step":1,"ratio":0.4,"price":100,"amount":null}]',
    );
    expect(plan).toHaveLength(1);
    expect(plan[0].ratio).toBe(0.4);
  });

  it("깨진 JSON이어도 화면이 무너지지 않는다", () => {
    expect(parseTranches("{망가짐")).toEqual([]);
    expect(parseTranches(null)).toEqual([]);
    expect(parseFactors("[")).toEqual({});
  });

  it("팩터 점수를 푼다", () => {
    expect(parseFactors('{"value":83,"risk":null}')).toEqual({
      value: 83,
      risk: null,
    });
  });
});

describe("비중이 줄어든 이유", () => {
  it("줄지 않았으면 아무 말도 하지 않는다", () => {
    expect(reductionNote(1.0)).toBeNull();
  });

  it("배치가 null 로 둔 1% 미만 축소는 웹도 말하지 않는다 (25.689, 교차검증)", () => {
    const 근거 = JSON.stringify({ volatility_factor: 0.996, reduction_note: null });
    expect(reductionNote(0.996, 근거)).toBeNull();
    // 키가 없는 옛 행은 예전처럼 옛 방식
    expect(reductionNote(0.5, JSON.stringify({ volatility_factor: 0.5 }))).toContain("50%");
  });

  it("줄었으면 얼마나 줄었는지 밝힌다", () => {
    // 왜 추천인데 금액이 작은가에 답하지 않으면 추천 자체를 의심하게 된다
    expect(reductionNote(0.5)).toContain("50%");
  });

  it("줄인 까닭을 실제 배수에서 읽는다 — 시장 국면만 줄였으면 종목 탓을 하지 않는다 (25.233)", () => {
    const 국면만 = JSON.stringify({
      volatility_factor: 1,
      mdd_factor: 1,
      regime_factor: 0.5,
    });
    const 글 = reductionNote(0.5, 국면만)!;
    expect(글).toContain("시장 약세");
    expect(글).not.toContain("변동성");
    expect(글).not.toContain("낙폭");
  });

  it("종목 위험으로 줄였으면 그것을 말한다", () => {
    const 글 = reductionNote(
      0.6,
      JSON.stringify({
        volatility_factor: 0.6,
        mdd_factor: 0.8,
        regime_factor: 1,
      }),
    )!;
    expect(글).toContain("변동성·낙폭");
    expect(글).not.toContain("시장 약세");
  });

  it("근거를 못 읽으면 까닭을 지어내지 않는다", () => {
    const 글 = reductionNote(0.5, "{깨짐")!;
    expect(글).not.toMatch(/변동성|낙폭|시장/);
    expect(글).toContain("50%");
  });
});

describe("기간별 묶기", () => {
  const row = (horizon: string): RecommendRow =>
    ({ horizon, stock_id: 1 }) as RecommendRow;

  it("단기 중기 장기 순으로 묶는다", () => {
    const groups = groupByHorizon([row("long"), row("short"), row("mid")]);
    expect(groups.map((g) => g.horizon)).toEqual(["short", "mid", "long"]);
  });

  it("비어 있는 기간은 내보내지 않는다", () => {
    const groups = groupByHorizon([row("short")]);
    expect(groups).toHaveLength(1);
  });
});

describe("근거표", () => {
  const sample = JSON.stringify({
    ma20: 74200,
    criteria: [
      {
        label: "정배열",
        display: "20일선 74,200원 > 60일선 71,900원",
        threshold: "MA20 > MA60",
        source: "prices (시세)",
        as_of: "2026-09-15",
        passed: true,
      },
    ],
  });

  it("배치가 만든 근거표를 그대로 푼다", () => {
    const rows = parseCriteria(sample);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe("정배열");
    expect(rows[0].as_of).toBe("2026-09-15");
  });

  it("근거표가 없으면 빈 목록이다", () => {
    // 옛 행에는 criteria 가 없다. 그때는 근거 보기를 그리지 않는다
    expect(parseCriteria(JSON.stringify({ ma20: 1 }))).toEqual([]);
    expect(parseCriteria(null)).toEqual([]);
    expect(parseCriteria("{깨짐")).toEqual([]);
  });

  it("모양이 어긋난 행은 버린다", () => {
    const broken = JSON.stringify({ criteria: [{ label: "x" }, null, 3] });
    expect(parseCriteria(broken)).toEqual([]);
  });

  it("질의가 근거표 열을 읽는다", () => {
    const { sql } = buildQuery(base, "2026-09-16");
    expect(sql).toContain("sg.rationale_data");
  });
});

describe("오래된 값", () => {
  const today = new Date("2026-09-16T12:00:00Z");

  it("며칠 지났는지 센다", () => {
    expect(daysSince("2026-09-15", today)).toBe(1);
    expect(daysSince("2026-09-16", today)).toBe(0);
  });

  it("오늘은 사용자의 날짜(KST)로 센다 — UTC 자정 전의 한국 아침 (25.236)", () => {
    // 화 08:40 KST = 월 23:40 UTC. 월요일 기준값은 한국에서 이미 하루 전이다
    const 화요일_아침 = new Date("2026-09-14T23:40:00Z");
    expect(daysSince("2026-09-14", 화요일_아침)).toBe(1);
  });

  it("날짜가 아닌 기준일은 건너뛴다", () => {
    // "2025 사업보고서" 는 오래됐다고 표시할 대상이 아니다
    expect(daysSince("2025 사업보고서", today)).toBeNull();
    expect(daysSince(null, today)).toBeNull();
    expect(isStale("2025 사업보고서", today)).toBe(false);
  });

  it("주간 배치 한 주기는 정상이다", () => {
    expect(isStale("2026-09-09", today)).toBe(false); // 7일
  });

  it("문턱을 넘기면 오래된 값이다", () => {
    const old = new Date(today);
    old.setUTCDate(old.getUTCDate() - (STALE_AFTER_DAYS + 1));
    expect(isStale(old.toISOString().slice(0, 10), today)).toBe(true);
  });
});

describe("마지막 배치 시각", () => {
  it("KST 로 보여준다", () => {
    // 12:41 UTC = 21:41 KST
    expect(formatKst("2026-09-16T12:41:52Z")).toContain("21:41");
  });

  it("없으면 없다", () => {
    expect(formatKst(null)).toBeNull();
  });
});

describe("어제와 비교", () => {
  it("새로 들어온 키와 빠진 종목을 나눈다", () => {
    const before = [
      { stock_id: 1, horizon: "mid", ticker: "A", name: "가" },
      { stock_id: 2, horizon: "short", ticker: "B", name: "나" },
    ];
    const today = [
      { stock_id: 1, horizon: "mid" },
      { stock_id: 3, horizon: "long" },
    ];
    const d = diffSignals(today, before);
    expect(d.added).toEqual(["3:long"]);
    expect(d.dropped.map(signalKey)).toEqual(["2:short"]);
  });

  it("같은 종목이라도 기간이 다르면 다른 신호다", () => {
    const d = diffSignals(
      [{ stock_id: 1, horizon: "short" }],
      [{ stock_id: 1, horizon: "mid", ticker: "A", name: "가" }],
    );
    expect(d.added).toEqual(["1:short"]);
    expect(d.dropped).toHaveLength(1);
  });

  it("전 기준일이 없으면 둘 다 비어 있다", () => {
    expect(diffSignals([{ stock_id: 1, horizon: "mid" }], [])).toEqual({
      added: ["1:mid"],
      dropped: [],
    });
  });
});

describe("신호 성적 한 줄", () => {
  const base = {
    horizon: "short",
    window_days: 20,
    n: 31,
    avg_ret: 0.032,
    win_rate: 0.58,
    avg_excess: 0.01,
    hit_target_rate: null,
    hit_stop_rate: null,
    since: "2026-01-02",
    computed_at: "2026-09-17T00:00:00",
  };
  it("평균·이긴 비율·초과를 적는다", () => {
    expect(outcomeLine(base)).toBe(
      "단기 20일 뒤: 평균 +3.2% · 이긴 58% · 지수 대비 +1.0%p (n=31)",
    );
  });
  it("반올림으로 끝값을 만들지 않고, 부호와 작은 표본을 적는다 (25.699)", () => {
    const 줄 = outcomeLine({ ...base, n: 7, avg_ret: -0.0004, win_rate: 0.996, avg_excess: 0.003 });
    expect(줄).toContain("평균 0.0%");
    expect(줄).not.toContain("-0.0");
    expect(줄).toContain("이긴 99% 넘게");
    expect(줄).toContain("표본이 적어 단정하지 않음");
    expect(outcomeLine({ ...base, hit_target_rate: 0.004, hit_stop_rate: 0 })).toContain("목표 도달 1% 미만 · 손절 터치 0%");
  });
  it("표본이 모자라면 그렇다고 말한다", () => {
    expect(
      outcomeLine({ ...base, n: 3, avg_ret: null, win_rate: null }),
    ).toContain("표본 3건");
  });
  it("터치 비율은 있을 때만", () => {
    expect(
      outcomeLine({
        ...base,
        window_days: 60,
        hit_target_rate: 0.4,
        hit_stop_rate: 0.1,
      }),
    ).toContain("목표 도달 40% · 손절 터치 10%");
  });
});

it("추천 카드의 권장 비중은 2부 반영 전이라고 밝힌다 (25.272)", async () => {
  const { readFileSync } = await import("node:fs");
  const 글 = readFileSync(
    `${process.cwd()}/components/RecommendList.tsx`,
    "utf-8",
  );
  // 리포트는 상위 몇 종목만 싣는다 — 그 밖의 카드가 "2부에 있다" 고 하지 않는다 (25.586)
  expect(글).toContain("반영 전 — 리포트는 시장별 상위 {REPORT_TOP_STOCKS}종목만 싣고 2부에서 반영합니다");
});

it("리포트 종목 수가 배치와 같다 (25.586)", async () => {
  const { readFileSync } = await import("node:fs");
  const { REPORT_TOP_STOCKS } = await import("@/lib/recommend");
  const 배치 = readFileSync(`${process.cwd()}/../batch/services/report_picks.py`, "utf-8");
  expect(배치).toMatch(new RegExp(`^TOP_STOCKS = ${REPORT_TOP_STOCKS}$`, "m"));
});

it("리포트·추천 화면은 늦은 응답·실패로 다른 시장 것을 남기지 않는다 (25.586)", async () => {
  const { readFileSync } = await import("node:fs");
  const 추천 = readFileSync(`${process.cwd()}/components/RecommendList.tsx`, "utf-8");
  expect(추천).toContain("if (번호 !== 조회번호.current) return;");
  const 리포트 = readFileSync(`${process.cwd()}/components/ReportView.tsx`, "utf-8");
  expect(리포트).toContain("setData((d) => (d ? { ...d, report: null, items: [], notes: [], missed_reports: null } : d));");
  expect(리포트).toMatch(/setDate\(null\);\s*\/\/[^\n]*\n\s*setData\(null\);/);
});

describe("추천 목록을 조용히 자르지 않는다 (docs/infra.md 25.377)", () => {
  it("기본이 다 보이는 크기이고, 경로가 잘렸는지 말한다", async () => {
    expect(recommendQuerySchema.parse({}).limit).toBe(500);
    const { readFileSync } = await import("node:fs");
    const src = readFileSync("app/api/recommend/route.ts", "utf8");
    expect(src).toContain("const truncated = 읽은.length > parsed.data.limit;");
    expect(src).toContain("건만 보입니다");
  });
});

describe("비중 축소 까닭 (docs/infra.md 25.624)", () => {
  it("배치가 쓴 한 줄이 있으면 그대로 보이고 계산하지 않는다", async () => {
    const { reductionNote } = await import("@/lib/recommend");
    const 근거 = JSON.stringify({ volatility_factor: 0.8, mdd_factor: 0.5, regime_factor: 0.5, reduction_note: "낙폭·시장 약세 때문에 비중을 75% 줄였습니다 (배수 ×0.25)" });
    expect(reductionNote(0.25, 근거)).toBe("낙폭·시장 약세 때문에 비중을 75% 줄였습니다 (배수 ×0.25)");
  });
});
