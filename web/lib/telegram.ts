/**
 * 텔레그램 발송 (웹 쪽). 파이썬 배치는 `batch/notify/telegram.py` 를 쓴다.
 *
 * **길이 제한을 여기서 지킨다** (2026-09-21, docs/infra.md 25.58).
 * 텔레그램 `sendMessage` 는 4,096자를 넘으면 `ok:false, "message is too long"` 으로 거절한다.
 * 장중 알림은 밀린 것을 **한 통으로 묶어** 보내는데(`bundleMessage`, 최대 50건), 긴 종목명과
 * 가격이 섞인 날이면 그 한 통이 4,096자를 넘는다. 그런데 이 경로는 보낸 뒤에야 `sent_at` 을
 * 찍으므로, 한 번 거절당하면 **같은 50건이 영원히 대기열에 남아** 5분마다 같은 실패를
 * 되풀이한다. 스스로 낫지 않는다 — 장중 알림 통로가 통째로 막히고, 막혔다는 사실조차
 * 텔레그램으로 알릴 수 없다(막힌 것이 텔레그램이다). 그래서 파이썬과 같은 방식으로 나눠 보낸다.
 *
 * **429 를 여기서 기다리지 않는다.** 파이썬 쪽은 `retry_after` 만큼 잔다(그쪽은 Actions 라
 * 실행이 한 번뿐이다). 여기는 Vercel 서버리스라 자는 동안 실행 시간을 태우고, 무엇보다
 * **5분 뒤 크론이 다시 부른다** — 보내지 못한 알림은 `sent_at` 이 비어 있어 그때 다시 나간다.
 * 기다리지 않는 것이 여기서는 맞다.
 */

import { withDisclaimer } from "@/lib/notice";

/** 텔레그램 텍스트 메시지 길이 상한으로 알려진 값 [확인필요]. batch/notify/telegram.py 의 MAX_LEN 과 같다 */
export const MAX_LEN = 4096;
/** 실제로 자르는 길이. 상한을 공식 문서로 검증하지 않았으므로 여유를 둔다 */
export const SAFE_LEN = 3900;

/**
 * 길이 제한에 맞춰 나눈다. 줄 단위를 지켜 자른다.
 * `batch/notify/telegram.py` 의 `split_message` 와 **같은 결과를 내야 한다** —
 * `tests/fixtures/telegram_split.json` 을 양쪽 테스트가 함께 읽어 확인한다.
 *
 * 파이썬은 코드포인트를, 자바스크립트는 UTF-16 단위를 센다. 한글·한자·기호는 둘 다 1 이라
 * 같지만 이모지 같은 서러게이트 쌍은 자바스크립트가 2 로 센다 — 그쪽이 더 짧게 자르는 쪽이고,
 * 196자 여유(4096-3900)가 그 차이를 덮는다.
 */
function joinedLen(lines: string[]): number {
  return lines.reduce((n, x) => n + x.length, 0) + Math.max(0, lines.length - 1);
}

export function splitMessage(text: string, limit: number = SAFE_LEN): string[] {
  if (text.length <= limit) return [text];

  const chunks: string[] = [];
  let current: string[] = [];
  let currentLen = 0;

  for (let line of text.split("\n")) {
    // 한 줄이 통째로 한도를 넘으면 강제로 쪼갠다
    while (line.length > limit) {
      if (current.length) {
        chunks.push(current.join("\n"));
        current = [];
        currentLen = 0;
      }
      // **서러게이트 쌍을 가르지 않는다** (docs/infra.md 25.256). UTF-16 단위로 자르면 이모지가 반으로 갈려 깨진 글자가 된다
      const cut = /[\uD800-\uDBFF]/.test(line[limit - 1] ?? "") ? limit - 1 : limit;
      chunks.push(line.slice(0, cut));
      line = line.slice(cut);
    }

    const add = line.length + (current.length ? 1 : 0);
    if (currentLen + add > limit) {
      // **들여 쓴 줄은 머리 줄과 함께 간다** (docs/infra.md 25.819) — 파이썬 `split_message` 와 같은 규칙
      let cut = current.length;
      if (line[0] === " " || line[0] === "\t") {
        let j = current.length - 1;
        while (j > 0 && (current[j][0] === " " || current[j][0] === "\t")) j -= 1;
        if (j > 0 && joinedLen(current.slice(j)) + 1 + line.length <= limit) cut = j;
      }
      chunks.push(current.slice(0, cut).join("\n"));
      current = [...current.slice(cut), line];
      currentLen = joinedLen(current);
    } else {
      current.push(line);
      currentLen += add;
    }
  }

  if (current.length) chunks.push(current.join("\n"));
  // 빈 조각은 보내지 않는다 (docs/infra.md 25.256, 파이썬 split_message 와 같은 규칙)
  return chunks.filter((c) => c.trim().length > 0);
}

/**
 * **보냈는지 모른다** — 응답 헤더를 받기 전에 제한 시간이 지났다 (docs/infra.md 25.613).
 * 10초 제한은 DNS·TCP·TLS 까지 덮어 요청이 나갔는지 가르지 못한다. 장중·무응답 경로는 이것도 **풀어 다시 보낸다** —
 * 알림은 빠지는 편이 두 번 가는 편보다 나쁘다 (25.618). 이름을 따로 두는 것은 기록(heartbeat 오류)에서 까닭을 가리려는 것이다.
 */
export class TelegramUncertainError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TelegramUncertainError";
  }
}

/**
 * 한 통 보낸다. 길면 나눠서 순서대로 보낸다.
 *
 * 중간에서 실패하면 앞부분은 이미 나갔는데 부르는 쪽은 실패로 본다 — 다음 호출이 처음부터
 * 다시 보내므로 앞부분이 겹친다. **겹치는 편이 안 보내는 편보다 낫다.** (파이썬은 25.414 부터 보낸 조각을 들고
 * 올려 겹치지 않게 한다 — 여기는 장중 묶음이 한 통에 맞춰져(`alertGroups`) 나뉘는 일이 드물어 두었다.)
 * 응답 시간 초과는 `TelegramUncertainError` 로 던진다.
 */
export async function sendTelegram(text: string, fetchImpl: typeof fetch = fetch): Promise<void> {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  // 테스트 발송(`api/telegram/test`)과 같게 앞뒤 공백을 뺀다 — 값 끝 줄바꿈이면 테스트만 성공할 수 있었다 (25.587)
  const chatId = process.env.TELEGRAM_CHAT_ID?.trim();
  if (!token || !chatId) throw new Error("TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 가 비어 있습니다");
  // **여기서 한 번 붙인다** (docs/infra.md 25.115). 부르는 쪽마다 적게 하면 언젠가 빠뜨린다 —
  // 무응답 알림이 실제로 빠뜨리고 있었다. 나누기 **전에** 붙여야 마지막 통에 들어간다
  for (const chunk of splitMessage(withDisclaimer(text))) {
    let response: Response;
    try {
      response = await fetchImpl(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text: chunk, disable_web_page_preview: true }),
        cache: "no-store",
        // 매달리면 부른 경로(장중 30초)가 통째로 죽는다 — 제한 시간을 둔다 (docs/infra.md 25.595)
        signal: AbortSignal.timeout(10_000),
      });
    } catch (e) {
      const 이름 = e instanceof Error ? e.name : "";
      if (이름 === "TimeoutError" || 이름 === "AbortError") {
        throw new TelegramUncertainError("텔레그램 응답 시간 초과 — 보냈는지 알 수 없습니다");
      }
      throw e;
    }
    // 헤더를 받은 뒤 **본문을 읽다가** 시간이 다 되면 텔레그램은 이미 받았다 (25.618, 교차검증). 예전에는 `{}` 로 삼켜
    // "HTTP 200" 보통 실패가 되고, 장중 경로가 묶음을 풀어 같은 알림이 두 번 갔다
    // 헤더가 200 이면 텔레그램은 받았다 — 본문을 읽다 **무엇으로든** 실패하면(시간 초과·연결 끊김 `terminated`) 보낸 것으로 본다
    // (25.618·25.619, 교차검증: 시간 초과만 봐서 헤더 뒤 끊김은 "HTTP 200" 실패가 되어 두 번 갔다)
    const body = (await response.json().catch(() => (response.ok ? { ok: true } : {}))) as {
      ok?: boolean;
      description?: string;
    };
    // description 에 토큰이 들어가지 않는다. 그대로 올려도 안전하다
    if (!body.ok) throw new Error(body.description ?? `텔레그램 HTTP ${response.status}`);
  }
}
