/**
 * 금액·수량 칸의 글을 숫자로 읽는다 — 매매 폼과 설정 화면이 같이 쓴다 (docs/infra.md 25.645·25.1113).
 *
 * 따로 둔 까닭: 설정(`lib/settings.ts`)은 크론 경로도 읽는데, `lib/portfolio.ts` 를 들이면 그 경로가 매매 쓰기 문장까지
 * 끌어와 "크론 경로의 쓰기 대상" 검사(`cronWrites.test.ts`)에 걸린다.
 */
/**
 * 금액·수량 칸의 글을 숫자로 (docs/infra.md 25.642, 감사). 빈칸은 `null`(비움), 읽을 수 없으면 `NaN`.
 *
 * 예전 칸은 `type="number"` 라 브라우저가 "1,500" 을 빈 글자로 넘겼고, 수수료·세금·환율은 빈칸을 "비움" 으로 받아
 * **말없이** 설정 비율·자동 환율·세금 0 으로 저장됐다. 글자 칸으로 받아 쉼표·공백을 빼고, 못 읽으면 막는다(25.584 와 같다)
 */
export function readAmount(s: string): number | null {
  const 글 = s.trim().replace(/\s/g, "");
  if (글 === "") return null;
  // 쉼표는 **세 자리 구분일 때만** 뺀다 — "1,5"·유럽식 "1.380,5" 를 15·1.3805 로 말없이 읽지 않게.
  // `Number` 가 받는 "1e3"·"0x10" 도 금액 칸에서는 받지 않는다 (25.645, 교차검증)
  if (글.includes(",") && !/^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(글)) return NaN;
  const 숫자 = 글.replace(/,/g, "");
  return /^-?(\d+(\.\d*)?|\.\d+)$/.test(숫자) ? Number(숫자) : NaN;
}
