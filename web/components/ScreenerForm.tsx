"use client";

import Link from "next/link";
import { cachedFetch } from "@/lib/clientCache";
import { useCallback, useEffect, useRef, useState } from "react";
import { readJson } from "@/lib/http";
import Icon from "@/components/Icon";
import MarketTabs from "@/components/MarketTabs";
import { readSavedCountry, saveCountry, type Country } from "@/lib/market";
import { signedInt } from "@/lib/portfolio";
import {
  MARKETS_BY_COUNTRY,
  MONEY_UNIT,
  SORT_FIELDS,
  defaultFilters,
  type ScreenerFilters,
  type ScreenerRow,
  type SortField,
  csvFileName,
  fiscalYearLabel,
  toCsv,
  limitNote,
} from "@/lib/screener";

type Bounds = { min: number | null; max: number | null };
type RangeKey = {
  [K in keyof ScreenerFilters]: ScreenerFilters[K] extends Bounds ? K : never;
}[keyof ScreenerFilters];

// 금액 두 칸은 나라마다 단위가 다르다(억 / $M).
const MONEY_FIELDS: Array<{ key: RangeKey; label: string }> = [
  { key: "market_cap", label: "시가총액" },
  { key: "avg_turnover_20d", label: "20일 평균 거래대금" },
];

// 재무에서 나오는 조건. **두 나라 모두 보여 준다** (docs/infra.md 25.249). 예전에는 "미국은 재무 수집 경로가 없다" 며
// 국내 탭에서만 보였는데, 미국 재무(`jobs/us_financials`, 월 1회)가 생긴 뒤에도 그대로였다. 값이 비었으면
// 결과 아래 안내(`/api/screener` 의 notes)가 이유를 말한다 — 화면에 박은 문장은 데이터가 바뀌어도 안 바뀐다
const RANGE_FIELDS: Array<{ key: RangeKey; label: string; unit: string; hint?: string }> = [
  { key: "per", label: "PER", unit: "배", hint: "낮을수록 싸다" },
  { key: "pbr", label: "PBR", unit: "배" },
  { key: "roe", label: "ROE", unit: "%", hint: "높을수록 자본을 잘 굴린다" },
  { key: "debt_ratio", label: "부채비율", unit: "%", hint: "낮을수록 안전" },
  { key: "operating_margin", label: "영업이익률", unit: "%" },
  { key: "revenue_growth", label: "매출 성장률", unit: "%", hint: "전년 대비" },
  { key: "operating_income_growth", label: "영업이익 성장률", unit: "%" },
];

const METRIC_FIELDS: Array<{ key: RangeKey; label: string; unit: string; hint?: string }> = [
  { key: "cagr", label: "CAGR", unit: "%" },
  // 낙폭의 크기로 입력한다 — 30 이면 "30% 넘게 빠진 적 없음" (25.770)
  { key: "mdd", label: "MDD 낙폭", unit: "%", hint: "크기로 넣습니다 — 30 이면 30% 넘게 빠진 적 없음 (25.773)" },
  { key: "sharpe", label: "샤프", unit: "" },
  { key: "volatility_ann", label: "변동성", unit: "%" },
];


function num(value: number | null, digits = 1): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function eok(value: number | null): string {
  if (value === null || value === undefined) return "-";
  const billions = value / 100_000_000;
  if (billions >= 10_000) return `${(billions / 10_000).toFixed(1)}조`;
  return `${Math.round(billions).toLocaleString("ko-KR")}억`;
}

/** 달러 금액. 10억 달러 이상은 $B, 그 아래는 $M. */
function usd(value: number | null): string {
  if (value === null || value === undefined) return "-";
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  return `$${Math.round(value / 1e6).toLocaleString("en-US")}M`;
}

/**
 * 결과의 열. 넓은 화면의 표와 폰의 카드가 **같은 정의**를 쓴다 — 한쪽만 고치면 두 화면의 숫자가 어긋난다.
 * `short` 는 폰 카드에 쓰는 짧은 이름, `sort` 는 그 열이 정렬 기준일 때 카드에서 굵게 보이려는 것이다.
 */
type ResultColumn = {
  key: string;
  label: string;
  short: string;
  value: (row: ScreenerRow) => string;
  sort?: SortField;
  title?: (row: ScreenerRow) => string | undefined;
};

/**
 * 걸었거나 정렬한 값인데 기본 열에 없으면 **열을 더한다** (docs/infra.md 25.777, 스크리너 감사 — 25.365 와 같은 종류의 남은 곳).
 * 국내 프리셋 "성장" 은 영업이익성장률로 거르고 정렬하는데 그 열이 없어, 조건대로 걸렸는지 화면에서 확인할 수 없었다
 */
export function extraColumns(
  base: ResultColumn[],
  used: ScreenerFilters | null,
  money: (value: number | null) => string,
  기간: string,
): ResultColumn[] {
  if (!used) return [];
  const 후보: Array<{ col: ResultColumn; filter?: keyof ScreenerFilters }> = [
    { col: { key: "operating_income_growth", label: "영업이익성장", short: "영익↑", sort: "operating_income_growth", value: (r) => num(r.operating_income_growth) }, filter: "operating_income_growth" },
    { col: { key: "operating_margin", label: "영업이익률", short: "이익률", sort: "operating_margin", value: (r) => num(r.operating_margin) }, filter: "operating_margin" },
    { col: { key: "debt_ratio", label: "부채비율", short: "부채", value: (r) => num(r.debt_ratio) }, filter: "debt_ratio" },
    { col: { key: "sharpe", label: `샤프${기간}`, short: `샤프${기간}`, sort: "sharpe", value: (r) => num(r.sharpe, 2) }, filter: "sharpe" },
    { col: { key: "volatility_ann", label: `변동성${기간}`, short: `변동성${기간}`, sort: "volatility_ann", value: (r) => (r.volatility_ann === null ? "-" : num(r.volatility_ann * 100)) }, filter: "volatility_ann" },
    { col: { key: "turnover", label: "거래대금", short: "거래대금", sort: "avg_turnover_20d", value: (r) => money(r.avg_turnover_20d) }, filter: "avg_turnover_20d" },
  ];
  const 있음 = new Set(base.map((c) => c.key));
  const 걸림 = (f?: keyof ScreenerFilters) => {
    if (!f) return false;
    const v = used[f] as { min?: number | null; max?: number | null } | undefined;
    return !!v && (v.min != null || v.max != null);
  };
  return 후보
    .filter(({ col, filter }) => !있음.has(col.key) && (걸림(filter) || (col.sort !== undefined && used.sort_by === col.sort)))
    .map(({ col }) => col);
}

function resultColumns(kr: boolean, money: (value: number | null) => string, window: string | null = null): ResultColumn[] {
  // 성과 지표 열에 **어느 기간 값인지** 붙인다 (25.772, 스크리너 감사) — 조건을 걸지 않으면 화면 어디에도 1Y/3Y/5Y 가 없었다
  const 기간 = window ? `(${window})` : "";
  const tail: ResultColumn[] = [
    { key: "cagr", label: `CAGR${기간}`, short: `CAGR${기간}`, sort: "cagr", value: (r) => (r.cagr === null ? "-" : num(r.cagr * 100)) },
    { key: "mdd", label: `MDD${기간}`, short: `MDD${기간}`, sort: "mdd", value: (r) => (r.mdd === null ? "-" : num(r.mdd * 100)) },
    {
      key: "sentiment", label: "감성", short: "감성", sort: "sentiment",
      value: (r) => (r.sentiment === null || r.sentiment === undefined ? "-" : signedInt(r.sentiment)),
      title: (r) => (r.sentiment != null && r.sentiment_date ? `기준 ${r.sentiment_date}` : undefined),
    },
  ];
  if (!kr) {
    // **재무 열도 보인다** (docs/infra.md 25.365). 25.249 부터 미국도 PER·PBR 등으로 **걸러지는데** 열이 없어,
    // 조건대로 걸렸는지 확인할 수 없었다 — 근거를 볼 수 없는 결과다. 미국은 거래대금이 판정 기준이라 함께 둔다
    return [
      { key: "close", label: "종가", short: "종가", value: (r) => (r.close === null ? "-" : `$${num(r.close, 2)}`) },
      { key: "market_cap", label: "시총", short: "시총", sort: "market_cap", value: (r) => money(r.market_cap) },
      { key: "turnover", label: "거래대금", short: "거래대금", sort: "avg_turnover_20d", value: (r) => money(r.avg_turnover_20d) },
      { key: "per", label: "PER", short: "PER", sort: "per", value: (r) => num(r.per) },
      { key: "pbr", label: "PBR", short: "PBR", sort: "pbr", value: (r) => num(r.pbr, 2) },
      { key: "roe", label: "ROE", short: "ROE", sort: "roe", value: (r) => num(r.roe) },
      { key: "revenue_growth", label: "매출성장", short: "매출↑", sort: "revenue_growth", value: (r) => num(r.revenue_growth) },
      ...tail,
    ];
  }
  return [
    { key: "market_cap", label: "시총", short: "시총", sort: "market_cap", value: (r) => money(r.market_cap) },
    { key: "per", label: "PER", short: "PER", sort: "per", value: (r) => num(r.per) },
    { key: "pbr", label: "PBR", short: "PBR", sort: "pbr", value: (r) => num(r.pbr, 2) },
    { key: "roe", label: "ROE", short: "ROE", sort: "roe", value: (r) => num(r.roe) },
    { key: "operating_margin", label: "영업이익률", short: "이익률", sort: "operating_margin", value: (r) => num(r.operating_margin) },
    { key: "revenue_growth", label: "매출성장", short: "매출↑", sort: "revenue_growth", value: (r) => num(r.revenue_growth) },
    { key: "debt_ratio", label: "부채비율", short: "부채", value: (r) => num(r.debt_ratio) },
    ...tail,
  ];
}

/** 입력칸·고르기·단추를 폰에서 손가락 높이(44px)로. 넓은 화면은 전처럼 낮게 */
const CONTROL = "h-11 rounded-lg border border-slate-300 bg-white px-2 text-sm sm:h-auto sm:py-1 dark:border-slate-700 dark:bg-slate-950";

function RangeInput({
  label,
  unit,
  hint,
  value,
  onChange,
}: {
  label: string;
  unit: string;
  hint?: string;
  value: Bounds;
  onChange: (v: Bounds) => void;
}) {
  function parse(raw: string): number | null {
    if (raw.trim() === "") return null;
    const parsed = Number(raw);
    return Number.isNaN(parsed) ? null : parsed;
  }

  const active = value.min !== null || value.max !== null;

  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <span className="min-w-0 text-sm">
        <span className={active ? "font-medium" : "text-slate-600 dark:text-slate-400"}>
          {label}
        </span>
        {hint && (
          <span className="ml-1 text-xs text-slate-400 dark:text-slate-500">{hint}</span>
        )}
      </span>
      <span className="flex shrink-0 items-center gap-1">
        <input
          type="number"
          inputMode="decimal"
          placeholder="최소"
          value={value.min ?? ""}
          onChange={(e) => onChange({ ...value, min: parse(e.target.value) })}
          className={`w-20 text-right tabular-nums outline-none focus:border-slate-900 dark:focus:border-slate-300 ${CONTROL}`}
        />
        <span className="text-xs text-slate-400">~</span>
        <input
          type="number"
          inputMode="decimal"
          placeholder="최대"
          value={value.max ?? ""}
          onChange={(e) => onChange({ ...value, max: parse(e.target.value) })}
          className={`w-20 text-right tabular-nums outline-none focus:border-slate-900 dark:focus:border-slate-300 ${CONTROL}`}
        />
        <span className="w-6 text-xs text-slate-500">{unit}</span>
      </span>
    </div>
  );
}

// 프리셋은 재무 조건이 들어 있어 국내에서만 쓴다.
const PRESETS: Array<{ name: string; hint: string; patch: Partial<ScreenerFilters> }> = [
  {
    name: "저평가 우량",
    hint: "PER 낮고 ROE 높고 빚 적은 회사",
    patch: {
      per: { min: null, max: 12 },
      roe: { min: 10, max: null },
      debt_ratio: { min: null, max: 100 },
      exclude_loss_making: true,
      sort_by: "roe",
    },
  },
  {
    name: "성장",
    hint: "매출과 영업이익이 함께 늘어난 회사",
    patch: {
      revenue_growth: { min: 15, max: null },
      operating_income_growth: { min: 20, max: null },
      exclude_loss_making: true,
      sort_by: "operating_income_growth",
    },
  },
  {
    name: "대형 안정",
    hint: "시총 1조 이상, 빚 적고 흑자",
    patch: {
      market_cap: { min: 10_000, max: null },
      debt_ratio: { min: null, max: 80 },
      exclude_loss_making: true,
      sort_by: "market_cap",
    },
  },
];

export default function ScreenerForm() {
  // 국내·미국 탭. 첫 렌더는 국내, 브라우저에서 마지막 선택을 읽어 바꾼다.
  const [country, setCountry] = useState<Country>("KR");
  const [filters, setFilters] = useState<ScreenerFilters>(() => defaultFilters("KR"));
  /** 마지막 조회에 쓴 상한 — 결과가 그만큼이면 잘렸을 수 있다고 말한다 (25.771) */
  const 조회상한 = useRef<number | null>(null);
  /** 마지막 조회에 쓴 성과 지표 기간 — 결과 열 이름에 붙인다 (25.772) */
  const 조회기간 = useRef<string | null>(null);
  /** 마지막 조회의 조건 — 걸었거나 정렬한 값의 열을 더한다 (25.777) */
  const 조회필터 = useRef<ScreenerFilters | null>(null);
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [conditions, setConditions] = useState<string[]>([]);
  /** 데이터 기준 시각 한 줄 (docs/infra.md 25.364). 폰에서도 보이게 본문에 적는다 */
  const [basis, setBasis] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  // 저장한 조건 (docs/screener.md 2장). 나라마다 따로다
  const [saved, setSaved] = useState<Array<{ id: number; name: string; filters: ScreenerFilters | null }>>([]);
  const [presetName, setPresetName] = useState("");
  const [presetNote, setPresetNote] = useState<string | null>(null);
  // 세부 조건 칸. 폰에서는 접어 두어 결과가 먼저 보이게 한다(처음 열 때 기본 조건으로 이미 찾는다).
  // 넓은 화면은 전처럼 펼쳐 둔다 (docs/pwa.md 3.2, 2단계)
  const [panelOpen, setPanelOpen] = useState(false);
  const resultsRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (window.matchMedia("(min-width: 640px)").matches) setPanelOpen(true);
  }, []);

  // 저장 조건 목록도 **가장 늦게 보낸 요청만** 싣는다 (25.579, 교차검증) — 늦게 온 국내 목록이 미국 탭을 덮으면 누른 조건이
  // 국내 조회를 보내 25.577 의 "$400000.0B" 가 다른 길로 되살아났다
  const 목록번호 = useRef(0);
  // 지금 보이는 나라. 저장·삭제가 끝난 뒤 목록을 다시 읽을 때 **요청을 보낼 때의 나라**가 아니라 이것을 쓴다 (25.582, 교차검증 —
  // 국내에서 저장하는 사이 미국 탭으로 바꾸면 국내 목록 요청이 순번을 이겨 미국 탭에 국내 조건이 실렸다)
  const 지금나라 = useRef<Country>("KR");
  const 읽기실패 = "저장한 조건을 읽지 못했습니다";
  const [presetAlert, setPresetAlert] = useState<string | null>(null);
  const loadPresets = useCallback(async (c: Country) => {
    const 번호 = ++목록번호.current;
    const res = await readJson<{ presets?: Array<{ id: number; name: string; filters: ScreenerFilters | null }> }>(`/api/screener/presets?country=${c}`);
    if (번호 !== 목록번호.current) return;
    // **못 읽은 것을 "없다" 로 만들지 않는다** (25.163 과 같은 모양, 25.577) — 한도에 걸린 날 저장한 조건이 사라진 것처럼
    // 보였다. 못 읽으면 목록은 그대로 두고 까닭을 말한다. 까닭은 접힌 조건 칸 밖에 보인다(폰은 기본으로 접혀 있다, 25.579)
    if (!res.ok) {
      setPresetAlert(`${읽기실패}: ${res.error ?? "알 수 없음"}`);
      return;
    }
    // 읽기 실패 문구만 지운다 — 바로 앞 삭제 실패 알림까지 지우면 보이기도 전에 사라진다
    setPresetAlert((a) => (a?.startsWith(읽기실패) ? null : a));
    setSaved(res.data?.presets ?? []);
  }, []);

  async function savePreset() {
    const name = presetName.trim();
    if (!name) {
      setPresetNote("이름을 넣으세요");
      return;
    }
    const res = await readJson<{ ok?: boolean; errors?: string[] }>("/api/screener/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, filters }),
    });
    setPresetNote(res.ok ? `"${name}" 저장했습니다` : (res.data?.errors?.join(", ") ?? res.error ?? "저장 실패"));
    if (res.ok) {
      setPresetName("");
      void loadPresets(지금나라.current);
    }
  }

  async function deletePreset(id: number, name: string) {
    if (!window.confirm(`"${name}" 을(를) 지울까요?`)) return;
    const res = await readJson(`/api/screener/presets/${id}`, { method: "DELETE" });
    // 조건 버튼은 접힌 칸 밖에 있다 — 그 결과도 밖에 보인다 (25.582)
    if (!res.ok) setPresetAlert(`"${name}" 을(를) 지우지 못했습니다: ${res.error ?? "알 수 없음"}`);
    void loadPresets(지금나라.current);
  }

  function applySaved(p: { id: number; name: string; filters: ScreenerFilters | null }) {
    if (!p.filters) {
      setPresetAlert(`"${p.name}" 은 저장된 조건을 읽을 수 없습니다. 지우고 다시 저장하세요`);
      return;
    }
    // 다른 나라의 조건을 이 탭에서 돌리지 않는다 — 행이 다른 통화로 그려진다 (25.579)
    if (p.filters.country !== country) {
      setPresetAlert(`"${p.name}" 은 다른 나라의 조건입니다`);
      return;
    }
    setFilters(p.filters);
    void search(p.filters);
    void readJson(`/api/screener/presets/${p.id}`, { method: "PATCH" });
  }

  function downloadCsv() {
    // 화면 표와 같은 값을 파일로. 브라우저 안에서만 만든다 — 서버를 거치지 않는다
    const blob = new Blob([toCsv(rows, 조회기간.current)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = csvFileName(country);
    a.click();
    URL.revokeObjectURL(url);
  }

  // **가장 늦게 보낸 조회만 화면에 싣는다** (docs/infra.md 25.577, 감사). 국내 조회(약 3.5초) 중 미국 탭을 누르면 미국이
  // 먼저 끝나고 늦게 온 국내 응답이 미국 탭의 행을 덮어, 삼성전자 시총이 "$400000.0B" 로 보였다
  const 조회번호 = useRef(0);
  // **실패하면 앞 조회의 결과·까닭·기준을 지운다** (docs/infra.md 25.673, 감사). 예전에는 행만 비워 "0종목 · 조건에 맞는
  // 종목이 없습니다. 까닭은 위를 보세요" 옆에 이전 조회의 까닭·기준이 남았고, 네트워크 예외에서는 이전 행까지 새 조건
  // 아래 그대로 남았다(25.163 — 못 읽은 것을 "없다" 로 보이지 않는다)
  const 비우기 = () => {
    setRows([]);
    setConditions([]);
    setBasis(null);
    setNotes([]);
    setElapsed(null);
  };
  const search = useCallback(async (current: ScreenerFilters) => {
    const 번호 = ++조회번호.current;
    setBusy(true);
    setErrors([]);
    try {
      const response = await cachedFetch("/api/screener", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(current),
      });
      const body = await response.json().catch(() => ({}));
      if (번호 !== 조회번호.current) return; // 그 사이 다른 조회를 보냈다 — 늦게 온 옛 응답은 버린다
      if (!response.ok) {
        setErrors(body.errors ?? ["조회에 실패했습니다"]);
        비우기();
        return;
      }
      setRows(body.rows ?? []);
      조회상한.current = current.limit;
      조회기간.current = current.metric_window;
      조회필터.current = current;
      setConditions(body.conditions ?? []);
      setBasis(body.basis ?? null);
      setSectors(body.sectors ?? []);
      setNotes(body.notes ?? []);
      setElapsed(body.elapsed_ms ?? null);
    } catch {
      if (번호 === 조회번호.current) {
        setErrors(["서버에 연결하지 못했습니다"]);
        비우기();
      }
    } finally {
      if (번호 === 조회번호.current) setBusy(false);
    }
  }, []);

  // 처음 열면 마지막에 본 나라의 기본 조건으로 한 번 보여 준다
  useEffect(() => {
    const saved = readSavedCountry();
    const initial = defaultFilters(saved);
    setCountry(saved);
    지금나라.current = saved;
    setFilters(initial);
    void search(initial);
    void loadPresets(saved);
  }, [search, loadPresets]);

  /** 나라를 바꾸면 조건을 그 나라 기본값으로 되돌린다. 단위(억 / $M)가 달라 그대로 두면 뜻이 바뀐다 */
  function switchCountry(next: Country) {
    if (next === country) return;
    const fresh = defaultFilters(next);
    setCountry(next);
    지금나라.current = next;
    saveCountry(next);
    setFilters(fresh);
    setRows([]);
    // 앞 나라의 업종 목록도 지운다 — 새 나라 첫 조회가 실패하면 미국 탭에 국내 업종이 남아 골라도 0건이었다 (25.675, 교차검증)
    setSectors([]);
    // 앞 나라의 저장 조건을 남기지 않는다 — 새 나라 목록을 못 읽으면 앞 나라 조건이 이 탭에 보였다 (25.579, 교차검증)
    setSaved([]);
    setPresetAlert(null);
    void search(fresh);
    void loadPresets(next);
  }

  function patch(updater: (draft: ScreenerFilters) => void) {
    setFilters((prev) => {
      const next = structuredClone(prev);
      updater(next);
      return next;
    });
  }

  function applyPreset(preset: (typeof PRESETS)[number]) {
    const next = { ...defaultFilters(country), ...preset.patch };
    setFilters(next);
    void search(next);
  }

  /**
   * [조회]. 폰에서는 조건 칸을 접고 결과로 내려간다 — 조건을 고친 뒤 결과를 찾아 스크롤하던 것을 없앤다.
   * 전에는 [조회] 가 조건 목록 한가운데 떠 있어 입력칸을 가렸다(2026-09-18 "조회버튼이 부자연스럽다").
   */
  function runSearch() {
    void search(filters);
    if (!window.matchMedia("(min-width: 640px)").matches) {
      setPanelOpen(false);
      requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }

  const kr = country === "KR";
  const unit = MONEY_UNIT[country].label;
  const money = kr ? eok : usd;
  const 기본열 = resultColumns(kr, money, 조회기간.current);
  const columns = [...기본열, ...extraColumns(기본열, 조회필터.current, money, 조회기간.current ? `(${조회기간.current})` : "")];

  return (
    <div className="space-y-3 sm:space-y-4">
      <MarketTabs active={country} onChange={switchCountry} />

      {!kr ? (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          금액 단위는 백만달러($M)입니다. 기본은 유니버스 밖 종목까지 거래대금 순으로 보여 줍니다.
          재무·유니버스가 비어 있으면 결과 아래에 그 이유가 나옵니다(미국 배치는 임시 DB 운영 중 쉽니다).
        </p>
      ) : null}

      {presetAlert ? (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{presetAlert}</p>
      ) : null}

      {/* 자주 쓰는 조건. 누르면 바로 찾는다 — 폰에서 가장 짧은 길이다 */}
      {kr || saved.length > 0 ? (
        <section className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
          <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">자주 쓰는 조건 — 누르면 바로 찾습니다</p>
          <div className="flex flex-wrap gap-2">
            {(kr ? PRESETS : []).map((preset) => (
              <button
                key={preset.name}
                type="button"
                onClick={() => applyPreset(preset)}
                title={preset.hint}
                className="min-h-11 rounded-lg border border-slate-300 px-3 text-sm active:bg-slate-100 sm:min-h-0 sm:py-1.5 sm:hover:bg-slate-50 dark:border-slate-700 dark:active:bg-slate-800"
              >
                {preset.name}
              </button>
            ))}
            {saved.map((p) => (
              <span key={p.id} className="inline-flex items-stretch rounded-lg border border-slate-300 text-sm dark:border-slate-700">
                <button type="button" onClick={() => applySaved(p)} className="min-h-11 px-3 active:bg-slate-100 sm:min-h-0 sm:py-1.5 dark:active:bg-slate-800">
                  {p.name}
                </button>
                <button
                  type="button"
                  aria-label={`${p.name} 지우기`}
                  onClick={() => void deletePreset(p.id, p.name)}
                  className="min-w-11 border-l border-slate-300 px-2 text-slate-400 hover:text-rose-600 sm:min-w-0 dark:border-slate-700"
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              onClick={() => applyPreset({ name: "", hint: "", patch: {} })}
              className="min-h-11 rounded-lg px-3 text-sm text-slate-500 underline sm:min-h-0 sm:py-1.5 dark:text-slate-400"
            >
              조건 비우기
            </button>
          </div>
        </section>
      ) : null}

      {/* 세부 조건. 폰에서는 접어 두고, [조회] 는 이 칸의 끝에 둔다 */}
      <section className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <button
          type="button"
          onClick={() => setPanelOpen((v) => !v)}
          aria-expanded={panelOpen}
          className="flex min-h-12 w-full items-center justify-between gap-2 px-3 text-left"
        >
          <span className="text-sm font-medium">
            {panelOpen ? "조건 접기" : "조건 바꾸기"}
            <span className="ml-2 text-xs font-normal text-slate-500 dark:text-slate-400">
              {conditions.length > 0 ? `지금 ${conditions.length}개 적용` : "기본 조건"}
            </span>
          </span>
          <Icon name="chevron" className={`h-5 w-5 shrink-0 text-slate-400 transition-transform ${panelOpen ? "rotate-90" : ""}`} />
        </button>

        {panelOpen ? (
          <div className="border-t border-slate-100 p-3 dark:border-slate-800">
            <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 pb-3 dark:border-slate-800">
              <label className="flex items-center gap-2 text-sm">
                <span className="text-slate-600 dark:text-slate-400">시장</span>
                <select
                  value={filters.market}
                  onChange={(e) =>
                    patch((d) => {
                      d.market = e.target.value as ScreenerFilters["market"];
                    })
                  }
                  className={CONTROL}
                >
                  {MARKETS_BY_COUNTRY[country].map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex items-center gap-2 text-sm">
                <span className="text-slate-600 dark:text-slate-400">업종</span>
                <select
                  value={filters.sector}
                  onChange={(e) =>
                    patch((d) => {
                      d.sector = e.target.value;
                    })
                  }
                  className={`max-w-[12rem] ${CONTROL}`}
                >
                  <option value="ALL">전체</option>
                  {/* 지금 고른 업종이 목록에 없으면(다른 나라 조건을 불러왔을 때) 그대로 보여 준다 */}
                  {(sectors.includes(filters.sector) || filters.sector === "ALL" ? sectors : [filters.sector, ...sectors]).map((sector) => (
                    <option key={sector} value={sector}>
                      {sector}
                    </option>
                  ))}
                </select>
                {sectors.length === 0 ? <span className="text-xs text-slate-400">업종 자료 없음</span> : null}
              </label>

              <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
                <input
                  type="checkbox"
                  checked={filters.universe_only}
                  onChange={(e) => patch((d) => { d.universe_only = e.target.checked; })}
                  className="size-5 sm:size-4"
                />
                <span className="text-slate-600 dark:text-slate-400">유니버스만</span>
              </label>

              {kr ? (
                <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
                  <input
                    type="checkbox"
                    checked={filters.exclude_loss_making}
                    onChange={(e) =>
                      patch((d) => { d.exclude_loss_making = e.target.checked; })
                    }
                    className="size-5 sm:size-4"
                  />
                  <span className="text-slate-600 dark:text-slate-400">적자 제외</span>
                </label>
              ) : null}
            </div>

            <div className="mt-2 divide-y divide-slate-100 dark:divide-slate-800">
              {MONEY_FIELDS.map((field) => (
                <RangeInput
                  key={field.key}
                  label={field.label}
                  unit={unit}
                  value={filters[field.key] as Bounds}
                  onChange={(v) =>
                    patch((d) => {
                      (d[field.key] as Bounds) = v;
                    })
                  }
                />
              ))}
              {RANGE_FIELDS.map((field) => (
                <RangeInput
                  key={field.key}
                  label={field.label}
                  unit={field.unit}
                  hint={field.hint}
                  value={filters[field.key] as Bounds}
                  onChange={(v) =>
                    patch((d) => {
                      (d[field.key] as Bounds) = v;
                    })
                  }
                />
              ))}
            </div>

            <div className="mt-3 border-t border-slate-100 pt-3 dark:border-slate-800">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-medium text-slate-600 dark:text-slate-400">
                  성과 지표
                </span>
                <select
                  value={filters.metric_window}
                  onChange={(e) =>
                    patch((d) => {
                      d.metric_window = e.target.value as ScreenerFilters["metric_window"];
                    })
                  }
                  className={CONTROL}
                >
                  <option value="1Y">1년</option>
                  <option value="3Y">3년</option>
                  <option value="5Y">5년</option>
                </select>
              </div>
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {METRIC_FIELDS.map((field) => (
                  <RangeInput
                    key={field.key}
                    label={field.label}
                    unit={field.unit}
                    hint={field.hint}
                    value={filters[field.key] as Bounds}
                    onChange={(v) =>
                      patch((d) => {
                        (d[field.key] as Bounds) = v;
                      })
                    }
                  />
                ))}
              </div>
            </div>

            {/* 센티먼트는 5팩터와 별도 축이다 (CLAUDE.md). 여기서도 따로 묶어 둔다 */}
            <div className="mt-3 border-t border-slate-100 pt-3 dark:border-slate-800">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-400">뉴스 감성 (−100~+100)</span>
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                <RangeInput
                  label="감성 점수"
                  unit="점"
                  hint="수집한 상위 종목에만 값이 있습니다"
                  value={filters.sentiment as Bounds}
                  onChange={(v) =>
                    patch((d) => {
                      d.sentiment = v;
                    })
                  }
                />
              </div>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              <label className="flex items-center gap-2 text-sm">
                <span className="text-slate-600 dark:text-slate-400">정렬</span>
                <select
                  value={filters.sort_by}
                  onChange={(e) =>
                    patch((d) => {
                      d.sort_by = e.target.value as SortField;
                    })
                  }
                  className={CONTROL}
                >
                  {/* 25.249 부터 미국도 재무가 있다 — 재무 정렬을 숨기지 않는다 (docs/infra.md 25.365) */}
                  {Object.entries(SORT_FIELDS)
                    .map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                </select>
              </label>
              <button
                type="button"
                onClick={() => patch((d) => { d.sort_desc = !d.sort_desc; })}
                className={CONTROL}
              >
                {filters.sort_desc ? "내림차순" : "오름차순"}
              </button>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-slate-600 dark:text-slate-400">최대</span>
                <input
                  type="number"
                  value={filters.limit}
                  onChange={(e) =>
                    patch((d) => {
                      d.limit = Math.max(1, Math.min(500, Number(e.target.value) || 100));
                    })
                  }
                  className={`w-20 text-right ${CONTROL}`}
                />
                <span className="text-xs text-slate-500">종목</span>
              </label>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              <input
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="지금 조건에 이름 붙여 저장"
                maxLength={30}
                className={`min-w-0 flex-1 sm:w-48 sm:flex-none ${CONTROL}`}
              />
              <button type="button" onClick={() => void savePreset()} className={CONTROL}>
                저장
              </button>
              {presetNote ? <span className="w-full text-xs text-slate-500">{presetNote}</span> : null}
            </div>

            {/* 폰: 칸의 끝에 붙는다(떠 있지 않다). 넓은 화면: 전처럼 화면 아래에 붙는다 */}
            <div className="mt-3 bg-white pt-1 sm:sticky sm:bottom-0 sm:z-10 sm:border-t sm:border-slate-200 sm:py-3 dark:bg-slate-900 dark:sm:border-slate-800">
              <button
                type="button"
                onClick={runSearch}
                disabled={busy}
                className="h-12 w-full rounded-lg bg-slate-900 px-4 text-base font-medium text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
              >
                {busy ? "찾는 중" : "조회"}
              </button>
            </div>
          </div>
        ) : null}
      </section>

      {errors.length > 0 && (
        <div
          role="alert"
          className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          {errors.map((e, i) => (
            <p key={i}>{e}</p>
          ))}
        </div>
      )}

      {notes.map((note, i) => (
        <div
          key={i}
          className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300"
        >
          {note}
        </div>
      ))}

      <section
        ref={resultsRef}
        className="scroll-mt-16 rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 p-3 dark:border-slate-800">
          <p className="text-sm font-medium">
            {/* 조회 실패면 "0종목" 이 아니다 — 아래 "조건에 맞는 종목이 없다는 뜻이 아닙니다" 와 맞게 (25.675, 교차검증) */}
            {busy ? "찾는 중" : errors.length > 0 ? "조회 실패" : `${rows.length}종목`}
            {!busy && errors.length === 0 && limitNote(rows.length, 조회상한.current) && (
              <span className="ml-2 text-xs font-normal text-amber-700 dark:text-amber-300">
                {limitNote(rows.length, 조회상한.current)}
              </span>
            )}
            {elapsed !== null && (
              <span className="ml-2 text-xs font-normal text-slate-400">
                {elapsed}ms
              </span>
            )}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {conditions.join(" · ")}
          </p>
          {basis ? <p className="text-xs text-slate-500 dark:text-slate-400">{basis}</p> : null}
          {rows.length > 0 ? (
            <button type="button" onClick={downloadCsv} className="hidden rounded-lg border border-slate-300 px-2.5 py-1 text-xs sm:block dark:border-slate-700">
              CSV 내보내기
            </button>
          ) : null}
        </div>

        {rows.length === 0 ? (
          // **"범위를 넓혀 보세요" 는 늘 맞는 말이 아니다** (docs/infra.md 25.149).
          // 건 조건의 값이 아직 없어서 0건이면 범위를 아무리 넓혀도 안 나온다.
          // 무엇 때문인지는 경로가 가려 `notes` 로 보내 준다 — 위에 이미 떠 있다
          <p className="p-6 text-center text-sm text-slate-500 dark:text-slate-400">
            {errors.length > 0
              ? "조회하지 못했습니다 — 위 오류를 보세요. 조건에 맞는 종목이 없다는 뜻이 아닙니다."
              : `조건에 맞는 종목이 없습니다.${notes.length === 0 ? " 범위를 넓혀 보세요." : " 까닭은 위를 보세요."}`}
          </p>
        ) : (
          <>
            {/* 폰: 종목 카드. 표를 옆으로 밀지 않아도 한 종목의 숫자가 한눈에 들어온다 (2026-09-18) */}
            <ul className="divide-y divide-slate-100 sm:hidden dark:divide-slate-800">
              {rows.map((row) => (
                <li key={row.stock_id}>
                  <Link href={`/stocks/${row.stock_id}`} className="block px-3 py-3 active:bg-slate-50 dark:active:bg-slate-800">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-medium">{row.name}</span>
                      <span className="shrink-0 text-xs text-slate-400">
                        {row.ticker}
                        {row.fiscal_year ? ` · ${fiscalYearLabel(kr ? "KR" : "US", row.fiscal_year)}` : ""}
                      </span>
                    </div>
                    <dl className="mt-1.5 grid grid-cols-3 gap-x-3 gap-y-1 text-xs">
                      {columns.map((col) => (
                        <div key={col.key} className="flex justify-between gap-1" title={col.title?.(row)}>
                          <dt className="text-slate-400">{col.short}</dt>
                          <dd className={`tabular-nums ${col.sort === filters.sort_by ? "font-semibold" : ""}`}>{col.value(row)}</dd>
                        </div>
                      ))}
                    </dl>
                  </Link>
                </li>
              ))}
            </ul>

            {/* 넓은 화면: 표 */}
            <div className="hidden overflow-x-auto sm:block">
              <table className="w-full text-sm">
                <thead className="border-b border-slate-100 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                  <tr>
                    <th className="p-2 text-left font-medium">종목</th>
                    {columns.map((col) => (
                      <th key={col.key} className="p-2 text-right font-medium">{col.label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50 dark:divide-slate-800">
                  {rows.map((row) => (
                    <tr key={row.stock_id}>
                      <td className="p-2">
                        <Link href={`/stocks/${row.stock_id}`} className="font-medium hover:underline">{row.name}</Link>
                        <span className="ml-1 text-xs text-slate-400">{row.ticker}</span>
                        {row.fiscal_year ? (
                          <span className="ml-1 text-xs text-slate-400">
                            {fiscalYearLabel(kr ? "KR" : "US", row.fiscal_year)}
                          </span>
                        ) : null}
                      </td>
                      {columns.map((col) => (
                        <td key={col.key} className="p-2 text-right tabular-nums" title={col.title?.(row)}>
                          {col.value(row)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {kr ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          밸류에이션은 가장 최근 사업보고서 기준입니다. 분기를 섞지 않습니다.
          표에 표시된 연도가 그 기준 회계연도입니다.
        </p>
      ) : (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          거래대금은 야후가 주지 않아 종가 × 거래량으로 추정한 값입니다.
        </p>
      )}
    </div>
  );
}
