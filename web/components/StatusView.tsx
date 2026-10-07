"use client";

import { useCallback, useEffect, useState } from "react";
import { STATUS_LABEL, adjustQueueNote, dayNote, cronVerdict, freshnessVerdict, isStuck, sinceText, usageAmount, usageLabel, usageRatio, usageTone } from "@/lib/health";
import { readJson } from "@/lib/http";
import { userTimeOf } from "@/lib/market";

/**
 * 시스템 상태 화면 (docs/health.md 4장, Step 17).
 *
 * 무언가 이상할 때 여는 화면이다. 그래서 **가장 먼저 "무엇이 늦었나"** 를 보여 주고,
 * 그 아래에 판단의 재료(최근 배치·한도·신선도·크론 호출)를 둔다.
 *
 * 화면은 계산하지 않는다. 지연(초)과 사용률(%)만 저장된 값에서 나눈다.
 */

interface Watch {
  job: string; market: string; label: string; schedule: string; local_date: string; trading_day: boolean | null; paused?: boolean;
  due_utc: string | null; overdue: boolean; done_today: boolean;
  last_success_at: string | null; last_success_trade_date: string | null;
}

interface Run {
  id: number; job_name: string; market: string | null; trade_date: string | null; trigger_source: string | null;
  scheduled_for: string | null; started_at: string; finished_at: string | null; status: string;
  delay_seconds: number | null; step_log: string | null; error_text: string | null;
}

interface Usage {
  api_name: string; window_type: string; window_start: string; call_count: number; limit_value: number | null;
  warn_at_pct: number; state: string; last_call_at: string | null;
  /** 지금 창(오늘 UTC·이번 달)인가 (25.593) */
  current?: boolean;
}

interface HealthAlert {
  job: string; market: string; local_date: string; kind: string; message: string;
  created_at: string; sent_at: string | null;
}

interface Cron {
  job: string; market: string; called_at: string; outcome: string; detail: string | null;
  calls_today: number; day: string;
}

interface Data {
  now: string;
  watches: Watch[];
  runs: Run[];
  usage: Usage[];
  health_alerts: HealthAlert[];
  cron: Cron[];
  freshness: Array<{ key: string; label: string; value: string | null }>;
  /** 미국 수정주가 재수집 대기열 (docs/infra.md 25.165). 비어 있는 것이 정상이다 */
  adjust_queue?: { n: number; oldest: string | null } | null;
  /** 지금 쓰는 DB. D1 에서 쉬는 칸은 낡은 것이 정상이다 (docs/infra.md 25.140) */
  db_backend?: string;
  /**
   * 못 읽은 부분과 그 이유 (docs/infra.md 25.163). 빈 목록이 **"아직 없음" 인지
   * "못 읽음" 인지**를 가른다 — 이 화면이 "크론이 부르고 있나" 를 답하는 자리라,
   * 못 읽고 "호출 기록 없음" 이라 적으면 사람이 멀쩡한 크론을 다시 등록하러 간다.
   */
  read_errors?: Record<string, string>;
}

export default function StatusView() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const read = await readJson<Data>("/api/status");
      if (!read.ok) throw new Error(read.error ?? "불러오지 못했습니다");
      setData(read.data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "불러오지 못했습니다");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950 dark:text-rose-300">{error}</p>;
  }
  if (!data) return <p className="py-4 text-sm text-slate-500">불러오는 중...</p>;

  const now = new Date(data.now);
  const 못읽음 = (key: string) => data.read_errors?.[key] ?? null;
  const watchdog = data.cron.find((c) => c.job === "health");
  // **감시의 감시** (docs/infra.md 25.141). 이 한 줄이 멈추면 무응답 알림이 통째로 멈춘다.
  // "한 번도 없음" 은 전부터 말했지만, **불리다가 멈춘 것**은 회색 시각 한 줄이었다
  const watchdogVerdict = watchdog ? cronVerdict(watchdog, now) : null;
  const watchdogTint = !watchdogVerdict || watchdogVerdict.tone === "ok" ? "text-slate-500"
    : watchdogVerdict.tone === "warn" ? "text-amber-700 dark:text-amber-300"
    : "text-rose-700 dark:text-rose-300";
  const late = data.watches.filter((w) => w.overdue);
  return (
    <div className="flex flex-col gap-4">
      <section>
        <H2>무응답 감시</H2>
        {/*
          **거래일인지 못 읽었으면 "안 늦었다" 가 아니다** (docs/infra.md 25.163).
          `sessions` 를 못 읽으면 예전에는 모든 대상이 `trading_day: false` 가 되어 **쉬는 날처럼**
          보였다 — 늦은 것이 있어도 조용했다. 지금은 `null`(모름, 25.552)이고 이 띠가 까닭을 말한다
        */}
        {못읽음("sessions") ? (
          <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            거래일 달력을 읽지 못해 오늘이 거래일인지 가리지 못했습니다 — 아래 &ldquo;늦음&rdquo; 판정을 믿지 마세요 ({못읽음("sessions")})
          </p>
        ) : null}
        {late.length > 0 ? (
          <p className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:bg-rose-950 dark:text-rose-200">
            {late.map((w) => w.label).join(" · ")} 가 오늘 마감까지 성공하지 못했습니다. 텔레그램으로도 알립니다.
          </p>
        ) : null}
        {/* 폰: 대상마다 카드. 여섯 칸 표는 폰에서 옆으로 넘쳤다 (docs/pwa.md 3.2, 5단계) */}
        <ul className="flex flex-col gap-2 text-xs sm:hidden">
          {data.watches.map((w) => (
            <li key={`${w.job}-${w.market}`} className="rounded-xl border border-slate-200 px-3 py-2 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 font-medium">{w.label}</span>
                {w.paused ? <Tag tone="mute">D1 운영 중 쉼</Tag>
                  : w.trading_day === null ? <Tag tone="bad">달력 없음</Tag>
                  : !w.trading_day ? <Tag tone="mute">기대 없음</Tag>
                  : w.done_today ? <Tag tone="ok">성공</Tag>
                  : w.overdue ? <Tag tone="bad">무응답</Tag>
                  : <Tag tone="mute">대기</Tag>}
              </div>
              <p className="mt-0.5 text-slate-500">{w.schedule}</p>
              <p className="mt-0.5">
                오늘 {w.local_date}{dayNote(w.trading_day, w.paused)} · 마감 {w.due_utc ? localTime(w.due_utc, w.market) : "-"}
              </p>
              <p className="mt-0.5">
                마지막 성공 {w.last_success_trade_date ?? "없음"}
                <span className="ml-1 text-slate-400">({sinceText(w.last_success_at, now)})</span>
              </p>
            </li>
          ))}
        </ul>
        <div className="hidden overflow-x-auto rounded-xl border border-slate-200 text-xs sm:block dark:border-slate-800">
          <table className="w-full">
            <thead className="bg-slate-50 text-left text-slate-500 dark:bg-slate-900">
              <tr>
                <th className="px-3 py-1.5 font-medium">대상</th>
                <th className="px-3 py-1.5 font-medium">예약</th>
                <th className="px-3 py-1.5 font-medium">오늘</th>
                <th className="px-3 py-1.5 font-medium">마감</th>
                <th className="px-3 py-1.5 font-medium">상태</th>
                <th className="px-3 py-1.5 font-medium">마지막 성공</th>
              </tr>
            </thead>
            <tbody>
              {data.watches.map((w) => (
                <tr key={`${w.job}-${w.market}`} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="whitespace-nowrap px-3 py-1.5 font-medium">{w.label}</td>
                  <td className="px-3 py-1.5 text-slate-500">{w.schedule}</td>
                  <td className="whitespace-nowrap px-3 py-1.5">{w.local_date}{dayNote(w.trading_day, w.paused)}</td>
                  <td className="whitespace-nowrap px-3 py-1.5">{w.due_utc ? localTime(w.due_utc, w.market) : "-"}</td>
                  <td className="px-3 py-1.5">
                    {w.paused ? <Tag tone="mute">D1 운영 중 쉼</Tag>
                  : w.trading_day === null ? <Tag tone="bad">달력 없음</Tag>
                  : !w.trading_day ? <Tag tone="mute">기대 없음</Tag>
                      : w.done_today ? <Tag tone="ok">성공</Tag>
                      : w.overdue ? <Tag tone="bad">무응답</Tag>
                      : <Tag tone="mute">대기</Tag>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5">
                    {w.last_success_trade_date ?? "없음"}
                    <span className="ml-1 text-slate-400">({sinceText(w.last_success_at, now)})</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={`mt-1 text-[11px] ${watchdogTint}`} title={watchdogVerdict?.why}>
          마지막 감시 호출: {watchdog && watchdog.outcome !== "never"
            ? `${userTimeOf(watchdog.called_at)} KST (${sinceText(watchdog.called_at, now)}) · ${watchdog.outcome} · 오늘 ${watchdog.calls_today}회`
            // **못 읽었으면 "등록하세요" 라고 시키지 않는다** (docs/infra.md 25.163)
            : 못읽음("cron")
              ? `호출 기록을 읽지 못했습니다 — ${못읽음("cron")}`
              : "아직 없음 — cron-job.org 에 1시간마다 /api/cron/health 를 등록해야 합니다"}
          {watchdogVerdict && watchdogVerdict.tone !== "ok" && watchdog?.outcome !== "never"
            ? " — 감시가 멈춘 듯합니다. cron-job.org 의 작업과 헤더를 확인하세요"
            : ""}
        </p>
        {/* 못 읽은 것을 "알림 없음" 으로 만들지 않는다 (docs/infra.md 25.593, 감사 — 경로는 read_errors 를 채우는데 화면이 안 봤다) */}
        {못읽음("health_alerts") ? <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">최근 무응답 알림을 읽지 못했습니다 — {못읽음("health_alerts")}</p> : null}
        {data.health_alerts.length > 0 ? (
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer select-none text-slate-500">최근 알림 {data.health_alerts.length}건 (무응답 · 무거운 읽기 기록)</summary>
            <ul className="mt-1 space-y-1">
              {data.health_alerts.map((a) => (
                <li key={`${a.job}-${a.market}-${a.local_date}-${a.kind}`} className="whitespace-pre-line rounded-lg border border-slate-200 px-2 py-1 dark:border-slate-800">
                  {a.message}
                  <span className="ml-1 text-slate-400">{a.sent_at ? "보냄" : "미발송"}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>

      {/* 수동 실행 칸은 뺐다 (2026-10-02 사용자 지시: "화면상에서는 배치를 수동으로 돌릴 수 있는 기능을 빼줘", docs/infra.md 25.879).
          배치는 예약(cron-job.org·Actions)으로만 돈다. 꼭 돌려야 하면 GitHub Actions 화면의 Run workflow 를 쓴다 */}
      <section>
        <H2>API 한도</H2>
        {/* 지금 어느 DB 를 쓰는지 적는다 — auto 모드에서 D1 임시 운영인지 알 수 없었다 (docs/infra.md 25.599, 감사) */}
        {data.db_backend ? (
          <p className="mb-1 text-[11px] text-slate-500">
            지금 쓰는 DB: {data.db_backend === "d1" ? "Cloudflare D1 (임시 운영 — 미국 배치는 쉰다)" : "Turso"}
          </p>
        ) : null}
        {data.usage.length === 0 ? (
          <Empty>{못읽음("usage") ? `호출 기록을 읽지 못했습니다: ${못읽음("usage")}` : "최근 기록이 없습니다. 배치가 돌면 채워집니다."}</Empty>
        ) : (
          <ul className="flex flex-col gap-1">
            {data.usage.map((u) => {
              const ratio = usageRatio(u.call_count, u.limit_value);
              // 저장된 `blocked` 를 먼저 본다 — DART 020 처럼 셈과 무관하게 막힌 것을 초록으로 보였다 (25.593, 감사).
              // 지난 창은 회색 — 지금 한도가 아니다
              const tone = u.current === false ? "unknown" : u.state === "blocked" ? "blocked" : usageTone(ratio, u.warn_at_pct);
              const bar = { ok: "bg-emerald-500", warn: "bg-amber-500", blocked: "bg-rose-600", unknown: "bg-slate-400" }[tone];
              return (
                <li key={`${u.api_name}-${u.window_type}-${u.window_start}`} className="rounded-xl border border-slate-200 px-3 py-1.5 text-xs dark:border-slate-800">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{usageLabel(u.api_name)}</span>
                    <span className="text-slate-500">
                      {u.window_type} {u.window_start}
                      {u.current === false ? " · 지난 창(지금 기록 없음)" : ""}
                      {u.state === "blocked" && u.current !== false ? " · 막힘" : ""}
                    </span>
                    <span className="ml-auto">
                      {usageAmount(u.api_name, u.call_count)} / {usageAmount(u.api_name, u.limit_value)}
                      {ratio !== null ? ` (${(ratio * 100).toFixed(0)}%)` : ""}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full rounded bg-slate-100 dark:bg-slate-800">
                    <div className={`h-1.5 rounded ${bar}`} style={{ width: `${Math.min((ratio ?? 0) * 100, 100)}%` }} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <p className="mt-1 text-[11px] text-slate-500">
          80% 에서 경고, 100% 에서 중단합니다 (CLAUDE.md 비용 규칙). GitHub Actions 분은 여기서 읽을 수 없습니다 —{" "}
          <a href="https://github.com/settings/billing" target="_blank" rel="noopener noreferrer" className="underline">GitHub 사용량</a>
          에서 확인하세요.
        </p>
      </section>

      <section>
        <H2>데이터 신선도</H2>
        {/* 못 읽은 칸도 `null` 이라 "없음" 과 똑같이 보인다. 몇 칸인지는 말한다 (25.163) */}
        {못읽음("freshness") ? (
          <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            {못읽음("freshness")} — 그 칸의 &ldquo;없음&rdquo; 은 자료가 없다는 뜻이 아닙니다
          </p>
        ) : null}
        {/*
          **어긋난 줄 알면서 말하지 않고 있었다** (docs/infra.md 25.165).
          감지는 일일 배치의 `log.info` 에만 남고, 우리는 Actions 로그를 못 읽는다(25.17).
          대기 중인 종목의 옛 수정종가는 지금 기준과 어긋나 있고 모멘텀·성과지표가 그 값을 쓴다
        */}
        {못읽음("adjust_queue") ? (
          <p className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
            수정주가 재수집 대기열을 읽지 못했습니다 — 비어 있는(정상) 것인지 알 수 없습니다 (25.593)
          </p>
        ) : null}
        {(() => {
          const q = adjustQueueNote(data.adjust_queue ?? null, now);
          if (!q) return null;
          const 색 = q.late
            ? "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200"
            : "bg-slate-50 text-slate-600 dark:bg-slate-900 dark:text-slate-300";
          return <p className={`mb-2 rounded-lg px-3 py-2 text-xs ${색}`}>{q.late ? "⚠ " : ""}{q.text}</p>;
        })()}
        <div className="grid grid-cols-2 gap-1 text-xs sm:grid-cols-3">
          {data.freshness.map((f) => {
            // **날짜만 늘어놓으면 아무도 못 읽는다** (docs/infra.md 25.140).
            // 칸마다 "며칠이면 늦은 것인가" 가 달라서, 판정은 health.ts 가 한다.
            // 근거(`why`)는 마우스를 올리면 펼쳐진다 — 판정만 있고 근거가 없으면 안 된다
            const v = freshnessVerdict(f.key, f.value, now, data.db_backend ?? "");
            return (
              <div key={f.key} className="rounded-lg border border-slate-200 px-2 py-1 dark:border-slate-800" title={v.why}>
                <div className="text-slate-500">{f.label}</div>
                <div className="flex items-baseline gap-1">
                  <span className="font-medium">{f.value ?? "없음"}</span>
                  <Tag tone={v.tone}>{v.text}</Tag>
                </div>
              </div>
            );
          })}
        </div>
        <p className="mt-1 text-[11px] text-slate-500">
          칸을 짚으면 판정의 근거가 나옵니다. 자료마다 채워지는 주기가 달라 잣대도 다릅니다.
        </p>
      </section>

      <section>
        <H2>최근 배치</H2>
        <ul className="flex flex-col gap-1">
          {data.runs.map((r) => (
            <li key={r.id} className="rounded-xl border border-slate-200 px-3 py-1.5 text-xs dark:border-slate-800">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium">{r.job_name}</span>
                <span className="text-slate-500">{r.market ?? "-"} · {r.trade_date ?? "-"} · {r.trigger_source ?? "-"}</span>
                <Tag tone={runTone(r, now)}>
                  {isStuck(r.status, r.started_at, now) ? "멈춘 듯 (기록 미완료)" : STATUS_LABEL[r.status] ?? r.status}
                </Tag>
                <span className="ml-auto text-slate-500">
                  {userTimeOf(r.started_at)}
                  {r.finished_at ? ` → ${userTimeOf(r.finished_at).slice(11)}` : ""}
                  {r.delay_seconds ? ` · 지연 ${Math.round(r.delay_seconds / 60)}분` : ""}
                </span>
              </div>
              {r.error_text ? <p className="mt-0.5 text-rose-600 dark:text-rose-400">{r.error_text}</p> : null}
              {r.step_log ? (
                <details className="mt-0.5">
                  <summary className="cursor-pointer select-none text-slate-500">단계별 로그</summary>
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-[11px] text-slate-600 dark:text-slate-300">
                    {pretty(r.step_log)}
                  </pre>
                </details>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <H2>크론 호출</H2>
        {data.cron.length === 0 ? (
          <Empty>{못읽음("cron") ? `호출 기록을 읽지 못했습니다: ${못읽음("cron")}` : "아직 호출 기록이 없습니다."}</Empty>
        ) : (
          <ul className="flex flex-col gap-1 text-xs">
            {data.cron.map((c) => {
              // **시각만 적어 두면 아무도 못 읽는다** (docs/infra.md 25.141).
              // `health` 가 `1970-01-01 · never` 로 앉아 있어도 회색 글씨였다 —
              // 그 한 줄의 뜻은 "무응답 감시가 아예 안 돈다" 다
              const v = cronVerdict(c, now);
              return (
                <li key={`${c.job}-${c.market}`} className="flex flex-wrap items-baseline gap-x-2 rounded-xl border border-slate-200 px-3 py-1.5 dark:border-slate-800" title={v.why}>
                  <span className="font-medium">{c.job} {c.market}</span>
                  <span className="text-slate-500">{c.outcome === "never" ? "호출된 적 없음" : `${userTimeOf(c.called_at)} · ${c.outcome} · 오늘 ${c.calls_today}회`}</span>
                  <span className="ml-auto">{v.text ? <Tag tone={v.tone}>{v.text}</Tag> : null}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

/** 끝나지 않고 굳은 행은 "실행 중" 이 아니라 문제로 보여 준다 */
function runTone(run: Run, now: Date): "ok" | "warn" | "bad" | "mute" {
  if (isStuck(run.status, run.started_at, now)) return "bad";
  if (run.status === "success") return "ok";
  if (run.status === "partial") return "warn";
  if (run.status === "running" || run.status === "skipped" || run.status === "dryrun") return "mute";
  return "bad";
}

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-1 text-sm font-semibold">{children}</h2>;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl border border-slate-200 px-3 py-3 text-xs text-slate-500 dark:border-slate-800">{children}</p>;
}

function Tag({ tone, children }: { tone: "ok" | "warn" | "bad" | "mute"; children: React.ReactNode }) {
  const style = {
    ok: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    warn: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
    bad: "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
    mute: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  }[tone];
  return <span className={`rounded px-1.5 py-0.5 text-[11px] ${style}`}>{children}</span>;
}

/** 마감 시각은 그 시장 현지 시각으로 보여 준다. UTC 로 적으면 매번 머리로 더해야 한다 */
function localTime(utc: string, market: string): string {
  const zone = market === "KR" ? "Asia/Seoul" : "America/New_York";
  try {
    return new Date(utc).toLocaleString("ko-KR", { timeZone: zone, hour: "2-digit", minute: "2-digit" });
  } catch {
    return utc.slice(11, 16);
  }
}

function pretty(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
