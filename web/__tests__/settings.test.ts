import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_SETTINGS,
  SETTINGS_KEYS,
  UNUSED_SETTING_REASON,
  checkWeightConsistency,
  missingCriticalValues,
  validateSettings,
  type Settings,
} from "@/lib/settings";

function withDefaults(patch: Record<string, unknown> = {}): Settings {
  return structuredClone({ ...DEFAULT_SETTINGS, ...patch }) as Settings;
}

describe("기본값", () => {
  it("기본값 자체는 스키마를 통과한다", () => {
    const result = validateSettings(DEFAULT_SETTINGS);
    expect(result.ok).toBe(true);
  });

  it("수수료와 세율은 비어 있다", () => {
    // 증권사마다 달라 임의의 숫자를 넣으면 실현손익이 조용히 틀린다
    expect(DEFAULT_SETTINGS.fees.kr_buy_pct).toBeNull();
    expect(DEFAULT_SETTINGS.taxes.kr_transaction_pct).toBeNull();
  });

  it("비어 있는 값을 경고로 알려준다", () => {
    const result = validateSettings(DEFAULT_SETTINGS);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.warnings.length).toBeGreaterThan(0);
      expect(result.warnings[0]).toContain("국내 매수 수수료");
    }
  });

  it("추세 필터는 켜져 있고 약세 배수는 0.5 다 (batch/services/trend.py 와 같은 값)", () => {
    expect(DEFAULT_SETTINGS.trend_filter).toEqual({ enabled: true, bear_factor: 0.5 });
  });

  it("약세 배수는 0~1 이어야 한다", () => {
    const bad = withDefaults({ trend_filter: { enabled: true, bear_factor: 1.5 } });
    expect(validateSettings(bad).ok).toBe(false);
    const zero = withDefaults({ trend_filter: { enabled: true, bear_factor: 0 } });
    expect(validateSettings(zero).ok).toBe(true);
  });

  it("팩터 다섯 개가 균등하게 100이다", () => {
    const w = DEFAULT_SETTINGS.factor_weights;
    expect(w.value + w.quality + w.growth + w.momentum + w.risk).toBe(100);
  });
});

describe("팩터 가중치", () => {
  it("합이 100이 아니면 거부한다", () => {
    const bad = withDefaults({
      factor_weights: { value: 30, quality: 20, growth: 20, momentum: 20, risk: 20 },
    });
    const result = validateSettings(bad);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.join(" ")).toContain("100");
    }
  });

  it("합이 100이면 배분이 달라도 받는다", () => {
    const ok = withDefaults({
      factor_weights: { value: 40, quality: 30, growth: 10, momentum: 10, risk: 10 },
    });
    expect(validateSettings(ok).ok).toBe(true);
  });

  it("음수 가중치는 거부한다", () => {
    const bad = withDefaults({
      factor_weights: { value: -10, quality: 30, growth: 30, momentum: 30, risk: 20 },
    });
    expect(validateSettings(bad).ok).toBe(false);
  });

  it("소수점 반올림 오차는 허용한다", () => {
    const ok = withDefaults({
      factor_weights: {
        value: 33.33,
        quality: 33.33,
        growth: 33.34,
        momentum: 0,
        risk: 0,
      },
    });
    expect(validateSettings(ok).ok).toBe(true);
  });
});

describe("센티먼트 가중치", () => {
  it("0이면 감성을 끄는 것이고 유효하다", () => {
    expect(validateSettings(withDefaults({ sentiment_weight: 0 })).ok).toBe(true);
  });

  it("50을 넘으면 거부한다", () => {
    // 뉴스 감성 하나로 종합 점수가 ±25점 넘게 움직이는 것은 설계 의도가 아니다 (25.639 식)
    const result = validateSettings(withDefaults({ sentiment_weight: 60 }));
    expect(result.ok).toBe(false);
  });
});

describe("기간별 목표와 손절", () => {
  it("손절선이 양수면 거부한다", () => {
    const bad = withDefaults({
      horizon_targets: {
        ...DEFAULT_SETTINGS.horizon_targets,
        short: { target_pct: 10, stop_pct: 7 },
      },
    });
    expect(validateSettings(bad).ok).toBe(false);
  });

  it("목표수익률이 0이면 거부한다", () => {
    const bad = withDefaults({
      horizon_targets: {
        ...DEFAULT_SETTINGS.horizon_targets,
        mid: { target_pct: 0, stop_pct: -15 },
      },
    });
    expect(validateSettings(bad).ok).toBe(false);
  });

  it("기본값은 기간이 길수록 목표와 손절 폭이 크다", () => {
    const t = DEFAULT_SETTINGS.horizon_targets;
    expect(t.short.target_pct).toBeLessThan(t.mid.target_pct);
    expect(t.mid.target_pct).toBeLessThan(t.long.target_pct);
    expect(t.short.stop_pct).toBeGreaterThan(t.mid.stop_pct);
  });
});

describe("비중 상한", () => {
  it("종목 상한이 섹터 상한보다 크면 거부한다", () => {
    const bad = withDefaults({
      max_weight_per_stock: 40,
      max_weight_per_sector: 30,
    });
    const result = validateSettings(bad);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.join(" ")).toContain("섹터");
    }
  });

  it("같으면 허용한다", () => {
    const ok = withDefaults({ max_weight_per_stock: 30, max_weight_per_sector: 30 });
    expect(validateSettings(ok).ok).toBe(true);
  });

  it("0은 거부한다", () => {
    expect(validateSettings(withDefaults({ max_weight_per_stock: 0 })).ok).toBe(false);
  });
});

describe("조용시간", () => {
  it("HH:MM 형식이 아니면 거부한다", () => {
    const bad = withDefaults({
      quiet_hours: { enabled: true, start: "25:00", end: "07:00", deliver_on_release: true },
    });
    expect(validateSettings(bad).ok).toBe(false);
  });

  it("자정을 넘는 구간을 허용한다", () => {
    // 00:00~07:00 처럼 날짜를 넘는 것이 기본이다
    const ok = withDefaults({
      quiet_hours: { enabled: true, start: "23:00", end: "07:00", deliver_on_release: true },
    });
    expect(validateSettings(ok).ok).toBe(true);
  });
});

describe("통화", () => {
  it("원화 단일 풀 외에는 받지 않는다", () => {
    expect(validateSettings(withDefaults({ base_currency: "USD" })).ok).toBe(false);
  });
});

describe("보조 함수", () => {
  it("비어 있는 필수 값을 모두 찾는다", () => {
    const missing = missingCriticalValues(DEFAULT_SETTINGS);
    expect(missing).toContain("국내 매수 수수료");
    expect(missing).toContain("국내 무위험수익률");
    // 비어 있으면 배당 폼이 세금을 못 채우고, 비워 둔 채 저장하면 0 으로 기록된다 (25.127)
    expect(missing).toContain("국내 배당소득세");
  });

  it("값을 채우면 목록에서 빠진다", () => {
    const filled = withDefaults({
      fees: { kr_buy_pct: 0.015, kr_sell_pct: 0.015, us_buy_pct: 0.25, us_sell_pct: 0.25 },
      // 배당 원천징수율도 필수가 됐다 (25.127) — 배당 폼이 그 비율로 세금 칸을 채운다
      taxes: { ...DEFAULT_SETTINGS.taxes, kr_transaction_pct: 0.18, kr_dividend_pct: 15.4, us_dividend_pct: 15 },
      risk_free_manual: { kr_pct: 3.2, us_pct: 4.1 },
    });
    expect(missingCriticalValues(filled)).toEqual([]);
  });

  it("일관성 검사가 문제를 문장으로 돌려준다", () => {
    const problems = checkWeightConsistency(
      withDefaults({ max_weight_per_stock: 50, max_weight_per_sector: 20 }),
    );
    expect(problems.length).toBe(1);
  });
});

describe("알 수 없는 입력", () => {
  it("빈 객체는 거부한다", () => {
    expect(validateSettings({}).ok).toBe(false);
  });

  it("null 은 거부한다", () => {
    expect(validateSettings(null).ok).toBe(false);
  });

  it("문자열로 온 숫자는 거부한다", () => {
    // 폼에서 문자열이 새어 들어오면 계산이 조용히 틀어진다
    expect(
      validateSettings(withDefaults({ total_investable_amount: "1000000" })).ok,
    ).toBe(false);
  });
});

describe("쓰이지 않는 설정 칸에는 사유가 있다", () => {
  /**
   * **있었던 일** (docs/infra.md 25.127). 세율 칸이 넷인데 **읽는 코드가 하나뿐**이었다.
   * 사용자가 "배당소득세 15.4" 를 적고 저장해도 바뀌는 숫자가 하나도 없었고, 화면도
   * 문서도 그 사실을 말하지 않았다. 입력란은 **쓰인다는 약속**이다.
   *
   * 여기서 막는 것: 새 칸을 만들고 배선을 잊는 일. 쓰거나, 왜 안 쓰는지 적거나 둘 중 하나다.
   */
  const 쓰는곳: Record<string, string> = {
    kr_transaction_pct: "batch: Costs.for_country · portfolio.cost_rates",
    kr_dividend_pct: "web: 배당 폼의 원천징수 채우기 (withholdingRate)",
    us_dividend_pct: "web: 배당 폼의 원천징수 채우기 (withholdingRate)",
    // 25.617 부터 연간 추정에 쓴다 — 사유 표에 "기록용" 으로 남아 화면이 거짓말을 했다 (25.629)
    us_capital_gains_pct: "batch: portfolio.us_capital_gains_estimates (연간 추정·참고값)",
    pension_income_pct: "batch: tax_sim.simulate (ETF 계좌별 세후 적립 시뮬레이션, 25.1003)",
    pension_credit_pct: "batch: tax_sim.simulate (ETF 계좌별 세후 적립 시뮬레이션, 25.1003)",
  };

  it("모든 세율 칸은 쓰이거나 사유가 있다", () => {
    const 칸 = Object.keys(DEFAULT_SETTINGS.taxes);
    expect(칸.length).toBeGreaterThan(3); // 훑기가 조용히 0개를 내면 이 검사가 장식이 된다
    const 고아 = 칸.filter((k) => !(k in 쓰는곳) && !(k in UNUSED_SETTING_REASON));
    expect(고아, "쓰이지도 않고 사유도 없는 설정 칸이다").toEqual([]);
  });

  it("사유는 비어 있지 않다", () => {
    // 빈 문자열을 넣어 검사를 통과시키는 것을 막는다. 사유는 사람이 읽을 문장이어야 한다
    for (const [키, 사유] of Object.entries(UNUSED_SETTING_REASON)) {
      expect(사유.length, `${키} 의 사유가 너무 짧다`).toBeGreaterThan(20);
    }
  });

  it("쓰인다고 적어 둔 칸을 사유 표에 또 넣지 않는다", () => {
    // 둘 다에 있으면 어느 쪽이 사실인지 알 수 없다
    for (const 키 of Object.keys(쓰는곳)) {
      expect(UNUSED_SETTING_REASON[키]).toBeUndefined();
    }
  });
});

describe("모든 설정 키는 어딘가에서 읽힌다 (docs/infra.md 25.257)", () => {
  /**
   * 위 검사는 **세율 칸만** 본다. `backfill_years` 는 저장만 되고 배치도 웹도 안 읽었는데 사유가 없었다.
   * 최상위 키마다 `lib/settings.ts` 밖(배치·웹)에 그 이름이 나오는지 본다. 없으면 사유 표에 있어야 한다.
   */
  const 뿌리 = join(process.cwd(), "..");
  function 모든_글(): string {
    const 쌓기: string[] = [];
    const 훑기 = (디렉터리: string) => {
      for (const 이름 of readdirSync(디렉터리)) {
        if (이름 === "node_modules" || 이름 === "__pycache__" || 이름 === "__tests__" || 이름.startsWith(".")) continue;
        const 길 = join(디렉터리, 이름);
        if (statSync(길).isDirectory()) 훑기(길);
        else if (/\.(py|ts|tsx)$/.test(이름) && !길.endsWith(join("lib", "settings.ts"))) 쌓기.push(readFileSync(길, "utf-8"));
      }
    };
    for (const d of ["batch", "web/lib", "web/app", "web/components"]) 훑기(join(뿌리, d));
    return 쌓기.join("\n");
  }

  it("읽는 곳이 없는 키는 사유가 있다", () => {
    const 글 = 모든_글();
    expect(글.length).toBeGreaterThan(100_000); // 훑기가 비면 공짜로 통과한다
    const 고아 = SETTINGS_KEYS.filter((k) => !new RegExp(`\\b${k}\\b`).test(글) && !(k in UNUSED_SETTING_REASON));
    expect(고아, "저장만 되고 아무도 읽지 않는 설정이다 — 배선하거나 UNUSED_SETTING_REASON 에 사유를 적어라").toEqual([]);
  });
});
