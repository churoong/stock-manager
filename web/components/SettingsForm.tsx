"use client";

import TelegramQaSetup from "@/components/TelegramQaSetup";
import { useEffect, useMemo, useState } from "react";
import { clearClientCache } from "@/lib/clientCache";
import MarketTabs from "@/components/MarketTabs";
import { readSavedCountry, saveCountry, type Country } from "@/lib/market";
import { TARGETS_DELAY_NOTE, UNUSED_SETTING_REASON, canSaveSettings, numberFieldInput, type Settings } from "@/lib/settings";
import { releaseNote } from "@/lib/intraday";

/**
 * `loadFailed`: 저장된 설정을 읽지 못해 `initial` 이 **기본값**이다 (docs/infra.md 25.351).
 * 그때 저장하면 PUT 이 모든 키를 쓰므로 수수료·세율·무위험수익률·목표/손절이 전부 기본값으로 덮인다.
 * 그래서 저장을 막는다. 텔레그램 테스트는 설정을 쓰지 않으므로 그대로 둔다.
 */
type Props = { initial: Settings; initialWarnings: string[]; loadFailed?: boolean };

type Msg = { kind: "ok" | "warn" | "error"; lines: string[] } | null;

const FACTOR_LABELS: Record<string, string> = {
  value: "밸류",
  quality: "퀄리티",
  growth: "성장",
  momentum: "모멘텀",
  risk: "리스크",
};

const HORIZON_LABELS: Record<string, string> = {
  short: "단기 (1~3개월)",
  mid: "중기 (3~12개월)",
  long: "장기 (1년 이상)",
};

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-sm font-semibold">{title}</h2>
      {hint && (
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>
      )}
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

function NumberField({
  label,
  value,
  onChange,
  suffix,
  step = 1,
  nullable = false,
  hint,
}: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  suffix?: string;
  step?: number;
  nullable?: boolean;
  /** 이 칸이 무엇에 쓰이는지(또는 **왜 안 쓰이는지**). 사유 없는 칸을 두지 않는다 (25.127) */
  hint?: string;
}) {
  // 입력칸 글은 따로 들고 있는다 — "-" 나 빈 칸을 치는 도중에 부모 값이 0 으로 바뀌지 않게 (25.573)
  const [글, 글바꾸기] = useState<string>(value === null ? "" : String(value));
  useEffect(() => {
    const 지금 = numberFieldInput(글, nullable);
    if (!지금.commit || 지금.value !== value) 글바꾸기(value === null ? "" : String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 부모 값이 바뀔 때만 맞춘다
  }, [value]);
  return (
    <label className="flex flex-wrap items-center justify-between gap-x-3 text-sm">
      <span className="text-slate-700 dark:text-slate-300">
        {label}
        {nullable && value === null && (
          <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">
            미입력
          </span>
        )}
      </span>
      <span className="flex shrink-0 items-center gap-1">
        <input
          // 글자 칸 — 숫자 칸은 브라우저마다 "1,000" 을 ""로 넘기거나 막는다 (25.1113, 매매 폼 25.642 와 같게)
          type="text"
          inputMode="decimal"
          value={글}
          placeholder={nullable ? "확인 필요" : ""}
          onChange={(e) => {
            글바꾸기(e.target.value);
            const 받음 = numberFieldInput(e.target.value, nullable);
            if (받음.commit) onChange(받음.value);
          }}
          // 칸을 떠날 때 올리지 않은 글(빈 칸·"-")은 지금 값으로 되돌린다 — 비운 채 저장해도 0 이 되지 않는다
          onBlur={() => 글바꾸기(value === null ? "" : String(value))}
          className="w-28 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-right text-base tabular-nums outline-none focus:border-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:focus:border-slate-300"
        />
        {suffix && (
          <span className="w-6 text-xs text-slate-500 dark:text-slate-400">
            {suffix}
          </span>
        )}
      </span>
      {hint && (
        <span className="mt-1 w-full text-xs text-slate-500 dark:text-slate-400">{hint}</span>
      )}
    </label>
  );
}

export default function SettingsForm({ initial, initialWarnings, loadFailed = false }: Props) {
  const [s, setS] = useState<Settings>(initial);
  // 이 화면이 불러온(또는 마지막으로 저장한) 값 — 서버가 바뀐 칸만 쓰게 함께 보낸다 (25.1115)
  const [base, setBase] = useState<Settings>(initial);
  const [msg, setMsg] = useState<Msg>(
    initialWarnings.length ? { kind: "warn", lines: initialWarnings } : null,
  );
  const [busy, setBusy] = useState(false);

  // 시장별 항목(수수료·세금·무위험수익률)은 국내·미국 탭으로 나눈다 (2026-09-17 사용자 요청).
  // 두 시장 값은 한 상태에 함께 있어서 탭을 바꿔도 입력이 사라지지 않고, 저장도 한 번에 된다.
  const [country, setCountry] = useState<Country>("KR");
  useEffect(() => {
    setCountry(readSavedCountry());
  }, []);

  const weightSum = useMemo(
    () =>
      s.factor_weights.value +
      s.factor_weights.quality +
      s.factor_weights.growth +
      s.factor_weights.momentum +
      s.factor_weights.risk,
    [s.factor_weights],
  );
  const weightOk = Math.abs(weightSum - 100) < 0.01;

  function patch(updater: (draft: Settings) => Settings) {
    setS((prev) => updater(structuredClone(prev)));
  }

  async function save() {
    if (!canSaveSettings(loadFailed)) return;
    setBusy(true);
    setMsg(null);
    try {
      clearClientCache(); // 설정을 바꾸면 화면 캐시를 버린다 (25.879)
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...s, _base: base }),
      });
      const body = await response.json().catch(() => ({}));

      if (!response.ok) {
        setMsg({ kind: "error", lines: body.errors ?? ["저장에 실패했습니다"] });
        return;
      }
      setBase(s);
      // 포트폴리오에 쓰는 칸이 바뀌었으면 재계산을 깨웠는지 말한다 — 못 깨웠으면 다음 일일 배치 때 반영된다 (25.629)
      const 재계산: string | null = body.recalc
        ? body.recalc.dispatched
          ? "포트폴리오를 다시 계산하도록 요청했습니다 — 몇 분 뒤 반영됩니다"
          : `포트폴리오는 아직 옛 설정으로 계산돼 있습니다: ${body.recalc.reason ?? "재계산을 깨우지 못했습니다"}`
        : null;
      const 목표손절 = body.targets_changed ? [TARGETS_DELAY_NOTE] : [];
      const 줄들: string[] = [...(body.warnings?.length ? body.warnings : ["저장했습니다"]), ...(재계산 ? [재계산] : []), ...목표손절];
      setMsg({ kind: body.warnings?.length || (body.recalc && !body.recalc.dispatched) ? "warn" : "ok", lines: 줄들 });
    } catch {
      setMsg({ kind: "error", lines: ["서버에 연결하지 못했습니다"] });
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    setBusy(true);
    setMsg(null);
    try {
      const response = await fetch("/api/telegram/test", { method: "POST" });
      const body = await response.json().catch(() => ({}));
      setMsg(
        response.ok
          ? { kind: "ok", lines: [body.message ?? "보냈습니다"] }
          : { kind: "error", lines: [body.error ?? "발송에 실패했습니다"] },
      );
    } catch {
      setMsg({ kind: "error", lines: ["서버에 연결하지 못했습니다"] });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Section
        title="투자 금액"
        hint="원화 단일 풀로 관리합니다. 미국 종목은 당일 환율로 환산해 비중을 계산합니다."
      >
        <NumberField
          label="총 투자가능금액"
          value={s.total_investable_amount}
          step={100000}
          suffix="원"
          onChange={(v) =>
            patch((d) => ({ ...d, total_investable_amount: v ?? 0 }))
          }
        />
      </Section>

      <Section
        title="팩터 가중치"
        hint="다섯 팩터의 합이 100이어야 합니다. 센티먼트는 별도 축입니다."
      >
        {(Object.keys(FACTOR_LABELS) as Array<keyof Settings["factor_weights"]>).map(
          (key) => (
            <NumberField
              key={key}
              label={FACTOR_LABELS[key]}
              value={s.factor_weights[key]}
              suffix="%"
              onChange={(v) =>
                patch((d) => ({
                  ...d,
                  factor_weights: { ...d.factor_weights, [key]: v ?? 0 },
                }))
              }
            />
          ),
        )}
        <p
          className={`text-xs ${weightOk ? "text-slate-500 dark:text-slate-400" : "text-red-600 dark:text-red-400"}`}
        >
          합계 {weightSum.toFixed(1)}%{weightOk ? "" : " · 100%가 되어야 저장됩니다"}
        </p>
        <NumberField
          label="센티먼트 가중치"
          value={s.sentiment_weight}
          suffix="%"
          onChange={(v) => patch((d) => ({ ...d, sentiment_weight: v ?? 0 }))}
        />
        <p className="text-xs text-slate-500 dark:text-slate-400">
          0으로 두면 뉴스 감성을 종합 점수에서 빼고 다섯 팩터만으로 계산합니다.
        </p>
      </Section>

      <Section title="기간별 목표와 손절" hint="손절선은 음수로 넣습니다.">
        {(Object.keys(HORIZON_LABELS) as Array<keyof Settings["horizon_targets"]>).map(
          (key) => (
            <div key={key} className="space-y-2 border-t border-slate-100 pt-3 first:border-0 first:pt-0 dark:border-slate-800">
              <p className="text-xs font-medium text-slate-600 dark:text-slate-400">
                {HORIZON_LABELS[key]}
              </p>
              <NumberField
                label="목표수익률"
                value={s.horizon_targets[key].target_pct}
                suffix="%"
                onChange={(v) =>
                  patch((d) => ({
                    ...d,
                    horizon_targets: {
                      ...d.horizon_targets,
                      [key]: { ...d.horizon_targets[key], target_pct: v ?? 0 },
                    },
                  }))
                }
              />
              <NumberField
                label="손절선"
                value={s.horizon_targets[key].stop_pct}
                suffix="%"
                onChange={(v) =>
                  patch((d) => ({
                    ...d,
                    horizon_targets: {
                      ...d.horizon_targets,
                      [key]: { ...d.horizon_targets[key], stop_pct: v ?? 0 },
                    },
                  }))
                }
              />
            </div>
          ),
        )}
      </Section>

      <Section title="비중 상한" hint="원화 환산 평가액 기준으로 판정합니다.">
        <NumberField
          label="종목당 상한"
          value={s.max_weight_per_stock}
          suffix="%"
          onChange={(v) => patch((d) => ({ ...d, max_weight_per_stock: v ?? 0 }))}
        />
        <NumberField
          label="섹터당 상한"
          value={s.max_weight_per_sector}
          suffix="%"
          onChange={(v) => patch((d) => ({ ...d, max_weight_per_sector: v ?? 0 }))}
        />
        <NumberField
          label="최소 주문 금액"
          value={s.min_order_amount}
          suffix="원"
          onChange={(v) => patch((d) => ({ ...d, min_order_amount: v ?? 0 }))}
        />
        <p className="text-xs text-slate-500">
          권장 금액이 이보다 작으면 포트폴리오에서 빼고 사유를 적습니다. 상한이나 분산으로 금액이 깎인
          뒤에도 다시 봅니다. 미국 종목에는 그날 환율로 나눈 달러 값이 쓰입니다.
        </p>
      </Section>

      <Section
        title="시장 추세 필터"
        hint="지수(코스피·코스닥·S&P 500)가 200일 이동평균 아래면 신규 매수 권장 비중에 배수를 곱합니다. 점수와 신호는 바뀌지 않습니다."
      >
        <label className="flex items-center justify-between gap-3 text-sm">
          <span className="text-slate-700 dark:text-slate-300">약세 국면에서 비중 축소</span>
          <input
            type="checkbox"
            checked={s.trend_filter.enabled}
            onChange={(e) =>
              patch((d) => ({
                ...d,
                trend_filter: { ...d.trend_filter, enabled: e.target.checked },
              }))
            }
            className="size-5"
          />
        </label>
        {s.trend_filter.enabled && (
          <NumberField
            label="약세 배수 (0 = 신규 매수 금액 없음, 1 = 축소 없음)"
            value={s.trend_filter.bear_factor}
            step={0.05}
            onChange={(v) =>
              patch((d) => ({
                ...d,
                trend_filter: { ...d.trend_filter, bear_factor: v ?? 0.5 },
              }))
            }
          />
        )}
      </Section>

      <Section
        title="시장별 수수료·세금·무위험수익률"
        hint="증권사와 계좌에 따라 다릅니다. 비워 두면 실현손익이 정확하지 않습니다. 무위험수익률은 샤프지수 계산에 씁니다."
      >
        <MarketTabs
          active={country}
          onChange={(next) => {
            setCountry(next);
            saveCountry(next);
          }}
        />
        {country === "KR" ? (
          <>
            <NumberField
              label="매수 수수료"
              value={s.fees.kr_buy_pct}
              suffix="%"
              step={0.001}
              nullable
              onChange={(v) => patch((d) => ({ ...d, fees: { ...d.fees, kr_buy_pct: v } }))}
            />
            <NumberField
              label="매도 수수료"
              value={s.fees.kr_sell_pct}
              suffix="%"
              step={0.001}
              nullable
              onChange={(v) => patch((d) => ({ ...d, fees: { ...d.fees, kr_sell_pct: v } }))}
            />
            <NumberField
              label="매도 거래세 (증권거래세 + 농특세 합)"
              value={s.taxes.kr_transaction_pct}
              suffix="%"
              step={0.01}
              nullable
              // 배치는 이 값을 거래세와 코스피 농어촌특별세의 **합**으로 읽는다(backtest.py 시행일별 세율, portfolio 실현손익).
              // 체결 내역에는 둘이 따로 찍혀 거래세 칸만 옮겨 적기 쉬웠다 (docs/infra.md 25.912, 감사)
              hint="체결 내역의 증권거래세와 농어촌특별세를 더한 값입니다. 2026년 코스피·코스닥 모두 0.20%."
              onChange={(v) =>
                patch((d) => ({ ...d, taxes: { ...d.taxes, kr_transaction_pct: v } }))
              }
            />
            <NumberField
              label="배당소득세"
              value={s.taxes.kr_dividend_pct}
              suffix="%"
              step={0.1}
              nullable
              onChange={(v) =>
                patch((d) => ({ ...d, taxes: { ...d.taxes, kr_dividend_pct: v } }))
              }
            />
            <NumberField
              label="무위험수익률"
              value={s.risk_free_manual.kr_pct}
              suffix="%"
              step={0.01}
              nullable
              onChange={(v) =>
                patch((d) => ({ ...d, risk_free_manual: { ...d.risk_free_manual, kr_pct: v } }))
              }
            />
          </>
        ) : (
          <>
            <NumberField
              label="매수 수수료"
              value={s.fees.us_buy_pct}
              suffix="%"
              step={0.001}
              nullable
              onChange={(v) => patch((d) => ({ ...d, fees: { ...d.fees, us_buy_pct: v } }))}
            />
            <NumberField
              label="매도 수수료"
              value={s.fees.us_sell_pct}
              suffix="%"
              step={0.001}
              nullable
              onChange={(v) => patch((d) => ({ ...d, fees: { ...d.fees, us_sell_pct: v } }))}
            />
            <NumberField
              label="배당원천징수"
              value={s.taxes.us_dividend_pct}
              suffix="%"
              step={0.1}
              nullable
              onChange={(v) =>
                patch((d) => ({ ...d, taxes: { ...d.taxes, us_dividend_pct: v } }))
              }
            />
            <NumberField
              label="해외 양도소득세 (연간 추정용·참고값)"
              value={s.taxes.us_capital_gains_pct}
              suffix="%"
              step={0.1}
              nullable
              hint="한 해 해외 실현손익을 합산해 기본공제 250만원을 뺀 뒤 이 세율을 곱해, 포트폴리오 요약에 연간 추정(참고값)으로 보입니다. 실현손익 숫자에는 섞지 않습니다"
              onChange={(v) =>
                patch((d) => ({ ...d, taxes: { ...d.taxes, us_capital_gains_pct: v } }))
              }
            />
            <NumberField
              label="연금소득세 (시뮬레이션용)"
              value={s.taxes.pension_income_pct}
              suffix="%"
              step={0.1}
              nullable
              hint="ETF 계좌별 탭의 세후 적립 시뮬레이션에만 씁니다. 연금으로 받을 때의 세율을 넣으세요(나이에 따라 다릅니다)"
              onChange={(v) => patch((d) => ({ ...d, taxes: { ...d.taxes, pension_income_pct: v } }))}
            />
            <NumberField
              label="연금저축 세액공제율 (시뮬레이션용)"
              value={s.taxes.pension_credit_pct}
              suffix="%"
              step={0.1}
              nullable
              hint="ETF 계좌별 탭의 세후 적립 시뮬레이션에만 씁니다. 소득 구간에 따라 다릅니다"
              onChange={(v) => patch((d) => ({ ...d, taxes: { ...d.taxes, pension_credit_pct: v } }))}
            />
            <NumberField
              label="무위험수익률"
              value={s.risk_free_manual.us_pct}
              suffix="%"
              step={0.01}
              nullable
              onChange={(v) =>
                patch((d) => ({ ...d, risk_free_manual: { ...d.risk_free_manual, us_pct: v } }))
              }
            />
          </>
        )}
      </Section>

      <Section
        title="장중 알림 기준"
        hint="국내 장중 시세는 약 20분 지연입니다. 알림에 지연 사실이 함께 표시됩니다."
      >
        <NumberField
          label="급등락 기준"
          value={s.alert_thresholds.spike_pct}
          suffix="%"
          step={0.5}
          onChange={(v) =>
            patch((d) => ({
              ...d,
              alert_thresholds: { ...d.alert_thresholds, spike_pct: v ?? 0 },
            }))
          }
        />
        <NumberField
          label="거래량 배수"
          value={s.alert_thresholds.volume_multiple}
          suffix="배"
          step={0.5}
          onChange={(v) =>
            patch((d) => ({
              ...d,
              alert_thresholds: { ...d.alert_thresholds, volume_multiple: v ?? 0 },
            }))
          }
        />
      </Section>

      <Section
        title="야간 알림"
        hint="미국 장중 알림은 한국 시간 밤에 발생합니다."
      >
        <label className="flex items-center justify-between gap-3 text-sm">
          <span className="text-slate-700 dark:text-slate-300">조용시간 사용</span>
          <input
            type="checkbox"
            checked={s.quiet_hours.enabled}
            onChange={(e) =>
              patch((d) => ({
                ...d,
                quiet_hours: { ...d.quiet_hours, enabled: e.target.checked },
              }))
            }
            className="size-5"
          />
        </label>
        {s.quiet_hours.enabled && (
          <>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span className="text-slate-700 dark:text-slate-300">시작</span>
              <input
                type="time"
                value={s.quiet_hours.start}
                onChange={(e) =>
                  patch((d) => ({
                    ...d,
                    quiet_hours: { ...d.quiet_hours, start: e.target.value },
                  }))
                }
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-base dark:border-slate-700 dark:bg-slate-950"
              />
            </label>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span className="text-slate-700 dark:text-slate-300">해제</span>
              <input
                type="time"
                value={s.quiet_hours.end}
                onChange={(e) =>
                  patch((d) => ({
                    ...d,
                    quiet_hours: { ...d.quiet_hours, end: e.target.value },
                  }))
                }
                className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-base dark:border-slate-700 dark:bg-slate-950"
              />
            </label>
            {/* 해제 시각이 실제로 언제 나가는지와 같은지 (docs/infra.md 25.254) */}
            <p className="text-xs text-slate-500 dark:text-slate-400">{releaseNote(s.quiet_hours.end)}</p>
            <label className="flex items-center justify-between gap-3 text-sm">
              <span className="text-slate-700 dark:text-slate-300">
                해제 시각에 묶어서 발송
              </span>
              <input
                type="checkbox"
                checked={s.quiet_hours.deliver_on_release}
                onChange={(e) =>
                  patch((d) => ({
                    ...d,
                    quiet_hours: {
                      ...d.quiet_hours,
                      deliver_on_release: e.target.checked,
                    },
                  }))
                }
                className="size-5"
              />
            </label>
          </>
        )}
      </Section>

      {msg && (
        <div
          role="status"
          className={`rounded-lg px-3 py-2 text-sm ${
            msg.kind === "ok"
              ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
              : msg.kind === "warn"
                ? "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                : "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300"
          }`}
        >
          {msg.lines.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      <TelegramQaSetup />

      {/* 폰은 아래 메뉴가 떠 있다(Nav.tsx). 그 높이만큼 올리지 않으면 저장 단추가 가린다 */}
      <div className="sticky bottom-[calc(var(--phone-nav)+env(safe-area-inset-bottom))] sm:bottom-0 z-10 flex gap-2 border-t border-slate-200 bg-slate-50 py-3 dark:border-slate-800 dark:bg-slate-950">
        <button
          onClick={save}
          disabled={busy || !weightOk || !canSaveSettings(loadFailed)}
          title={canSaveSettings(loadFailed) ? undefined : "저장된 설정을 읽지 못해 저장을 막았습니다. 새로고침해 주세요"}
          className="flex-1 rounded-lg bg-slate-900 px-4 py-2.5 text-base font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
        >
          {busy ? "처리 중" : "저장"}
        </button>
        <button
          onClick={sendTest}
          disabled={busy}
          className="rounded-lg border border-slate-300 px-4 py-2.5 text-base font-medium disabled:opacity-40 dark:border-slate-700"
        >
          텔레그램 테스트
        </button>
      </div>
    </div>
  );
}
