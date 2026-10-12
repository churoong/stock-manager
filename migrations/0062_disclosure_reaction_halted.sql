-- 공시 반응 — 5거래일째에 거래정지·상장폐지라 못 잰 건수 (docs/disclosure_reaction.md 3장, docs/infra.md 25.1154).
-- 창을 시장 거래일로 세면서, 그날 종가가 없는 사건을 빼되 몇 건인지 알림에 함께 적는다
ALTER TABLE disclosure_reaction ADD COLUMN halted INTEGER NOT NULL DEFAULT 0;
