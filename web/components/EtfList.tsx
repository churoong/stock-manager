"use client";

import { useCallback, useEffect, useState } from "react";
import { afterHydration, cachedFetch, markHydrated, peekCachedJson } from "@/lib/clientCache";
import { money } from "@/lib/portfolio";
import QuickTrade from "@/components/QuickTrade";
import AccumulationStocks from "@/components/AccumulationStocks";
import CriteriaTable from "@/components/CriteriaTable";
import MarketTabs from "@/components/MarketTabs";
import SatelliteList from "@/components/SatelliteList";
import {
  ETF_STALE_AFTER_DAYS,
  formatAssets,
  formatPct,
  emptyMarketNote,
  groupByMarket,
  overlapText,
  parseOverlap,
  type EtfRow,
  type MarketGroup,
} from "@/lib/etf";
import { ACCOUNTS, groupByAccount, type Account } from "@/lib/etfAccounts";
import { taxSimLines, type TaxSim } from "@/lib/taxSim";
import { GROUP_LABEL, WIDE_POOL_WARNING, onePerIndex, parseTilt, tiltLine, tiltSource } from "@/lib/etfTilt";
import { readSavedCountry, saveCountry, type Country } from "@/lib/market";
import { daysSince, formatKst, parseCriteria } from "@/lib/recommend";

/**
 * 장기 적립 ETF 목록.
 *
 * 한 ETF 마다 답해야 하는 질문 (etf.md 7장)
 *   무엇을   이름·분류(국내는 기초지수)·운용사
 *   왜       근거 문장. 보수를 반드시 보여 준다. 20년이면 이게 제일 크다
 *            국내는 보수 자료가 없어 "보수 미반영" 을 숨기지 않고 적는다
 *   확인     근거표. 값·문턱·출처·기준일
 *   참고     오늘 추천 종목이 얼마나 담겼나. 고르는 기준이 아니다
 *
 * 수익률은 보여주지 않는다. 지난 수익률은 다음 20년을 말해 주지 않는다.
 */

interface LastRun {
  finished_at: string | null;
  status: string | null;
  /** 일부만 끝난 판정의 까닭 (lib/etf.etfRunNote, 25.580) */
  note?: string | null;
  /** 이름으로 판정 전에 뺀 ETF (lib/etf.etfNameExcludedNote, 25.718) */
  name_note?: string | null;
}

type View = "focus" | "core" | "accounts" | "satellite" | "stocks";

const VIEWS: Array<{ key: View; label: string }> = [
  // 우리 종목 집중 (docs/etf.md 11.5, 25.968) — 우리 점수 상위 종목을 많이 담은 순. 넓은 지수가 아니어도 들어온다
  { key: "focus", label: "우리 종목 집중" },
  { key: "core", label: "핵심 ETF" },
  // 계좌별 (docs/etf.md 11.1, 25.966) — 연금저축·IRP·일반계좌마다 무엇을 먼저 담나
  { key: "accounts", label: "계좌별" },
  { key: "satellite", label: "위성 ETF" },
  { key: "stocks", label: "종목" },
];

const VIEW_STORAGE_KEY = "longterm.view";

/**
 * 장기 적립 탭. 국내·미국 → [핵심 ETF][위성 ETF][종목].
 *
 * 핵심은 넓은 지수(docs/etf.md 8·9장), 위성은 배당·업종·테마(10장)다. 위성은 핵심을 대신하지 않아
 * 같은 목록에 섞지 않는다. 종목 적립은 사용자 결정(2026-09-17)으로 DART 배당 수집·검증 뒤에 공개한다.
 */
function readSavedView(): View {
  try {
    const saved = window.localStorage.getItem(VIEW_STORAGE_KEY);
    if (saved === "focus" || saved === "core" || saved === "accounts" || saved === "satellite" || saved === "stocks") return saved;
  } catch {
    // 저장소를 못 쓰면 우리 종목 집중부터 (25.968)
  }
  return "focus";
}

export default function EtfList() {
  // 메뉴로 옮겨 온 화면이면 저장된 나라·보기로 **처음부터** 그린다 (docs/infra.md 25.893). 예전에는 늘 국내·핵심으로 그렸다가
  // 저장된 미국·종목으로 바꾸느라 핵심 ETF 를 한 번 더 읽었다. 첫 하이드레이션은 서버 그림과 같게 국내·핵심
  const [country, setCountry] = useState<Country>(() => (afterHydration() ? readSavedCountry() : "KR"));
  const [view, setView] = useState<View>(() => (afterHydration() ? readSavedView() : "focus"));
  useEffect(() => {
    markHydrated();
    setCountry(readSavedCountry());
    setView(readSavedView());
  }, []);

  return (
    <div>
      <MarketTabs
        active={country}
        onChange={(next) => {
          setCountry(next);
          saveCountry(next);
        }}
      />
      <div role="tablist" aria-label="장기 적립 보기" className="mb-3 flex gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            type="button"
            role="tab"
            aria-selected={v.key === view}
            onClick={() => {
              setView(v.key);
              try {
                window.localStorage.setItem(VIEW_STORAGE_KEY, v.key);
              } catch {
                // 기억하지 못해도 된다
              }
            }}
            className={`min-h-11 flex-1 rounded-md px-2 text-sm transition sm:min-h-0 sm:py-1.5 ${
              v.key === view
                ? "bg-white font-medium shadow-sm dark:bg-slate-900"
                : "text-slate-600 hover:bg-white/60 dark:text-slate-300 dark:hover:bg-slate-900/60"
            }`}
          >
            {v.label}
          </button>
        ))}
      </div>

      {view === "focus" ? <FocusEtfList country={country} /> : null}
      {view === "core" ? <CoreEtfList country={country} /> : null}
      {view === "accounts" ? <AccountEtfList /> : null}
      {view === "satellite" ? <SatelliteList country={country} /> : null}
      {view === "stocks" ? <AccumulationStocks country={country} /> : null}
    </div>
  );
}

interface CoreResponse {
  picked?: EtfRow[];
  tilt_leaders?: EtfRow[];
  tilt_leaders_kr?: EtfRow[];
  excluded?: Record<string, EtfRow[]>;
  notes?: string[];
  last_runs?: Record<string, LastRun | null>;
}

function CoreEtfList({ country }: { country: Country }) {
  // 이미 읽은 응답이 있으면 그것으로 바로 그린다 (25.893)
  const [d0] = useState(() => peekCachedJson<CoreResponse>("/api/etf", { method: "POST" }));
  const [picked, setPicked] = useState<EtfRow[]>(d0?.picked ?? []);
  const [excluded, setExcluded] = useState<Record<string, EtfRow[]>>(d0?.excluded ?? {});
  const [notes, setNotes] = useState<string[]>(d0?.notes ?? []);
  const [lastRuns, setLastRuns] = useState<Record<string, LastRun | null>>(d0?.last_runs ?? {});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(d0 === null);

  const load = useCallback(async () => {
    if (peekCachedJson("/api/etf", { method: "POST" }) === null) setLoading(true);
    setError(null);
    try {
      const response = await cachedFetch("/api/etf", { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        setError(data.errors?.join(", ") ?? "조회에 실패했습니다");
        return;
      }
      setPicked(data.picked ?? []);
      setExcluded(data.excluded ?? {});
      setNotes(data.notes ?? []);
      // 시장별 마지막 실행 (docs/infra.md 25.354). 다른 시장의 실행 시각을 빌려 쓰지 않는다
      setLastRuns(data.last_runs ?? {});
    } catch {
      setError("조회에 실패했습니다");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return <p className="py-8 text-sm text-slate-500">불러오는 중…</p>;
  }

  if (error) {
    return (
      <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">
        {error}
      </p>
    );
  }

  // 훅이 아니다. early return 뒤라 훅을 쓰면 안 된다(RecommendList 주석 참고).
  const markets = groupByMarket(picked, undefined, excluded);
  const market = markets.find((m) => m.country === country) ?? markets[0];
  const ran = formatKst(lastRuns[country]?.finished_at ?? null);

  return (
    <div>
      <div className="mb-3 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium">통과 {market.total}개</span>
          <span className="text-slate-400">{market.label} · 월 1회 판정</span>
        </div>
        {ran ? (
          <div className="mt-1 text-slate-500 dark:text-slate-400">마지막 배치 {ran} KST</div>
        ) : null}
        {lastRuns[country]?.name_note ? (
          <div className="mt-1 text-slate-500 dark:text-slate-400">{lastRuns[country]?.name_note}</div>
        ) : null}
        {lastRuns[country]?.note ? (
          <div className="mt-1 text-amber-700 dark:text-amber-300">{lastRuns[country]?.note}</div>
        ) : null}
      </div>

      {notes.map((note) => (
        <p
          key={note}
          className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          {note}
        </p>
      ))}

      <MarketSection market={market} excluded={excluded[market.country] ?? []} ranAt={ran} />
    </div>
  );
}

/**
 * 우리 종목 집중 (docs/etf.md 11.5, 25.968). 미국 주식형 ETF 전체를 "우리 점수 상위 10% 종목 비중" 순으로 — 배치가 낸 순위 그대로.
 * 넓은 지수가 아닌 것(업종·테마·팩터)도 들어오고, 그런 카드에는 경고를 단다. 백테스트 전 참고 순위다.
 */
function FocusEtfList({ country }: { country: Country }) {
  const [d0] = useState(() => peekCachedJson<CoreResponse>("/api/etf", { method: "POST" }));
  const [byCountry, setByCountry] = useState<Record<Country, EtfRow[]>>({
    US: d0?.tilt_leaders ?? [], KR: d0?.tilt_leaders_kr ?? [],
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(d0 === null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const response = await cachedFetch("/api/etf", { method: "POST" });
        const data = await response.json();
        if (!alive) return;
        if (!response.ok) {
          setError(data.errors?.join(", ") ?? "조회에 실패했습니다");
          return;
        }
        setByCountry({ US: data.tilt_leaders ?? [], KR: data.tilt_leaders_kr ?? [] });
      } catch {
        if (alive) setError("조회에 실패했습니다");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (loading) return <p className="py-8 text-sm text-slate-500">불러오는 중…</p>;
  if (error) {
    return (
      <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">
        {error}
      </p>
    );
  }
  const rows = byCountry[country];
  const first = rows.length > 0 ? parseTilt(rows[0].rationale_data) : null;
  return (
    <div>
      <p className="mb-3 text-xs leading-relaxed text-slate-500">
        {country === "KR" ? "국내 상장" : "미국 상장"} 주식형 ETF 를 <b>우리 장기 점수(밸류·퀄리티) 상위 {(first?.top_share_pct ?? 10).toFixed(0)}% 종목을 얼마나 담았나</b>{" "}
        순으로 세웠습니다. 전 종목 보유는 SEC 분기 공시에서 받았습니다(약 두 달 늦음). 넓은 지수가 아닌 ETF 도 들어옵니다 —
        아직 백테스트로 효과를 확인하지 않은 참고 순위입니다.
        {first?.score_as_of ? ` 점수 기준일 ${first.score_as_of}.` : ""}
      </p>
      {country === "KR" ? (
        <p className="mb-3 rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600 dark:bg-slate-900 dark:text-slate-300">
          국내 상장은 두 무리로 따로 세웁니다 — <b>국내 지수</b>(같은 지수의 KODEX 구성종목, 국내 점수로)와 <b>미국 지수</b>(같은 지수의 미국 ETF 보유, 미국 점수로).
          국내·미국 점수는 잣대가 달라 섞지 않습니다. 같은 지수 상품은 하나로 묶었습니다. 액티브·선물형은 보유가 지수와 달라 뺐습니다.
          연금저축·IRP 에서 살 수 있는 것은 이 목록입니다.
        </p>
      ) : null}
      {rows.length === 0 ? (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          아직 순위가 없습니다 — 다음 ETF 판정(월 1회) 뒤 "점수 담음" 단계가 채웁니다.
        </p>
      ) : null}
      <div className="flex flex-col gap-2">
        {(country === "KR"
          ? (["kr_kr", "kr_us"] as const).flatMap((g) =>
              onePerIndex(rows.filter((r) => (parseTilt(r.rationale_data)?.group ?? "kr_us") === g)).map((x, i) => ({
                ...x,
                head: i === 0 ? GROUP_LABEL[g] : null,
              })),
            )
          : rows.map((row) => ({ row, others: [] as string[], head: null as string | null }))
        ).map(({ row, others, head }) => {
          const t = parseTilt(row.rationale_data);
          return (
            <div key={row.etf_id}>
              {head ? <h3 className="mb-1 mt-3 text-sm font-medium">{head}</h3> : null}
              <div className="mb-0.5 text-xs text-slate-500">
                {t?.rank_all ?? "-"}위 · {row.category ?? "분류 없음"}
                {others.length > 0 ? ` · 같은 지수 다른 상품 ${others.length}개 (${others.slice(0, 4).join(", ")}${others.length > 4 ? " 외" : ""})` : ""}
              </div>
              {t?.pool === "wide" ? (
                <p className="mb-1 text-xs text-amber-700 dark:text-amber-300">{WIDE_POOL_WARNING}</p>
              ) : null}
              <EtfCard row={row} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

const ACCOUNT_STORAGE_KEY = "longterm.account";

/**
 * 계좌별 보기 (docs/etf.md 11.1, 25.966). 국내·미국 상장을 함께 본다 — 연금저축은 국내 상장만 살 수 있고 일반계좌는 둘 다다.
 * 순위·까닭은 배치가 적은 그대로다. 분류(국내는 기초지수)마다 그 분류 1위만 — 같은 지수의 상품을 다 늘어놓지 않는다.
 */
/** 계좌별 세후 적립 시뮬레이션 (docs/etf.md 11.7, 25.1003). 배치가 계산한 값을 읽어 보인다. 못 읽으면 조용히 접는다 — 곁다리다 */
function TaxSimCard() {
  const [sim, setSim] = useState<TaxSim | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const response = await fetch("/api/etf/tax-sim");
        const data = await response.json();
        if (!alive) return;
        if (!response.ok) return setNote(`세후 시뮬레이션을 읽지 못했습니다 (${data.errors?.join(", ") ?? response.status})`);
        setSim(data.sim ?? null);
        setNote(data.note ?? null);
      } catch {
        if (alive) setNote("세후 시뮬레이션을 읽지 못했습니다");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);
  const v = taxSimLines(sim);
  if (!v && !note) return null;
  return (
    <details className="mb-3 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-800">
      <summary className="cursor-pointer text-sm font-medium">같은 돈, 계좌에 따라 세후 얼마나 다를까</summary>
      {v ? (
        <div className="mt-2 leading-relaxed text-slate-600 dark:text-slate-300">
          <p className="mb-2">{v.head}</p>
          {v.groups.map((g) => (
            <div key={g.years} className="mb-2">
              <p className="font-medium">{g.years}년 뒤</p>
              {g.lines.map((l) => <p key={l}>{l}</p>)}
            </div>
          ))}
          <p className="text-slate-500">
            세율은 설정의 값입니다. 금융소득종합과세·건강보험료·연금 수령 한도·환율 변동은 넣지 않았습니다.
          </p>
        </div>
      ) : (
        <p className="mt-2 text-slate-500">{note}</p>
      )}
    </details>
  );
}

function AccountEtfList() {
  const [d0] = useState(() => peekCachedJson<CoreResponse>("/api/etf", { method: "POST" }));
  const [picked, setPicked] = useState<EtfRow[]>(d0?.picked ?? []);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(d0 === null);
  const [account, setAccount] = useState<Account>("pension");

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(ACCOUNT_STORAGE_KEY);
      if (saved === "pension" || saved === "irp" || saved === "taxable") setAccount(saved);
    } catch {
      // 기억하지 못해도 된다
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const response = await cachedFetch("/api/etf", { method: "POST" });
        const data = await response.json();
        if (!alive) return;
        if (!response.ok) {
          setError(data.errors?.join(", ") ?? "조회에 실패했습니다");
          return;
        }
        setPicked(data.picked ?? []);
      } catch {
        if (alive) setError("조회에 실패했습니다");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (loading) return <p className="py-8 text-sm text-slate-500">불러오는 중…</p>;
  if (error) {
    return (
      <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">
        {error}
      </p>
    );
  }

  const view = groupByAccount(picked, account);
  return (
    <div>
      <div role="tablist" aria-label="계좌" className="mb-3 flex gap-1">
        {ACCOUNTS.map((a) => (
          <button
            key={a.key}
            type="button"
            role="tab"
            aria-selected={a.key === account}
            onClick={() => {
              setAccount(a.key);
              try {
                window.localStorage.setItem(ACCOUNT_STORAGE_KEY, a.key);
              } catch {
                // 기억하지 못해도 된다
              }
            }}
            className={`min-h-11 rounded-full border px-3 text-sm sm:min-h-0 sm:py-1 ${
              a.key === account
                ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
                : "border-slate-200 text-slate-600 dark:border-slate-700 dark:text-slate-300"
            }`}
          >
            {a.label}
          </button>
        ))}
      </div>
      <p className="mb-3 text-xs leading-relaxed text-slate-500">
        순서는 계좌마다 세금이 어디서 갈리는지로 정했습니다. 세율은 설정의 값을 인용만 합니다 —
        제도와 세율은 바뀔 수 있으니 가입한 금융사에서 확인하세요. 국내·미국 상장을 함께 봅니다.
      </p>
      <TaxSimCard />
      {view.missing > 0 && view.tiers.length === 0 ? (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          계좌별 순위는 다음 월 판정부터 나옵니다 — 지금 판정은 이 기능 전에 냈습니다.
        </p>
      ) : null}
      {view.tiers.map((tier) => (
        <div key={`${tier.priority}-${tier.country}-${tier.bucket}`} className="mb-4">
          <h3 className="mb-1 text-sm font-medium">
            {tier.priority}순위 · {tier.country === "KR" ? "국내 상장" : "미국 상장"} {tier.bucket}
          </h3>
          {tier.reason ? <p className="mb-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300">{tier.reason}</p> : null}
          {tier.note ? <p className="mb-1 text-xs text-amber-700 dark:text-amber-300">{tier.note}</p> : null}
          <div className="flex flex-col gap-2">
            {tier.rows.map((row) => (
              <EtfCard key={row.etf_id} row={row} />
            ))}
          </div>
        </div>
      ))}
      {view.ineligible > 0 ? (
        <p className="text-xs text-slate-500">이 계좌에서 살 수 없는 통과 ETF {view.ineligible}개는 뺐습니다(연금저축·IRP 는 국내 상장만).</p>
      ) : null}
    </div>
  );
}

function MarketSection({ market, excluded, ranAt }: { market: MarketGroup; excluded: EtfRow[]; ranAt: string | null }) {
  const age = daysSince(market.asOf);
  const stale = age !== null && age > ETF_STALE_AFTER_DAYS;

  if (market.total === 0 && excluded.length === 0) {
    return (
      <section className="mb-8">
        <p className="text-xs text-slate-500">{emptyMarketNote(ranAt)}</p>
      </section>
    );
  }

  return (
    <section className="mb-8">
      <h2 className="mb-1 flex flex-wrap items-baseline gap-x-2 text-sm font-semibold">
        <span
          className={`text-xs font-normal ${
            stale ? "text-amber-700 dark:text-amber-300" : "text-slate-400"
          }`}
        >
          기준일 {market.asOf ?? "-"}
          {age !== null && age > 0 ? ` (${age}일 전)` : ""}
          {stale ? ` · ${ETF_STALE_AFTER_DAYS}일 넘게 새로 판정되지 않았습니다` : ""}
        </span>
      </h2>
      {market.country === "KR" ? (
        <p className="mb-2 text-xs leading-relaxed text-slate-500">
          국내 ETF 는 총보수 자료를 받을 경로가 없어 <b>보수를 반영하지 않았습니다</b>. 같은 기초지수
          안에서 순자산과 괴리율로만 순위를 냈습니다. 고르기 전에 운용사에서 보수를 꼭 확인하세요.
        </p>
      ) : null}

      {market.buckets.map((group) => (
        <div key={group.bucket} className="mb-4">
          <h3 className="mb-2 text-sm font-medium">
            {group.bucket}
            <span className="ml-2 font-normal text-slate-500">통과 {group.total}개</span>
          </h3>
          {group.categories.map((cat) => (
            <div key={cat.category} className="mb-3">
              {/* 순위는 이 분류 안에서만 뜻이 있다. 분류가 다르면 비교하지 않는다 */}
              <h4 className="mb-1 text-xs text-slate-500">
                {market.country === "KR" ? `기초지수 ${cat.category}` : cat.category}
                <span className="ml-1 text-slate-400">
                  {cat.total > cat.rows.length
                    ? `· ${cat.total}개 중 상위 ${cat.rows.length}개`
                    : `· ${cat.total}개`}
                </span>
              </h4>
              <div className="flex flex-col gap-2">
                {cat.rows.map((row) => (
                  <EtfCard key={row.etf_id} row={row} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}

      {excluded.length > 0 ? <ExcludedList rows={excluded} /> : null}
    </section>
  );
}

function EtfCard({ row }: { row: EtfRow }) {
  const [open, setOpen] = useState(false);
  const criteria = parseCriteria(row.rationale_data);
  const overlap = overlapText(parseOverlap(row.rationale_data));
  const tilt = parseTilt(row.rationale_data);
  const kr = row.country === "KR";

  return (
    <article className="rounded-xl border border-slate-200 dark:border-slate-800">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full px-3 py-2.5 text-left"
      >
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-semibold">{kr ? row.name : row.symbol}</span>
          <span className="text-xs text-slate-500">{kr ? row.symbol : row.name}</span>
          <span className="ml-auto text-sm">
            {kr ? (
              <span className="text-xs text-amber-700 dark:text-amber-300">보수 미반영</span>
            ) : (
              <>
                보수 <b>{formatPct(row.expense_ratio)}</b>
              </>
            )}
            <span className="ml-2 text-xs text-slate-400">{open ? "▲" : "▼"}</span>
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-slate-500">
          {row.category_size === 1 ? (
            <span>분류 안 통과 1개</span>
          ) : row.rank_in_category && row.category_size ? (
            <span>
              분류 안 {row.rank_in_category}/{row.category_size}위
            </span>
          ) : null}
          <span>· 순자산 {formatAssets(row.total_assets, row.country)}</span>
          {kr && row.premium_abs_avg !== null ? (
            <span>· 평균 괴리율 {formatPct(row.premium_abs_avg, 3)}</span>
          ) : null}
          {row.last_close !== null && row.last_close !== undefined ? (
            // 이은 ETF 는 일일 배치가 날마다 종가를 받는다 (25.896)
            <span>· 현재가 {money(row.last_close, row.currency ?? (row.country === "US" ? "USD" : "KRW"))} ({row.last_close_date} 종가)</span>
          ) : row.prev_close !== null && row.prev_close !== undefined ? (
            // 아직 날마다 종가가 없으면 월 판정 때 받은 직전 종가 — 현재가로 읽히지 않게 날짜를 붙인다 (25.894)
            // 미국은 야후 previousClose 라 판정일(UTC 실행일)의 전 거래일 종가다 — 판정일을 종가 날짜처럼 적었다 (25.921)
            <span>· {row.country === "US" ? "판정 전 거래일 종가" : "판정 때 종가"} {money(row.prev_close, row.currency ?? (row.country === "US" ? "USD" : "KRW"))} ({row.country === "US" ? `${row.as_of_date} 판정` : row.as_of_date})</span>
          ) : null}
          {row.family ? <span>· {row.family}</span> : null}
        </div>
        <p
          className={`mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-300 ${
            open ? "" : "line-clamp-2"
          }`}
        >
          {row.rationale_text}
        </p>
        {tilt ? <p className="mt-1 text-xs text-sky-700 dark:text-sky-300">{tiltLine(tilt)}</p> : null}
      </button>

      {open ? (
        <div className="border-t border-slate-100 px-3 pb-3 pt-2 dark:border-slate-800">
          {/* 그 자리에서 매매 입력 (25.896). 이은 ETF 만 — 잇기 전이면 다음 일일 배치가 잇는다 */}
          <div className="mb-1 flex flex-wrap items-center justify-end gap-3 text-xs">
            {row.stock_id ? (
              <QuickTrade
                stock={{
                  id: row.stock_id,
                  ticker: row.symbol,
                  name: row.name,
                  country: row.country,
                  currency: row.currency ?? (row.country === "US" ? "USD" : "KRW"),
                  market: "ETF",
                  asset_type: "etf",
                }}
                horizon="long"
              />
            ) : (
              <span className="text-slate-500">매매 입력은 다음 일일 배치가 이 ETF 를 이은 뒤 열립니다</span>
            )}
          </div>
          {overlap ? (
            <p className="text-xs leading-relaxed text-slate-600 dark:text-slate-300">
              <span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-slate-500 dark:bg-slate-800">
                참고
              </span>
              {overlap}
            </p>
          ) : null}
          {tilt ? (
            // 점수 담음의 근거 (25.967) — 가장 크게 기여한 종목과 어느 공시·언제
            <div className="mt-2 text-xs leading-relaxed text-slate-600 dark:text-slate-300">
              {tilt.top.length > 0 ? (
                <p>
                  기여 상위:{" "}
                  {tilt.top
                    .map((c) => `${c.symbol} ${c.weight_pct.toFixed(1)}%·${c.score.toFixed(0)}점`)
                    .join(" · ")}
                </p>
              ) : null}
              <p className="text-slate-500">{tiltSource(tilt)}</p>
            </div>
          ) : null}
          {/* 0줄이어도 그린다 — 그때는 빨간 경고가 뜬다 (docs/infra.md 25.71·25.174) */}
          <CriteriaTable
            rows={criteria}
            caption={`판정일 ${row.as_of_date}`}
            docPath={kr ? "docs/etf.md 9장" : "docs/etf.md 8장"}
            staleAfterDays={ETF_STALE_AFTER_DAYS}
          />
        </div>
      ) : null}
    </article>
  );
}

/**
 * 왜 없나.
 *
 * "왜 QQQ 가 없지?" 에 답해야 확인 가능한 추천이다(etf.md 4장). 순자산이 큰
 * 순으로 보여 이름이 알려진 ETF 가 먼저 나온다. 통화가 달라 시장별로 따로 세운다.
 */
function ExcludedList({ rows }: { rows: EtfRow[] }) {
  return (
    <details className="rounded-lg border border-slate-200 text-xs dark:border-slate-800">
      <summary className="cursor-pointer select-none px-3 py-2 text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-900">
        큰 ETF 중 뺀 것
        <span className="ml-2 text-slate-400">{rows.length}개 · 순자산 큰 순</span>
      </summary>
      <ul className="divide-y divide-slate-100 border-t border-slate-200 dark:divide-slate-800 dark:border-slate-800">
        {rows.map((row) => (
          <li key={row.etf_id} className="px-3 py-2">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">{row.country === "KR" ? row.name : row.symbol}</span>
              <span className="text-slate-500">{row.country === "KR" ? row.symbol : row.name}</span>
              <span className="ml-auto text-slate-500">{formatAssets(row.total_assets, row.country)}</span>
            </div>
            <p className="mt-0.5 text-amber-700 dark:text-amber-300">{row.excluded_reason}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}
