import { sendTelegram } from "@/lib/telegram";

/**
 * DB 한도로 크론 경로가 멈췄다는 것을 **UTC 하루에 한 번** 알린다 (docs/infra.md 25.876, 한도 날 회복 감사).
 *
 * 한도 날에는 장중 감시·뉴스·무응답 감시 경로가 모두 `200 {skipped}` 로 조용히 끝났다 — 2026-10-01 오후 국내 장중 손절·목표 알림이
 * 꺼졌다는 사실이 아무 데로도 가지 않았다. DB 에 쿨다운을 적을 수 없으므로(그것도 한도에 걸린다) **서버 메모리**로 하루 한 번만 —
 * 로그인 잠금 알림(25.846)과 같은 방식이다. 인스턴스가 여럿이면 인스턴스마다 한 번 갈 수 있다(요청마다보다 훨씬 적다).
 * 한도는 UTC 자정에 풀리므로 날짜도 UTC 다.
 */
let 보낸날: string | null = null;

export function resetQuotaNotice(): void {
  보낸날 = null;
}

export async function notifyQuotaOnce(route: string, reason: string, now: Date = new Date()): Promise<boolean> {
  const 오늘 = now.toISOString().slice(0, 10);
  if (보낸날 === 오늘) return false;
  보낸날 = 오늘;
  try {
    await sendTelegram(
      `⛔ DB 한도로 크론 경로가 멈췄습니다 (처음 걸린 곳: ${route})\n${reason}\n` +
        "그때까지 장중 감시(손절·목표·급등락 알림)·뉴스 수집·무응답 감시가 돌지 않습니다. 이 알림은 하루 한 번만 옵니다.",
    );
    return true;
  } catch {
    return false; // 알림 실패가 경로 응답을 바꾸지 않는다
  }
}
