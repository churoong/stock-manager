/**
 * 장중 경로만 부르는 알림 쓰기 문장 — 다른 크론 경로가 `lib/intraday.ts` 를 가져다 써도 alerts 쓰기가 섞이지 않게 따로 둔다
 * (`cronWrites.test.ts` 가 경로별 쓰기 대상을 본다).
 */
/**
 * 알림 저장. (종목, 트리거, 날짜) 유니크로 **하루 한 번**이다.
 *
 * **조용시간에 "보내지 않음"(`quiet-skipped`)으로 둔 행은 조용시간 밖의 같은 판정이 되살린다** (docs/infra.md 25.797, 알림 감사 #1).
 * 예전에는 `DO NOTHING` 이라, 조용시간 22:00~01:00·"해제 뒤 보내기" 끔에서 22:35 손절 터치가 `quiet-skipped` 로 저장되면 01:05 호출이 여전히 손절가
 * 아래를 봐도 그 장 동안 한 통도 나가지 않았다. 사용자가 끈 것은 "자는 동안 오는 알림" 이지 깨어 있는 시간의 손절 알림이 아니다.
 * 이미 보냈거나 보낼 차례인 행(NULL·시각)은 그대로 둔다. 되살린 행은 `affectedRows` 에 세어져 이번 호출이 보낸다.
 * 인자: 종목, 시장, 날짜, 트리거, 문구, data, 만든 시각, sent_at(조용시간이면 'quiet-skipped', 아니면 NULL)
 */
export const ALERT_UPSERT = `INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at, sent_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (stock_id, trigger_type, trade_date) DO UPDATE SET
  sent_at = NULL, message = excluded.message, data = excluded.data, created_at = excluded.created_at, is_read = 0
WHERE alerts.sent_at = 'quiet-skipped' AND excluded.sent_at IS NULL`;
