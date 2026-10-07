import { NextResponse } from "next/server";
import { notifyQuotaOnce } from "@/lib/quotaNotice";
import { newsTopN } from "@/lib/settings";
import { batch, execute, quotaReason, rowsToObjects } from "@/lib/db";
import { tokenMatches } from "@/lib/intraday";
import { recordHeartbeat } from "@/lib/heartbeat";
import { watchTursoReturn } from "@/lib/tursoWatch";
import {
  FEED_TIMEOUT_MS,
  FEED_USER_AGENT,
  FETCH_LOG_UPSERT,
  NEWS_INSERT,
  NEXT_TARGET,
  filterForSymbol,
  looksLikeFeed,
  nasdaqFeedUrl,
  parseRss,
  refetchCutoff,
  retryCutoff,
} from "@/lib/news";

/**
 * 미국 뉴스 수집 (docs/sentiment.md 1장). cron-job.org 가 **매시** 부르고, 한 번에 **한 종목**의 나스닥 RSS 를 받는다.
 * 1분이던 것을 2026-10-02 사용자 결정으로 매시로 줄였다 (docs/infra.md 25.880). 하루 24종목이다.
 *
 *   GET /api/cron/news   헤더 x-cron-secret: <CRON_SECRET>
 *
 * 왜 한 종목씩: 나스닥 robots.txt 의 Crawl-delay 30. Actions 로 150종목을 차례로 받으면 월 한도를 넘긴다.
 * 쓰는 표는 news · news_fetch_log · cron_heartbeats, 그리고 10분에 한 번 settings 한 줄
 * (Turso 복귀 표시, `watchTursoReturn()`, 25.12)이다. 점수는 Python 배치가 매긴다.
 * 2026-09-26 까지 settings 를 빼고 "뿐이다" 라고 적혀 있었다(docs/infra.md 25.195).
 */
export const dynamic = "force-dynamic";


export async function GET(request: Request) {
  if (!process.env.CRON_SECRET) {
    return NextResponse.json({ error: "CRON_SECRET 이 설정되지 않았습니다" }, { status: 503 });
  }
  if (!tokenMatches(request.headers.get("x-cron-secret"), process.env.CRON_SECRET)) {
    return NextResponse.json({ error: "토큰이 맞지 않습니다" }, { status: 401 });
  }
  const now = new Date();
  // Turso 가 풀렸는지 본다 (docs/infra.md 25.12). 매시 불리므로 부를 때마다 본다 — 예전 "10분에 한 번" 거름은
  // 매시 호출의 분이 0·10·20… 이 아니면 영영 안 봤다 (25.880). 뉴스 수집과 무관하고, 실패해도 수집을 막지 않는다
  await watchTursoReturn().catch(() => undefined);
  try {
    // 이 설정은 이제 켜고 끄는 스위치다. 몇 종목을 후보로 삼을지는 배치가 정한다
    // (jobs/monitor_targets.news_target_rows, docs/infra.md 24절)
    // 설정 화면·배치와 같은 규칙으로 읽는다 (25.629) — 범위 밖은 기본값, 0 만 끔
    const topN = newsTopN(
      rowsToObjects<{ value: string }>(
        await execute("SELECT value FROM settings WHERE key = 'sentiment_target_top_n'"),
      )[0]?.value,
    );
    if (!Number.isFinite(topN) || topN <= 0) {
      await recordHeartbeat("news", "US", now, "skipped:감성 대상 0", {});
      return NextResponse.json({ skipped: "설정 sentiment_target_top_n 이 0 이라 수집하지 않습니다" });
    }
    const target = rowsToObjects<{ stock_id: number; yahoo_symbol: string; name: string; last_fetched_at: string | null }>(
      await execute(NEXT_TARGET, [refetchCutoff(now), retryCutoff(now)]),
    )[0];
    if (!target) {
      await recordHeartbeat("news", "US", now, "skipped:받을 종목 없음", {});
      return NextResponse.json({ skipped: "모든 대상을 최근에 받았습니다" });
    }

    let status = "ok";
    let items: ReturnType<typeof parseRss> = [];
    try {
      const response = await fetch(nasdaqFeedUrl(target.yahoo_symbol), {
        headers: { "User-Agent": FEED_USER_AGENT, Accept: "application/rss+xml" },
        cache: "no-store",
        // cron-job.org 기본 제한이 30초다. 나스닥이 붙잡아 두면 여기서 끊고 기록만 남긴다
        signal: AbortSignal.timeout(FEED_TIMEOUT_MS),
      });
      if (!response.ok) status = `http:${response.status}`;
      else {
        const text = await response.text();
        // 200 이어도 RSS 가 아니면 실패다 — 봇 차단 페이지를 "기사 0건" 으로 적지 않는다 (25.640)
        if (looksLikeFeed(text)) items = filterForSymbol(parseRss(text), target.yahoo_symbol, target.name);
        else status = "not-rss";
      }
    } catch (error) {
      status = `fetch-error:${error instanceof Error ? error.message : "?"}`;
    }

    const stamp = now.toISOString();
    const results = items.length
      ? await batch(
          items.map((i) => ({
            sql: NEWS_INSERT,
            args: [target.stock_id, i.title, i.url, i.published_at, i.publisher, stamp],
          })),
        )
      : [];
    const added = results.reduce((sum, r) => sum + r.affectedRows, 0);
    await execute(FETCH_LOG_UPSERT, [target.stock_id, stamp, status, items.length, added]);
    await recordHeartbeat("news", "US", now, status === "ok" ? "checked" : "error", { symbol: target.yahoo_symbol, status, items: items.length, added });
    return NextResponse.json({ symbol: target.yahoo_symbol, status, items: items.length, added });
  } catch (error) {
    const message = error instanceof Error ? error.message : "실패";
    const quota = quotaReason(message);
    if (quota) {
      // 한도는 고장이 아니다. 500 을 쌓으면 cron-job.org 가 작업을 끌 수 있어 200 으로 답한다.
      // 호출 기록(heartbeat)도 쓰기라 남기지 않는다 (docs/infra.md 25.6). 멈췄다는 사실은 하루 한 번 알린다 (25.876)
      await notifyQuotaOnce("미국 뉴스", quota);
      return NextResponse.json({ skipped: quota });
    }
    if (/no such table/i.test(message)) {
      return NextResponse.json({ skipped: "뉴스 표가 아직 없습니다. Actions → 마이그레이션 적용 을 돌리세요" });
    }
    await recordHeartbeat("news", "US", now, "error", { error: message });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
