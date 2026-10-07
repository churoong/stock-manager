import { NextResponse } from "next/server";
import { notifyQuotaOnce } from "@/lib/quotaNotice";
import { batch, currentBackend, execute, quotaReason, rowsToObjects } from "@/lib/db";
import {
  HEALTH_ALERT_INSERT, LAST_SUCCESS, PENDING_HEALTH_ALERTS, SESSIONS_AROUND, TODAY_HEALTH_ALERTS,
  WATCHES, dueAlerts, localDate, type LastSuccess, type Market, type Session,
} from "@/lib/health";
import { tokenMatches } from "@/lib/intraday";
import { sendTelegram } from "@/lib/telegram";
import { recordHeartbeat } from "@/lib/heartbeat";
import { watchTursoReturn } from "@/lib/tursoWatch";

/**
 * 무응답 감시 (docs/health.md, Step 17). cron-job.org 가 1시간마다 부른다.
 *
 *   GET /api/cron/health     헤더 x-cron-secret: <CRON_SECRET>
 *
 * 순서: 토큰 확인 → 세션·마지막 성공·오늘 보낸 알림 읽기 → 판단(순수 함수) → 저장 → 발송
 *
 * **배치가 실패한 것은 배치가 알린다. 여기가 보는 것은 "아무 일도 없었다" 다.**
 * Actions 예약으로 감시하면 Actions 가 멈출 때 감시도 멈추므로 웹 경로에 뒀다
 * (사용자 결정 2026-09-17, docs/health.md 1장).
 *
 * **쓰기 대상은 셋이다** — health_alerts · cron_heartbeats · settings 한 줄
 * (`db_return_requested_at`, Turso 복귀 표시. `watchTursoReturn()` 이 쓴다, 25.12).
 * 점수·신호·매매는 건드리지 않는다.
 *
 * 2026-09-25 까지 이 줄은 **둘 뿐이라고** 적혀 있었다(docs/infra.md 25.191).
 * `settings` 쓰기는 일부러 넣은 것이고 백업에서도 사유와 함께 빠져 있는데(25.156),
 * 그것을 지킨다는 검사가 **이 파일의 글자만** 보고 있어서 import 를 타고 나가는
 * 쓰기를 처음부터 못 봤다. 이제 `__tests__/cronWrites.test.ts` 가 import 를 타고 본다.
 */
export const dynamic = "force-dynamic";

/** 감시가 실제로 돌고 있는지. 장중 경로와 같은 방식으로 한 줄을 덮어쓴다. 실패해도 본 작업을 막지 않는다 */

export async function GET(request: Request) {
  // 비밀이 빠진 것을 "토큰이 맞지 않습니다" 로 보이지 않게 — 다른 크론 세 경로와 같게 503 (docs/infra.md 25.655, 감사)
  if (!process.env.CRON_SECRET) {
    return NextResponse.json({ error: "CRON_SECRET 이 설정되지 않았습니다" }, { status: 503 });
  }
  if (!tokenMatches(request.headers.get("x-cron-secret"), process.env.CRON_SECRET)) {
    return NextResponse.json({ error: "토큰이 맞지 않습니다" }, { status: 401 });
  }

  const now = new Date();
  // Turso 가 풀렸는지도 본다 (docs/infra.md 25.12). 미국 뉴스 크론도 매시 보지만, 그 크론이 멈춰도 매시 한 번은 본다
  await watchTursoReturn().catch(() => undefined);
  // 어제부터 본다. 미국 현지 날짜가 한국보다 하루 뒤라 오늘만 보면 세션을 놓친다
  const from = new Date(now.getTime() - 36 * 3_600_000).toISOString().slice(0, 10);

  try {
    const [sessionsRs, lastRs, existingRs] = await Promise.all([
      execute(SESSIONS_AROUND, [from]),
      execute(LAST_SUCCESS),
      execute(TODAY_HEALTH_ALERTS, [from]),
    ]);

    // D1 임시 운영 중에는 미국을 돌리지 않는다(docs/infra.md 25.14). 미국 배치가 없다고 매일 알리면 거짓 알림이다
    const markets: Market[] = (await currentBackend()) === "d1" ? ["KR"] : ["KR", "US"];
    const due = dueAlerts(
      now,
      rowsToObjects<Session>(sessionsRs),
      rowsToObjects<LastSuccess>(lastRs),
      rowsToObjects<{ job: string; market: string; local_date: string; kind: string }>(existingRs),
      WATCHES,
      markets,
    );

    if (due.length > 0) {
      const stamp = now.toISOString();
      await batch(
        due.map((d) => ({
          sql: HEALTH_ALERT_INSERT,
          args: [d.job, d.market, d.localDate, d.kind, d.message, JSON.stringify(d.data), stamp, null],
        })),
      );
    }

    // 저장된 것 중 못 보낸 것을 보낸다. 방금 넣은 것과 지난번에 발송이 실패한 것이 함께 나온다
    const pending = rowsToObjects<{ id: number; message: string }>(await execute(PENDING_HEALTH_ALERTS));
    let sent = 0;
    const errors: string[] = [];
    for (const row of pending) {
      // **먼저 잡고 보낸다** (docs/infra.md 25.593, 감사 — 장중 경로 25.538 과 같게). 예전에는 보낸 뒤 `sent_at` 을 찍어, 발송은 됐는데
      // 찍기가 실패하면(일시 DB 오류·D1 쓰기 한도) 매시간 같은 "[무응답]" 이 다시 갔다. 잡기가 실패하면 보내지 않는다(다음 호출이 다시 본다)
      let 잡음 = false;
      try {
        const claim = await execute("UPDATE health_alerts SET sent_at = ? WHERE id = ? AND sent_at IS NULL", [now.toISOString(), row.id]);
        if (claim.affectedRows === 0) continue; // 다른 호출이 이미 잡았다
        잡음 = true;
        await sendTelegram(row.message);
        sent += 1;
      } catch (error) {
        errors.push(error instanceof Error ? error.message : "발송 실패");
        // 보내지 못했으면 잡은 것을 푼다 — 다음 호출이 다시 보낸다. 풀기마저 실패하면 이 알림은 빠진다(중복보다 낫다고 본다).
        // **보냈는지 몰라도 푼다** (docs/infra.md 25.618, 교차검증 — 25.613 을 되돌림). 무응답 경보는 `dueAlerts` 가 같은 날 다시
        // 만들지 않아, 풀지 않으면 그날 [무응답] 이 영영 가지 않았다. 경보는 두 번 가는 편이 안 가는 편보다 낫다
        if (잡음) {
          await execute("UPDATE health_alerts SET sent_at = NULL WHERE id = ?", [row.id]).catch(() => undefined);
        }
        break; // 한 번 막히면 나머지도 막힌다. 다음 호출이 다시 시도한다
      }
    }

    const detail = { due: due.length, sent, pending: pending.length, errors };
    await recordHeartbeat("health", "ALL", now, errors.length ? "error" : "checked", detail);
    return NextResponse.json({ checked_at: now.toISOString(), ...detail });
  } catch (error) {
    const message = error instanceof Error ? error.message : "감시에 실패했습니다";
    const quota = quotaReason(message);
    if (quota) {
      // 한도는 고장이 아니다. 500 을 쌓으면 cron-job.org 가 작업을 끌 수 있어 200 으로 답한다.
      // 호출 기록(heartbeat)도 쓰기라 남기지 않는다 (docs/infra.md 25.6). 멈췄다는 사실은 하루 한 번 알린다 (25.876)
      await notifyQuotaOnce("무응답 감시", quota);
      return NextResponse.json({ skipped: quota });
    }
    await recordHeartbeat("health", "ALL", now, "error", { error: message });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
