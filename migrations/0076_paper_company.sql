-- 모의 투자사 — 헌장·회의록·조사 자료·판 번호 (docs/company.md 1·3·5장, docs/infra.md 25.1466).
--
-- **표와 칸만** 만든다. 헌장 값은 소급 재생(scripts/company_replay.py, 25.1464)으로 문턱을 거른 뒤 **따로** 적는다 —
-- 이 마이그레이션이 적용돼도 `charter_json` 이 비어 있는 동안 모든 계좌는 판 1 그대로 돈다.

-- 헌장(철학·부서 가중·주관 부서·거부권·문턱 — services/company.CHARTERS 모양)과 판 2 를 시작한 날(그 시장 거래일)
ALTER TABLE paper_accounts ADD COLUMN charter_json TEXT;
ALTER TABLE paper_accounts ADD COLUMN charter_since TEXT;

-- 결정 원장에 판 번호 — 성과 분해·부서 성적표가 판 1 과 판 2 결정을 섞지 않게 (검증 [필수] 7). 이전 행은 비어 있다(= 판 1)
ALTER TABLE paper_decisions ADD COLUMN rule_version INTEGER;

-- 투자위원회 회의록 — (회사, 시장, 날짜) 한 장. 부서 근거는 싣지 않는다(paper_dossiers) — 입장·확신·표·쟁점·거부·결정·소수의견·운용역 메모
CREATE TABLE IF NOT EXISTS paper_meetings (
    account_id   TEXT NOT NULL REFERENCES paper_accounts (id),
    market       TEXT NOT NULL,
    decided_on   TEXT NOT NULL,            -- 회의를 연 리포트의 거래일(그 시장 현지)
    charter_hash TEXT NOT NULL,            -- 그날 헌장의 지문 (services/company.charter_hash)
    minutes_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (account_id, market, decided_on)
);

-- 조사 자료 — (시장, 신호 기준일, 종목) 한 번. 회사가 달라도 원자료는 같다(리서치의 비율만 회사마다 다르다)
CREATE TABLE IF NOT EXISTS paper_dossiers (
    market       TEXT NOT NULL,
    as_of        TEXT NOT NULL,            -- 신호 기준일 — 모든 자료를 이 날 이하로 읽었다
    stock_id     INTEGER NOT NULL REFERENCES stocks (id),
    dossier_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (market, as_of, stock_id)
);
