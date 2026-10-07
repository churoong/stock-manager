import { NextResponse } from "next/server";
import { notifyQuotaOnce } from "@/lib/quotaNotice";
import { newsTopN } from "@/lib/settings";
import { batch, execute, ifMissingTable, quotaReason, rowsToObjects } from "@/lib/db";
import { recordHeartbeat } from "@/lib/heartbeat";
import { tokenMatches } from "@/lib/intraday";
import { FEED_TIMEOUT_MS, FEED_USER_AGENT, looksLikeFeed } from "@/lib/news";
import {
  KR_ALIASES, KR_TARGETS, NEWS_INSERT_KR, YNA_FEEDS, matchStocks, mergeFeeds, newsKrOutcome, withAliases, type NamedStock,
} from "@/lib/newsKr";

/**
 * 국내 뉴스 수집 (docs/sentiment.md 1.2). cron-job.org 가 1시간마다 부른다.
 *
 *   GET /api/cron/news-kr   헤더 x-cron-secret: <CRON_SECRET>
 *
 * 연합뉴스 산업·경제·증권 RSS 세 개를 받아, 대상 종목 이름이 든 기사를 종목마다 한 줄씩 저장한다.
 * 저장은 제목·주소·시각뿐. 채점(KorFinASC)과 집계는 Python 워크플로(sentiment-kr.yml)가 한다.
 * 쓰는 표는 news · cron_heartbeats 뿐이다.
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
  try {
    // 설정 화면·배치와 같은 규칙으로 읽는다 (25.629) — 범위 밖은 기본값, 0 만 끔
    const topN = newsTopN(
      rowsToObjects<{ value: string }>(await execute("SELECT value FROM settings WHERE key = 'sentiment_target_top_n'"))[0]
        ?.value,
    );
    if (!Number.isFinite(topN) || topN <= 0) {
      await recordHeartbeat("news", "KR", now, "skipped:감성 대상 0", {});
      return NextResponse.json({ skipped: "설정 sentiment_target_top_n 이 0 이라 수집하지 않습니다" });
    }
    const 대상 = rowsToObjects<NamedStock>(await execute(KR_TARGETS));
    // 별칭(NAVER↔네이버 등)도 같은 종목으로 맞춘다 (25.840). 표가 아직 없을 때만(0043 전) 약칭만 쓴다
    const 별칭 = rowsToObjects<{ stock_id: number; alias: string }>(
      await execute(KR_ALIASES).catch(ifMissingTable({ columns: [], rows: [], affectedRows: 0 })),
    );
    const stocks = withAliases(대상, 별칭);
    if (stocks.length === 0) {
      await recordHeartbeat("news", "KR", now, "skipped:대상 없음", {});
      return NextResponse.json({ skipped: "국내 감성 대상 종목이 없습니다" });
    }

    const xmls: string[] = [];
    const errors: string[] = [];
    for (const url of YNA_FEEDS) {
      try {
        const response = await fetch(url, {
          headers: { "User-Agent": FEED_USER_AGENT, Accept: "application/rss+xml" },
          cache: "no-store",
          signal: AbortSignal.timeout(FEED_TIMEOUT_MS),
        });
        const text = response.ok ? await response.text() : "";
        // 200 이어도 RSS 가 아니면 실패로 센다 (25.640) — 피드 셋이 다 그러면 호출 기록이 error 가 된다
        if (response.ok && looksLikeFeed(text)) xmls.push(text);
        else errors.push(`${url.split("/").pop()} ${response.ok ? "RSS 아님" : `http:${response.status}`}`);
      } catch (error) {
        errors.push(`${url.split("/").pop()} ${error instanceof Error ? error.message : "실패"}`);
      }
    }

    const items = mergeFeeds(xmls);
    const stamp = now.toISOString();
    const rows: Array<{ sql: string; args: Array<string | number> }> = [];
    for (const item of items) {
      for (const stock of matchStocks(item.title, stocks)) {
        rows.push({ sql: NEWS_INSERT_KR, args: [stock.stock_id, item.title, item.url, item.published_at, stamp] });
      }
    }
    const results = rows.length ? await batch(rows) : [];
    const added = results.reduce((sum, r) => sum + r.affectedRows, 0);
    const detail = { feeds: xmls.length, items: items.length, targets: 대상.length, aliases: stocks.length - 대상.length, matched: rows.length, added, errors };
    // 피드를 받았는데 **항목이 0건**이면 실패다 (docs/infra.md 25.836, 감사) — 연합 피드는 늘 120건 안팎이라, 0 이면 `pubDate` 같은 형식이 바뀌어
    // `parseRss` 가 전부 버린 것이다. 예전에는 `checked` 로 남아 기사 유입이 0 인 채 /status 가 며칠 초록이었다
    const outcome = newsKrOutcome(xmls.length, items.length);
    await recordHeartbeat("news", "KR", now, outcome, items.length || !xmls.length ? detail : { ...detail, note: "피드를 받았는데 읽힌 기사가 0건입니다 — 형식이 바뀌었을 수 있습니다" });
    return NextResponse.json(detail);
  } catch (error) {
    const message = error instanceof Error ? error.message : "실패";
    const quota = quotaReason(message);
    if (quota) {
      // 한도는 고장이 아니다. 500 을 쌓으면 cron-job.org 가 작업을 끌 수 있어 200 으로 답한다.
      // 호출 기록(heartbeat)도 쓰기라 남기지 않는다 (docs/infra.md 25.6). 멈췄다는 사실은 하루 한 번 알린다 (25.876)
      await notifyQuotaOnce("국내 뉴스", quota);
      return NextResponse.json({ skipped: quota });
    }
    if (/no such table/i.test(message)) {
      return NextResponse.json({ skipped: "뉴스 표가 아직 없습니다. Actions → 마이그레이션 적용 을 돌리세요" });
    }
    await recordHeartbeat("news", "KR", now, "error", { error: message });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
