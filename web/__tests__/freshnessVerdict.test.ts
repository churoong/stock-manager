import { describe, expect, it } from "vitest";
import {
  FRESHNESS,
  FX_STALE_DAYS,
  MAX_AS_OF_AGE_DAYS,
  MAX_SESSION_GAP_DAYS,
  STALE_AFTER_DAYS,
  freshnessVerdict,
} from "@/lib/health";

/**
 * 신선도 칸의 **판정** (docs/infra.md 25.140).
 *
 * **있었던 일.** `/status` 의 "데이터 신선도" 는 날짜 스물여섯 개를 격자에 늘어놓기만 했다.
 * 칸마다 "며칠이면 늦은 것인가" 가 다른데(시세는 사흘, 배당은 반년) 그 잣대는 아무 데도
 * 없었다. 사람이 스물여섯 번 암산해야 하는 화면은 **아무도 읽지 않는다** — 25.139 에서
 * 다섯 칸을 더해 더 나빠졌다.
 *
 * 그리고 하나 더: 무응답 감시는 "D1 에서는 미국을 안 본다" 를 이미 알고 있었는데
 * (`dueAlerts` 의 `markets`, 25.14) **화면만 몰랐다.** 쉬기로 한 것을 빨갛게 칠하면
 * 거짓 경보이고, 거짓 경보가 쌓이면 진짜로 멈춘 날에도 사람이 안 읽는다.
 */

const 어느날 = new Date("2026-09-23T00:30:00.000Z"); // 한국 09-23 09:30

function 며칠전(n: number): string {
  const d = new Date(Date.parse("2026-09-23T00:00:00Z") - n * 86_400_000);
  return d.toISOString().slice(0, 10);
}

describe("얼마나 묵었나", () => {
  it("문턱 안이면 초록", () => {
    const v = freshnessVerdict("prices_kr", 며칠전(3), 어느날, "d1");
    expect(v.tone).toBe("ok");
    expect(v.text).toBe("3일 전");
  });

  it("문턱을 넘으면 노랑 — 한 번 거른 것은 흔하다", () => {
    const v = freshnessVerdict("prices_kr", 며칠전(MAX_AS_OF_AGE_DAYS + 1), 어느날, "d1");
    expect(v.tone).toBe("warn");
  });

  it("두 배를 넘으면 빨강 — 두 번 걸렀으면 멈춘 것이다", () => {
    const v = freshnessVerdict("prices_kr", 며칠전(MAX_AS_OF_AGE_DAYS * 2 + 1), 어느날, "d1");
    expect(v.tone).toBe("bad");
  });

  it("긴 연휴 끝의 정상 나이(연속 세 거래일 폭, 12일)는 초록이다 (25.242)", () => {
    // XKRX 2017-09-29 기준 시세가 10-11 아침 배치 전까지 남는다
    expect(freshnessVerdict("prices_kr", 며칠전(12), 어느날, "d1").tone).toBe("ok");
  });

  it("오늘 것은 '오늘' 이라고 적는다", () => {
    expect(freshnessVerdict("prices_kr", 며칠전(0), 어느날, "d1").text).toBe("오늘");
  });

  it("한 번도 안 채워졌으면 빨강", () => {
    const v = freshnessVerdict("index_prices_kr", null, 어느날, "turso");
    expect(v.tone).toBe("bad");
    expect(v.text).toBe("없음");
  });

  it("칸마다 잣대가 다르다 — 같은 날짜가 시세는 빨강, 배당은 초록", () => {
    // **이 검사가 이 파일의 이유다.** 하나의 문턱으로 스물여섯 칸을 재면 둘 중 하나가 거짓말한다
    const 백일전 = 며칠전(100);
    expect(freshnessVerdict("prices_kr", 백일전, 어느날, "turso").tone).toBe("bad");
    expect(freshnessVerdict("dividends_kr", 백일전, 어느날, "turso").tone).toBe("ok");
  });

  it("환율은 배치의 실측 상수를 쓴다", () => {
    expect(FX_STALE_DAYS).toBeLessThan(MAX_SESSION_GAP_DAYS);
    expect(freshnessVerdict("fx", 며칠전(FX_STALE_DAYS), 어느날, "turso").tone).toBe("ok");
    expect(freshnessVerdict("fx", 며칠전(FX_STALE_DAYS + 1), 어느날, "turso").tone).toBe("warn");
  });
});

describe("쉬기로 한 것을 고장이라고 부르지 않는다", () => {
  it("D1 에서 미국 칸은 '쉬는 중'", () => {
    const v = freshnessVerdict("prices_us", 며칠전(200), 어느날, "d1");
    expect(v.tone).toBe("mute");
    expect(v.text).toBe("쉬는 중");
    expect(v.why).toContain("25.14");
  });

  it("쉬는 중에도 마지막 기준일은 감추지 않는다", () => {
    // "쉬는 중" 만 적고 날짜를 지우면 **언제까지 채워졌는지**를 잃는다.
    // Turso 로 돌아가는 날 어디부터 백필할지가 그 날짜다 (scripts/turso_return.py)
    expect(freshnessVerdict("prices_us", "2026-09-17", 어느날, "d1").why).toContain("2026-09-17");
  });

  it("Turso 로 돌아가면 같은 칸을 다시 판정한다", () => {
    const v = freshnessVerdict("prices_us", 며칠전(200), 어느날, "turso");
    expect(v.tone).toBe("bad");
  });

  it("국내 칸은 D1 에서도 판정한다", () => {
    // 쉬는 것은 미국이다. 국내까지 묵음 처리하면 이 화면이 통째로 조용해진다
    expect(freshnessVerdict("prices_kr", 며칠전(200), 어느날, "d1").tone).toBe("bad");
  });
});

describe("판정하지 않기로 한 칸", () => {
  it("거래일 달력은 미래가 정상이라 여기서 재지 않는다", () => {
    const 앞날 = new Date(Date.parse("2026-09-23T00:00:00Z") + 9 * 86_400_000).toISOString().slice(0, 10);
    const v = freshnessVerdict("sessions_kr", 앞날, 어느날, "turso");
    expect(v.tone).toBe("mute");
    expect(v.text).toBe("앞으로 9일");
    // 남은 **거래일** 로 보는 규칙이 이미 있다. 달력일로 또 재면 두 잣대가 어긋난다
    expect(v.why).toContain("25.104");
  });

  it("수정주가 칸은 실행 기록을 본다 — 채워진 마지막 날은 마지막 기업행위의 전날일 뿐이다 (25.924)", () => {
    const f = FRESHNESS.find((x) => x.key === "adj_close_kr")!;
    expect(f.sql).toContain("FROM batch_runs");
    expect(f.sql).toContain("job_name = 'adjust_kr'");
    expect(f.sql).not.toContain("adj_close IS NOT NULL");
  });

  it("수정주가는 예약이 없다 — 늦었다고 말할 잣대가 없다", () => {
    const v = freshnessVerdict("adj_close_kr", 며칠전(40), 어느날, "turso");
    expect(v.tone).toBe("mute");
    expect(v.why).toContain("25.138");
  });

  it("모르는 키는 지어내지 않는다", () => {
    expect(freshnessVerdict("없는칸", "2026-09-01", 어느날, "turso").tone).toBe("mute");
  });
});

describe("모든 칸이 판정을 받는다", () => {
  it("읽어 냈다", () => {
    expect(FRESHNESS.length).toBeGreaterThanOrEqual(20);
  });

  it("every 가 빠진 칸이 없다", () => {
    // 새 칸을 더하면서 `every` 를 빼면 타입이 막아 준다. 값 자체가 잣대에 있는지는 여기서 본다
    const 잣대없음 = FRESHNESS.filter(
      (f) => !["ahead", "manual"].includes(f.every) && !(f.every in STALE_AFTER_DAYS),
    );
    expect(잣대없음.map((f) => f.key)).toEqual([]);
  });

  it("근거에 docs 파일 경로를 적지 않는다", () => {
    // 이 글은 화면의 툴팁으로 나간다. **폰에서 저장소 파일을 열 수 없다**
    // (`nav.test.ts` 의 같은 규칙과 한뜻이다). 절 번호만 남긴다
    for (const f of FRESHNESS) {
      for (const 값 of [null, "2026-09-23", "2020-01-01"]) {
        expect(freshnessVerdict(f.key, 값, 어느날, "d1").why, f.key).not.toMatch(/docs\/[a-z_]+\.md/);
      }
    }
  });

  it("어떤 칸도 판정에서 예외를 던지지 않는다", () => {
    for (const f of FRESHNESS) {
      for (const 값 of [null, "2026-09-23", "2020-01-01", "이상한값"]) {
        const v = freshnessVerdict(f.key, 값, 어느날, "d1");
        expect(v.why.length, `${f.key} 의 근거가 비었다`).toBeGreaterThan(5);
      }
    }
  });
});

/**
 * **나라를 가린 칸을 잡는다** (docs/infra.md 25.182).
 *
 * 2026-09-21 에 점수·신호·유니버스를 나라별로 나눴다. 이유는 이랬다.
 *
 * > 전체 MAX 로 한 줄만 보여 주면 **국내만 보고 "최신" 이라 말하고 미국이 몇 달 멈춘
 * > 것을 가린다.**
 *
 * 그때 나눈 것은 셋이었고, 그 뒤에 더한 칸들은 다시 전체 MAX 였다 — `accum_picks`·
 * `etf_profiles`·`earnings_calendar`·`signal_outcomes`·`index_prices`·`valuation_bands`.
 * 이유를 적어 두고도 새 칸에는 안 물은 것이다.
 *
 * 그래서 **이유가 아니라 그물로** 지킨다. 나라 차원이 있는 표를 읽는 칸은 나라를
 * 걸러야 하고, 안 거르려면 **왜 안 걸러도 되는지**를 적어야 한다.
 */
describe("나라를 가리는 칸이 없다", () => {
  /** 나라(또는 시장) 차원이 있는 표. 이 표를 읽으면 한 줄로 두 시장을 덮는다 */
  const 나라별_표 = [
    "stocks", "prices", "scores", "signals", "universe_members", "financials",
    "stock_dividends", "performance_metrics", "stock_accum_picks", "etf_profiles",
    "etf_picks", "etf_satellite_picks", "market_sessions", "monitor_targets",
    "valuation_bands", "index_prices", "signal_outcome_stats", "earnings_calendar",
    "disclosures", "insider_trades", "factors", "sentiment_scores",
  ];

  /**
   * 나라를 안 걸러도 되는 칸과 **왜인지**.
   *
   * 2026-09-26 까지 `sentiment` 가 여기 있었다 — "두 시장을 같은 크론이 채우니 크론 판정이 먼저 빨개진다". 틀렸다.
   * 크론은 `news` 만 쓰고 `sentiment_scores` 는 `jobs/sentiment` 가 시장마다 쓴다 (docs/infra.md 25.247)
   */
  const 안_걸러도_되는_칸: Record<string, string> = {
    adj_close_kr: "이름 그대로 국내 전용 작업(`adjust_kr`)의 실행 기록이다. 미국 수정주가는 `refresh_us_adjusted` 가 따로 채우고 `us_adjust_queue` 칸이 본다",
  };

  it("읽어 냈다", () => {
    expect(FRESHNESS.length).toBeGreaterThanOrEqual(30);
  });

  it.each(FRESHNESS.map((f) => f.key))("%s 가 나라를 가리지 않는다", (key) => {
    const f = FRESHNESS.find((x) => x.key === key)!;
    const 닿는표 = 나라별_표.filter((t) => new RegExp(`\\b${t}\\b`).test(f.sql));
    if (닿는표.length === 0) return;

    const 사유 = 안_걸러도_되는_칸[key];
    if (사유) {
      expect(사유.length, `${key} 의 사유가 너무 짧다`).toBeGreaterThan(20);
      return;
    }

    expect(
      /country = '|market = '|index_code /.test(f.sql),
      `${key}(${f.label}) 가 ${닿는표} 를 읽으면서 나라를 안 거른다.\n` +
        "국내만 채워져도 '최신' 으로 보인다 — 나라별로 나누거나 `안_걸러도_되는_칸` 에 사유를 적어라",
    ).toBe(true);
  });

  it("사유 목록이 낡지 않았다", () => {
    const 있는것 = new Set(FRESHNESS.map((f) => f.key));
    expect(Object.keys(안_걸러도_되는_칸).filter((k) => !있는것.has(k))).toEqual([]);
  });

  it("나뉜 칸은 쌍으로 있다", () => {
    // 한쪽만 만들면 다른 시장이 통째로 화면에서 사라진다
    const keys = new Set(FRESHNESS.map((f) => f.key));
    const 짝없음 = [...keys]
      .filter((k) => k.endsWith("_kr"))
      .filter((k) => !안_걸러도_되는_칸[k])
      .filter((k) => !keys.has(`${k.slice(0, -3)}_us`));

    expect(짝없음, "국내 칸만 있고 미국 칸이 없다").toEqual([]);
  });

  it("미국 칸은 D1 에서 쉬는 것을 안다", () => {
    // 미국은 통째로 멈춰 있다(25.14). 모르면 D1 운영 내내 빨간 칸이 늘어선다
    const 모르는것 = FRESHNESS.filter((f) => f.key.endsWith("_us") && !f.restsOnD1);
    expect(모르는것.map((f) => f.key)).toEqual([]);
  });
});
