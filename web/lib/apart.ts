/**
 * 문턱과 구별되는 소수 자릿수 — `base` 자리로 반올림하면 문턱과 같아 보이는 값(49.96 → "50.0" / 50 초과)이면 자리를 늘린다.
 * 배치 `services/broker_mirror._문턱값` 과 같은 뜻(25.1637, 웹 화면 감사 [필수] 1·2 — 25.1631 은 텔레그램만 고쳤다)
 */
export function apartDigits(
  v: number,
  t: number | null | undefined,
  base: number,
): number {
  if (t == null || !Number.isFinite(t) || v === t) return base;
  for (let d = base; d <= 6; d++)
    if (Number(v.toFixed(d)) !== Number(t.toFixed(d))) return d;
  return 6;
}
