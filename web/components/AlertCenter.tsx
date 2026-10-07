"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { DEFAULT_FILTER, TRIGGER_LABEL, TRIGGER_ORDER, alertsTabLabel, filterAlerts, hiddenUnreadNote, sentLabel, sessionLabel, unreadIdsShown, type AlertFilter } from "@/lib/alerts";
import { intradayVerdict } from "@/lib/health";
import { bodyOf, readJson } from "@/lib/http";
import { MONITOR_STALE_DAYS, activeSession, staleTargetsNote } from "@/lib/intraday";
import { userDateOf } from "@/lib/market";
import { money } from "@/lib/portfolio";
import { WATCH_GONE, WATCH_PRICE_MAX_RATIO, watchPriceIgnored } from "@/lib/watch";

/** 알림 센터: [알림] [관심 종목]. 숫자는 배치·장중 경로가 저장한 그대로다. 필터와 읽음은 lib/alerts. */

type Stock = { id: number; ticker: string; name: string; currency: string; market: string };

interface Alert {
  id: number; market: string; trade_date: string; trigger_type: string; message: string; data: string;
  created_at: string; sent_at: string | null; ticker: string; stock_id: number; is_read: number;
}

interface Watch {
  id: number; stock_id: number; ticker: string; country: string; currency: string; name: string;
  target_buy_price: number | null; alert_enabled: number; memo: string | null; last_close: number | null; last_date: string | null;
}

const TRIGGER = TRIGGER_LABEL;

/** 고르기: 폰에서 손가락 높이(44px). 넓은 화면은 전처럼 낮게 (docs/pwa.md 3.2) */
const SELECT = "h-11 rounded-lg border border-slate-300 bg-white px-2 text-sm sm:h-auto sm:rounded sm:px-1.5 sm:py-0.5 sm:text-[11px] dark:border-slate-700 dark:bg-slate-900";
/** 목록 줄 안의 작은 단추(읽음·빼기): 폰에서 누를 자리를 넓힌다 */
const TAP = "min-h-10 px-2 sm:min-h-0 sm:px-0";

function kst(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}


export default function AlertCenter() {
  const [tab, setTab] = useState<"alerts" | "watch">("alerts");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [targets, setTargets] = useState<Array<{ market: string; n: number; built_at: string }>>([]);
  const [sessions, setSessions] = useState<Array<{ market: string; next_date: string; next_open: string; next_close?: string }>>([]);
  const [beats, setBeats] = useState<Array<{ market: string; called_at: string; outcome: string; calls_today: number }>>([]);
  const [beatsError, setBeatsError] = useState<string | null>(null);
  const [watch, setWatch] = useState<Watch[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  // **못 읽은 것을 "없다" 로 적지 않는다** (docs/infra.md 25.74·25.584). 읽기가 실패하면 빈 목록이 "아직 알림이 없습니다"·
  // "국내 감시 없음 · 세션 정보 없음 · 호출 기록 없음" 으로 보여, 한도에 걸린 날 "크론이 안 불린다" 로 잘못 진단하게 했다
  const [alertsFailed, setAlertsFailed] = useState(false);
  const [totals, setTotals] = useState<{ total: number; unread: number } | null>(null);
  const [watchFailed, setWatchFailed] = useState(false);
  const [filter, setFilter] = useState<AlertFilter>(DEFAULT_FILTER);

  const markRead = async (ids: number[]) => {
    // 먼저 화면을 바꾸고 서버에 알린다. 실패하면 다시 읽어 되돌린다
    // 전체·안 읽은 수도 같이 줄인다 — 목록만 바꾸면 탭이 옛 안 읽음 수를 보이고 "목록 밖에 N건 더" 가 새로 떴다 (25.805, 교차검증)
    const 새로읽음 = alerts.filter((a) => !a.is_read && (ids.length === 0 || ids.includes(a.id))).length;
    setTotals((t) => (t ? { ...t, unread: Math.max(0, t.unread - 새로읽음) } : t));
    setAlerts((prev) => prev.map((a) => (ids.length === 0 || ids.includes(a.id) ? { ...a, is_read: 1 } : a)));
    const res = await readJson("/api/alerts", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, read: true }),
    });
    if (!res.ok) {
      // 다시 읽은 뒤에 적는다 — 먼저 적으면 `load()` 의 `setError(null)` 이 곧바로 지웠다 (25.592, 교차검증)
      const 실패 = `읽음 처리 실패: ${res.error}`;
      await load();
      setError((e) => (e ? `${e} · ${실패}` : 실패));
    }
  };

  const [readAt, setReadAt] = useState<Date | null>(null);
  const load = useCallback(async () => {
    // 두 경로를 따로 읽는다. 하나가 실패해도 나머지는 그린다 (lib/http.readJson 주석)
    const [a, w] = await Promise.all([
      readJson<Record<string, unknown>>("/api/alerts", undefined, { cache: true }),
      readJson<{ watchlist?: Watch[] }>("/api/watchlist", undefined, { cache: true }),
    ]);
    if (a.ok && a.data) {
      const aj = a.data as {
        alerts?: Alert[];
        targets?: Array<{ market: string; n: number; built_at: string }>;
        sessions?: Array<{ market: string; next_date: string; next_open: string; next_close?: string }>;
        heartbeats?: Array<{ market: string; called_at: string; outcome: string; calls_today: number }>;
        heartbeats_error?: string | null;
        notes?: string[];
        totals?: { total: number; unread: number } | null;
        server_time?: string;
      };
      setAlerts(aj.alerts ?? []);
      setTotals(aj.totals ?? null);
      setTargets(aj.targets ?? []);
      setSessions(aj.sessions ?? []);
      setBeats(aj.heartbeats ?? []);
      setBeatsError(aj.heartbeats_error ?? null);
      setNotes(aj.notes ?? []);
      setReadAt(aj.server_time ? new Date(aj.server_time) : null);
    }
    if (w.ok && w.data) setWatch(w.data.watchlist ?? []);
    setAlertsFailed(!a.ok);
    setWatchFailed(!w.ok);
    // 둘 다 실패하면 둘 다 적는다 — 예전에는 관심 종목 오류를 버렸다 (25.584)
    const 오류 = [a.ok ? null : `알림 ${a.error}`, w.ok ? null : `관심 종목 ${w.error}`].filter(Boolean);
    setError(오류.length ? 오류.join(" · ") : null);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 판정은 **그 자료를 읽은 시각**으로 한다 (docs/infra.md 25.917, 감사). 예전에는 그릴 때의 시각이었는데(25.144) 25.879 의 10분 캐시가
  // 끼면서, 08:55 에 읽은 하트비트(어제 15:55)를 09:05 에 다시 그려 "장중 감시가 멈췄습니다 — 17시간 전" 이 빨갛게 떴다.
  // 서버가 시각을 안 주는 옛 응답이면 그릴 때의 시각
  const now = readAt ?? new Date();

  return (
    <div>
      {error ? <p className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">{error}</p> : null}
      {notes.map((n) => <p key={n} className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">{n}</p>)}

      <p className="mb-3 text-xs text-slate-500">
        {alertsFailed ? "감시 목록·다음 정규장·호출 기록을 읽지 못했습니다" : null}
        {(alertsFailed ? [] : ["KR", "US"]).map((m) => {
          const t = targets.find((x) => x.market === m);
          const s = sessions.find((x) => x.market === m);
          return (
            <span key={m} className="mr-3">
              {m === "KR" ? "국내" : "미국"} 감시 {t ? `${t.n}종목` : "없음"}
              {/* 관심만 걸린 종목은 장중 경로가 따로 읽어 감시한다 — 수에 없으면 "감시 없음" 을 감시가 안 도는 것으로 읽었다 (25.816, 감사).
                  보유·추천과 겹칠 수 있어 더하지 않고 따로 적는다. 관심 목록을 못 읽었으면 적지 않는다 */}
              {!watchFailed && (() => {
                const n = watch.filter((w) => w.country === m && Number(w.alert_enabled) === 1).length;
                return n > 0 ? ` · 관심 ${n}` : null;
              })()}
              {/*
                **목록이 언제 만들어진 것인지 말한다** (docs/infra.md 25.162).
                `built_at` 은 여기까지 실어 오고도 **화면에 안 내놓고 있었다** — 종목 수만
                적으면 3주 전 목록과 오늘 목록이 똑같아 보인다. 장중 경로는 Actions 밖에서
                돌아서, 일일 배치가 멈춰도 옛 매수 구간으로 계속 알린다
              */}
              {t?.built_at
                ? (() => {
                    const note = staleTargetsNote([{ market: m, built_at: t.built_at }], now);
                    return note ? (
                      <span
                        className="ml-1 font-medium text-amber-700 dark:text-amber-300"
                        title={`감시 목록은 일일 배치가 거래일마다 다시 만든다. ${MONITOR_STALE_DAYS}일(거래일 사이 최장 간격)을 넘겼다`}
                      >
                        ⚠ 목록 {userDateOf(t.built_at)}
                      </span>
                    ) : (
                      <span className="ml-1"> (목록 {userDateOf(t.built_at)})</span>
                    );
                  })()
                : null}
              {s ? ` · 다음 정규장 ${kst(s.next_open)}` : " · 세션 정보 없음"}
              {(() => {
                const b = beats.find((x) => x.market === m);
                if (b) return ` · 마지막 호출 ${kst(b.called_at)} (${b.outcome.replace("skipped:", "")})`;
                // **못 읽은 것을 "안 불렸다" 로 적지 않는다** (docs/infra.md 25.74).
                // "호출 기록 없음" 은 외부 크론이 안 부른다는 진단으로 읽힌다(todo-user 2번)
                return beatsError ? " · 호출 기록을 읽지 못했습니다" : " · 호출 기록 없음";
              })()}
              {/*
                **장중인데 멈췄는지를 여기서 판정한다** (docs/infra.md 25.144).
                전에는 시각만 회색으로 적었다 — 사람이 "지금 장중인가" 와 "5분이 얼마나
                지났나" 를 매번 암산해야 했다. 못 읽었을 때(beatsError)는 판정하지 않는다
              */}
              {!beatsError && s?.next_close
                ? (() => {
                    const 장중 = activeSession(
                      [{ date: s.next_date, open_utc: s.next_open, close_utc: s.next_close }],
                      now,
                    ) !== null;
                    const v = intradayVerdict(m, beats.find((x) => x.market === m), 장중, now);
                    if (v.tone === "ok" || !v.text) return null;
                    const 색 = v.tone === "warn" ? "text-amber-700 dark:text-amber-300" : v.tone === "bad" ? "text-rose-700 dark:text-rose-300" : "";
                    return <span className={`ml-1 font-medium ${색}`} title={v.why}>{v.tone === "mute" ? "" : `⚠ ${v.text}`}</span>;
                  })()
                : null}
            </span>
          );
        })}
      </p>

      {/* 폰: 두 칸이 폭을 반씩, 손가락 높이. 넓은 화면: 전처럼 알약 모양 */}
      <div role="tablist" aria-label="알림" className="mb-3 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1 sm:flex sm:gap-2 sm:bg-transparent sm:p-0 dark:bg-slate-900 sm:dark:bg-transparent">
        {([["alerts", alertsFailed ? "알림 ?" : alertsTabLabel(alerts, totals)], ["watch", watchFailed ? "관심 종목 ?" : `관심 종목 ${watch.length}`]] as const).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
            className={`min-h-11 rounded-lg text-sm sm:min-h-0 sm:rounded-full sm:border sm:px-3 sm:py-1 sm:text-xs ${tab === key
              ? "bg-white font-semibold text-slate-900 shadow-sm sm:border-slate-900 sm:bg-slate-900 sm:text-white dark:bg-slate-700 dark:text-white sm:dark:border-slate-100 sm:dark:bg-slate-100 sm:dark:text-slate-900"
              : "text-slate-600 sm:border-slate-300 dark:text-slate-300 sm:dark:border-slate-700"}`}>
            {label}
          </button>
        ))}
      </div>

      {/* 다시 읽다 실패하면 옛 목록이 남는다 — 그렇다고 말한다 (docs/infra.md 25.588) */}
      {(tab === "alerts" ? alertsFailed && alerts.length > 0 : watchFailed && watch.length > 0) ? (
        <p className="mb-2 text-xs text-amber-700 dark:text-amber-300">새로 읽지 못해 아래는 앞서 읽은 목록입니다</p>
      ) : null}
      {tab === "alerts" ? (
        alerts.length === 0 ? <p className="py-4 text-sm text-slate-500">{alertsFailed ? "알림을 읽지 못했습니다." : "아직 알림이 없습니다."}</p> : (
          <>
          <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
            <select aria-label="시장" value={filter.market} onChange={(e) => setFilter({ ...filter, market: e.target.value as AlertFilter["market"] })} className={SELECT}>
              <option value="ALL">시장 전체</option>
              <option value="KR">국내</option>
              <option value="US">미국</option>
            </select>
            <select aria-label="트리거" value={filter.trigger} onChange={(e) => setFilter({ ...filter, trigger: e.target.value })} className={SELECT}>
              <option value="ALL">트리거 전체</option>
              {TRIGGER_ORDER.map((t) => <option key={t} value={t}>{TRIGGER_LABEL[t]}</option>)}
            </select>
            <label className="flex min-h-11 items-center gap-1.5 text-sm sm:min-h-0 sm:gap-1 sm:text-[11px]">
              <input type="checkbox" className="size-5 sm:size-auto" checked={filter.unreadOnly} onChange={(e) => setFilter({ ...filter, unreadOnly: e.target.checked })} />
              안 읽음만
            </label>
            {/* **지금 보이는 안 읽은 알림만** 읽음으로 (docs/infra.md 25.584, 감사). `ids: []` 는 서버에서 "안 읽은 전부" 라
                화면을 연 뒤 들어온 알림·필터로 가린 알림·200건 밖의 알림까지 한 번도 보이지 않은 채 읽음이 됐다. 서버 상한 200 */}
            {unreadIdsShown(alerts, filter).length > 0 ? (
              <button type="button" onClick={() => void markRead(unreadIdsShown(alerts, filter))} className="ml-auto h-11 rounded-lg border border-slate-300 px-3 text-sm sm:h-auto sm:rounded sm:px-2 sm:py-0.5 sm:text-[11px] dark:border-slate-700">
                모두 읽음
              </button>
            ) : null}
          </div>
          {hiddenUnreadNote(alerts, totals) ? <p className="mb-2 text-xs text-amber-700 dark:text-amber-300">{hiddenUnreadNote(alerts, totals)}</p> : null}
          {filterAlerts(alerts, filter).length === 0 ? <p className="py-3 text-xs text-slate-500">조건에 맞는 알림이 없습니다.</p> : null}
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
            {filterAlerts(alerts, filter).map((a) => (
              <li key={a.id} className={`px-3 py-2 ${a.is_read ? "opacity-70" : ""}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {!a.is_read ? <span aria-label="안 읽음" className="size-2 shrink-0 self-center rounded-full bg-sky-500" /> : null}
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] dark:bg-slate-800">{TRIGGER[a.trigger_type] ?? a.trigger_type}</span>
                  <Link href={`/stocks/${a.stock_id}`} className="hover:underline">{a.message}</Link>
                  {!a.is_read ? (
                    <button type="button" onClick={() => void markRead([a.id])} className={`ml-auto text-xs text-slate-500 underline sm:text-[11px] ${TAP}`}>읽음</button>
                  ) : null}
                </div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {a.market === "KR" ? "국내" : "미국"}{sessionLabel(a.market, a.trade_date)} · {kst(a.created_at)} · {sentLabel(a.sent_at, kst)}
                  {(() => {
                    try {
                      const d = JSON.parse(a.data) as { quote_time?: string; url?: string };
                      return (
                        <>
                          {d.quote_time ? ` · 시세 시각 ${kst(d.quote_time)}` : ""}
                          {d.url ? <> · <a className="underline" href={d.url} target="_blank" rel="noreferrer">공시 보기</a></> : null}
                        </>
                      );
                    } catch {
                      return null;
                    }
                  })()}
                </div>
              </li>
            ))}
          </ul>
          </>
        )
      ) : (
        <Watchlist items={watch} failed={watchFailed} onChanged={() => void load()} />
      )}
    </div>
  );
}

function Watchlist({ items, failed, onChanged }: { items: Watch[]; failed: boolean; onChanged: () => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Stock[]>([]);
  const [pick, setPick] = useState<Stock | null>(null);
  const [price, setPrice] = useState("");
  const [err, setErr] = useState<string | null>(null);
  // 저장은 됐는데 알릴 것(종가 이상 목표가) — 오류와 다른 색으로 (25.587, 교차검증: 빨간 칸이라 저장 실패처럼 보였다)
  const [warn, setWarn] = useState<string | null>(null);

  useEffect(() => {
    if (!q.trim()) {
      setFound([]);
      return;
    }
    const timer = setTimeout(async () => {
      const j = bodyOf<{ stocks?: Stock[] }>(await readJson(`/api/stocks/search?q=${encodeURIComponent(q.trim())}`));
      setFound(j.stocks ?? []);
    }, 250);
    return () => clearTimeout(timer);
  }, [q]);

  const add = async () => {
    if (!pick) return setErr("종목을 고르세요");
    // **읽지 못한 값을 "비움" 으로 보내지 않는다** (docs/infra.md 25.584, 감사). 숫자 칸은 "65,000" 을 빈 글자로 넘겨
    // `target_buy_price: null` 이 되고, 이미 있던 목표가가 지워져 장중 목표가 알림이 멈췄다. 글자 칸으로 받아 쉼표를 빼고,
    // 읽을 수 없으면 막는다. 비워 두면 목표가 키를 보내지 않는다 — 있던 목표가를 그대로 둔다(지우기는 목록의 "목표가 지우기", 25.587)
    const 글 = price.trim().replace(/,/g, "");
    const 값 = 글 ? Number(글) : undefined;
    if (값 !== undefined && !(Number.isFinite(값) && 값 > 0)) return setErr(`목표 매수가 "${price}" 를 숫자로 읽지 못했습니다`);
    const r = await readJson("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(값 === undefined ? { stock_id: pick.id } : { stock_id: pick.id, target_buy_price: 값 }),
    });
    const j = bodyOf<{ errors?: string[]; warnings?: string[] }>(r);
    if (!r.ok) {
      setWarn(null); // 앞 경고가 오류 줄과 함께 남지 않게 (25.589)
      return setErr(j.errors?.join(", ") ?? "저장 실패");
    }
    setPick(null); setPrice(""); setErr(null);
    setWarn(j.warnings?.length ? j.warnings.join(" · ") : null);
    onChanged();
  };

  // **쓰기의 결과를 본다** (docs/infra.md 25.317). 예전에는 응답을 버려 실패해도 말 없이 원래대로 돌아갔다
  const patch = async (id: number, body: Record<string, unknown>) => {
    const r = await readJson(`/api/watchlist/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) {
      setWarn(null); // 앞 경고가 오류와 함께 남지 않게 (25.592)
      setErr(`저장 실패: ${r.error ?? "알 수 없음"}`);
    } else {
      setErr(null); // 성공하면 앞 오류를 지운다 (25.589)
      const w = bodyOf<{ warnings?: string[] }>(r).warnings;
      setWarn(w?.length ? w.join(" · ") : null);
    }
    onChanged();
  };

  const remove = async (id: number) => {
    if (!confirm("관심 종목에서 뺄까요?")) return;
    const r = await readJson(`/api/watchlist/${id}`, { method: "DELETE" });
    // 이미 빠졌으면 바라던 결과다 — "삭제 실패" 가 아니다 (25.822, 교차검증. `removeWatch` 와 같게)
    if (!r.ok && r.error !== WATCH_GONE) {
      setWarn(null);
      setErr(`삭제 실패: ${r.error ?? "알 수 없음"}`);
    } else {
      setErr(null); // 성공하면 앞 오류를 지운다 (25.592)
    }
    onChanged();
  };

  const input = "h-11 rounded-lg border border-slate-300 px-2 text-sm sm:h-auto sm:rounded sm:py-1 dark:border-slate-700 dark:bg-slate-900";
  return (
    <div>
      <section className="mb-4 rounded-xl border border-slate-200 p-3 dark:border-slate-800">
        <h3 className="mb-2 text-sm font-medium">관심 종목 추가</h3>
        {pick ? (
          <p className="mb-2 text-sm">{pick.name} <span className="text-xs text-slate-500">{pick.ticker}</span>{" "}
            <button type="button" className="text-xs underline" onClick={() => setPick(null)}>바꾸기</button></p>
        ) : (
          <div className="relative mb-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="종목 이름·티커" className={`w-full ${input}`} />
            {found.length > 0 ? (
              <ul className="absolute z-10 mt-1 max-h-48 w-full overflow-auto rounded border border-slate-200 bg-white text-sm shadow dark:border-slate-700 dark:bg-slate-900">
                {found.map((s) => (
                  <li key={s.id}>
                    <button type="button" className="min-h-11 w-full px-3 py-2 text-left hover:bg-slate-100 sm:min-h-0 sm:px-2 sm:py-1 dark:hover:bg-slate-800"
                      onClick={() => { setPick(s); setQ(""); setFound([]); }}>
                      {s.name} <span className="text-xs text-slate-500">{s.ticker} · {s.market}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        )}
        <label className="flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
          목표 매수가 (비우면 있던 목표가를 그대로 둡니다 — 지우기는 아래 목록의 "목표가 지우기")
          <input type="text" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={`w-full sm:w-auto ${input}`} />
        </label>
        {err ? <p className="mt-2 text-xs text-rose-600">{err}</p> : null}
        {warn ? <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{warn}</p> : null}
        <button type="button" onClick={add} className="mt-3 h-12 w-full rounded-lg bg-slate-900 text-base text-white sm:mt-2 sm:h-auto sm:w-auto sm:rounded sm:px-3 sm:py-1.5 sm:text-sm dark:bg-slate-100 dark:text-slate-900">추가</button>
      </section>

      {items.length === 0 ? <p className="text-sm text-slate-500">{failed ? "관심 종목을 읽지 못했습니다." : "관심 종목이 없습니다."}</p> : (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
          {items.map((w) => (
            <li key={w.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <Link href={`/stocks/${w.stock_id}`} className="font-medium hover:underline">{w.name}</Link>
              {/* 통화를 붙인다 — 단위 없이 숫자만 두면 미국 종목에 원화를 넣게 된다 (docs/infra.md 25.360) */}
              <span className="text-xs text-slate-500">{w.ticker} · 종가 {w.last_close === null ? "-" : money(w.last_close, w.currency)} ({w.last_date ?? "-"})</span>
              <span className="text-xs text-slate-500">목표 매수가 {w.target_buy_price === null ? "없음" : money(w.target_buy_price, w.currency)}</span>
              {/* 장중 감시가 버리는 목표가는 그렇다고 — 살아 있는 값처럼 보였다 (25.813, 감사) */}
              {watchPriceIgnored(w.target_buy_price, w.last_close) && (
                <span className="text-xs text-amber-700 dark:text-amber-300">장중 감시 제외 — 마지막 종가의 {WATCH_PRICE_MAX_RATIO}배 넘음</span>
              )}
              {/* 목표가만 지우는 길 (docs/infra.md 25.587, 교차검증). 25.584 가 "비우면 그대로 둔다" 로 바꾼 뒤 화면에서 지울 방법이 없었다 */}
              {w.target_buy_price !== null ? (
                <button type="button" className={`text-xs text-slate-500 underline ${TAP}`}
                  onClick={() => { if (confirm("목표 매수가만 지울까요?")) void patch(w.id, { target_buy_price: null }); }}>
                  목표가 지우기
                </button>
              ) : null}
              <label className="ml-auto flex min-h-10 items-center gap-1.5 text-xs sm:min-h-0 sm:gap-1">
                <input type="checkbox" className="size-5 sm:size-auto" checked={w.alert_enabled === 1} onChange={(e) => void patch(w.id, { alert_enabled: e.target.checked })} />
                알림
              </label>
              <button type="button" className={`text-xs text-slate-500 underline ${TAP}`} onClick={() => void remove(w.id)}>빼기</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
