-- 보험 Agent 예시용 스키마 + 시드 데이터
-- 주계약(main_products) + 특약(riders) 구조의 가입 설계를 표현한다.
-- docker-entrypoint-initdb.d 에 의해 빈 볼륨 최초 기동 시 1회 실행된다.

-- 증권번호 시퀀스
CREATE SEQUENCE IF NOT EXISTS policy_no_seq START 1001;

-- ---------- 고객 등록 ----------
CREATE TABLE customers (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL,
    birth_date  DATE         NOT NULL,
    gender      VARCHAR(10)  NOT NULL CHECK (gender IN ('남', '여')),
    phone       VARCHAR(20),
    email       VARCHAR(100),
    job         VARCHAR(50),
    job_class   INT          NOT NULL DEFAULT 1 CHECK (job_class BETWEEN 1 AND 3), -- 직업급수(1:사무 ~ 3:고위험)
    address     VARCHAR(200),
    created_at  TIMESTAMP    DEFAULT now()
);

-- ---------- 주계약 상품 ----------
CREATE TABLE main_products (
    id            SERIAL PRIMARY KEY,
    code          VARCHAR(30)  UNIQUE NOT NULL,
    name          VARCHAR(100) NOT NULL,
    category      VARCHAR(30)  NOT NULL,         -- 생명/건강/암/어린이/상해 등
    description   TEXT,
    join_min_age  INT NOT NULL DEFAULT 0,        -- 가입 가능 최소 연령
    join_max_age  INT NOT NULL DEFAULT 100,      -- 가입 가능 최대 연령
    gender        VARCHAR(10),                   -- NULL이면 남녀 공용
    renewal_type  VARCHAR(20) NOT NULL DEFAULT '비갱신형',
    max_job_class INT NOT NULL DEFAULT 3         -- 가입 가능한 최대 직업급수
);

-- ---------- 주계약 보기/납기/보험료 플랜 ----------
CREATE TABLE main_product_plans (
    id               SERIAL PRIMARY KEY,
    main_product_id  INT NOT NULL REFERENCES main_products(id),
    coverage_period  VARCHAR(20) NOT NULL,       -- 보험기간(보기): 종신/80세만기/20년만기 등
    payment_period   VARCHAR(20) NOT NULL,       -- 납입기간(납기): 10년납/20년납/전기납 등
    coverage_amount  NUMERIC(15,0) NOT NULL,     -- 가입금액(주계약 보장금액)
    monthly_premium  NUMERIC(12,0) NOT NULL      -- 월 보험료
);

-- ---------- 특약 상품 ----------
CREATE TABLE riders (
    id               SERIAL PRIMARY KEY,
    code             VARCHAR(30) UNIQUE NOT NULL,
    name             VARCHAR(100) NOT NULL,
    description      TEXT,
    coverage_amount  NUMERIC(15,0) NOT NULL,
    monthly_premium  NUMERIC(12,0) NOT NULL,
    join_min_age     INT NOT NULL DEFAULT 0,
    join_max_age     INT NOT NULL DEFAULT 100
);

-- ---------- 주계약-특약 종속 매핑 (어떤 특약이 어떤 주계약에 부가 가능한지) ----------
CREATE TABLE main_product_riders (
    main_product_id  INT NOT NULL REFERENCES main_products(id),
    rider_id         INT NOT NULL REFERENCES riders(id),
    is_mandatory     BOOLEAN NOT NULL DEFAULT FALSE,  -- 필수특약 여부
    PRIMARY KEY (main_product_id, rider_id)
);

-- ---------- 계약(증권): 주계약 가입 ----------
CREATE TABLE policies (
    id               SERIAL PRIMARY KEY,
    policy_no        VARCHAR(20) UNIQUE NOT NULL
                     DEFAULT ('POL' || lpad(nextval('policy_no_seq')::text, 8, '0')),
    customer_id      INT NOT NULL REFERENCES customers(id),
    main_product_id  INT NOT NULL REFERENCES main_products(id),
    plan_id          INT NOT NULL REFERENCES main_product_plans(id),
    status           VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE/CANCELLED
    main_premium     NUMERIC(12,0) NOT NULL,                 -- 주계약 월 보험료
    total_premium    NUMERIC(12,0) NOT NULL,                 -- 주계약+특약 합계 월 보험료
    start_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at       TIMESTAMP DEFAULT now()
);

-- ---------- 계약에 부가된 특약 ----------
CREATE TABLE policy_riders (
    id               SERIAL PRIMARY KEY,
    policy_id        INT NOT NULL REFERENCES policies(id),
    rider_id         INT NOT NULL REFERENCES riders(id),
    monthly_premium  NUMERIC(12,0) NOT NULL
);

-- ====================================================================
-- 시드 데이터
-- ====================================================================

-- 고객
INSERT INTO customers (name, birth_date, gender, phone, email, job, job_class, address) VALUES
    ('김민수', '1985-03-12', '남', '010-1111-2222', 'minsu@example.com',    '회사원',   1, '서울 강남구'),
    ('이영희', '1992-07-25', '여', '010-3333-4444', 'younghee@example.com', '디자이너', 1, '서울 마포구'),
    ('박철수', '1970-11-03', '남', '010-5555-6666', 'chulsoo@example.com',  '자영업',   2, '경기 성남시'),
    ('최지은', '2015-05-20', '여', '010-7777-8888', NULL,                   '학생',     1, '서울 송파구'),
    ('정대현', '1955-01-15', '남', '010-9999-0000', 'daehyun@example.com',  '은퇴',     1, '부산 해운대구'),
    ('한지민', '1988-09-08', '여', '010-2222-3333', 'jimin@example.com',    '간호사',   1, '인천 연수구');

-- 주계약 상품
INSERT INTO main_products (code, name, category, description, join_min_age, join_max_age, gender, renewal_type, max_job_class) VALUES
    ('MAIN_LIFE',     '무배당 행복종신보험', '생명',   '사망 시 보험금을 지급하는 종신 보장 주계약',        15, 70, NULL, '비갱신형', 3),
    ('MAIN_HEALTH',   '건강한백세 건강보험', '건강',   '질병·상해 입원/수술을 보장하는 갱신형 건강보험',    20, 65, NULL, '갱신형',   3),
    ('MAIN_CANCER',   '암보장 암보험',       '암',     '암 진단·치료를 집중 보장하는 주계약',               20, 60, NULL, '비갱신형', 2),
    ('MAIN_KID',      '자녀사랑 어린이보험', '어린이', '어린이 질병·상해를 보장하는 주계약',                 0, 15, NULL, '비갱신형', 3),
    ('MAIN_ACCIDENT', '안심상해보험',        '상해',   '상해 사고를 보장하는 갱신형 상해보험',              15, 70, NULL, '갱신형',   3);

-- 주계약 보기/납기/보험료 플랜
INSERT INTO main_product_plans (main_product_id, coverage_period, payment_period, coverage_amount, monthly_premium)
SELECT mp.id, v.cov, v.pay, v.amt, v.prem
FROM (VALUES
    ('MAIN_LIFE',     '종신',     '20년납', 100000000, 120000),
    ('MAIN_LIFE',     '종신',     '30년납', 100000000,  95000),
    ('MAIN_LIFE',     '종신',     '20년납',  50000000,  65000),
    ('MAIN_HEALTH',   '100세만기','20년납',  30000000,  45000),
    ('MAIN_HEALTH',   '100세만기','전기납',  30000000,  38000),
    ('MAIN_CANCER',   '80세만기', '20년납',  50000000,  52000),
    ('MAIN_CANCER',   '90세만기', '20년납',  50000000,  61000),
    ('MAIN_KID',      '30세만기', '20년납',  30000000,  42000),
    ('MAIN_KID',      '30세만기', '15년납',  30000000,  49000),
    ('MAIN_ACCIDENT', '20년만기', '20년납', 100000000,  30000)
) AS v(code, cov, pay, amt, prem)
JOIN main_products mp ON mp.code = v.code;

-- 특약 상품
INSERT INTO riders (code, name, description, coverage_amount, monthly_premium, join_min_age, join_max_age) VALUES
    ('R_DEATH',       '일반사망특약',       '질병·상해로 인한 사망 보장',      10000000,  8000, 15, 70),
    ('R_CANCER_DIAG', '암진단특약',         '암 진단 확정 시 보험금 지급',     30000000, 15000,  0, 65),
    ('R_HOSPITAL',    '입원일당특약',       '입원 1일당 정액 보장',               30000,  6000,  0, 70),
    ('R_SURGERY',     '수술특약',           '수술 시 보험금 지급',              5000000,  5000,  0, 70),
    ('R_DISEASE',     '질병사망특약',       '질병으로 인한 사망 보장',         20000000,  9000, 15, 65),
    ('R_INJURY',      '상해후유장해특약',   '상해 후유장해 보장',             100000000,  4000,  0, 70),
    ('R_CHILD',       '어린이특정질병특약', '어린이 특정질병 보장',            20000000,  7000,  0, 15);

-- 주계약-특약 종속 매핑 (is_mandatory=TRUE 는 필수특약)
INSERT INTO main_product_riders (main_product_id, rider_id, is_mandatory)
SELECT mp.id, r.id, v.mand
FROM (VALUES
    ('MAIN_LIFE',     'R_DEATH',       FALSE),
    ('MAIN_LIFE',     'R_DISEASE',     FALSE),
    ('MAIN_LIFE',     'R_HOSPITAL',    FALSE),
    ('MAIN_LIFE',     'R_SURGERY',     FALSE),
    ('MAIN_HEALTH',   'R_HOSPITAL',    TRUE),
    ('MAIN_HEALTH',   'R_SURGERY',     FALSE),
    ('MAIN_HEALTH',   'R_CANCER_DIAG', FALSE),
    ('MAIN_CANCER',   'R_CANCER_DIAG', TRUE),
    ('MAIN_CANCER',   'R_HOSPITAL',    FALSE),
    ('MAIN_CANCER',   'R_SURGERY',     FALSE),
    ('MAIN_KID',      'R_CHILD',       TRUE),
    ('MAIN_KID',      'R_HOSPITAL',    FALSE),
    ('MAIN_KID',      'R_SURGERY',     FALSE),
    ('MAIN_KID',      'R_INJURY',      FALSE),
    ('MAIN_ACCIDENT', 'R_INJURY',      TRUE),
    ('MAIN_ACCIDENT', 'R_HOSPITAL',    FALSE),
    ('MAIN_ACCIDENT', 'R_SURGERY',     FALSE)
) AS v(pcode, rcode, mand)
JOIN main_products mp ON mp.code = v.pcode
JOIN riders r        ON r.code  = v.rcode;

-- 기존 가입 계약 예시: 박철수 → 건강보험(100세만기/20년납) + 입원일당특약
WITH np AS (
    INSERT INTO policies (customer_id, main_product_id, plan_id, main_premium, total_premium)
    SELECT c.id, mp.id, pl.id, 45000, 51000
    FROM customers c, main_products mp, main_product_plans pl
    WHERE c.name = '박철수'
      AND mp.code = 'MAIN_HEALTH'
      AND pl.main_product_id = mp.id
      AND pl.coverage_period = '100세만기'
      AND pl.payment_period = '20년납'
    RETURNING id
)
INSERT INTO policy_riders (policy_id, rider_id, monthly_premium)
SELECT np.id, r.id, r.monthly_premium
FROM np, riders r
WHERE r.code = 'R_HOSPITAL';

-- ---------- 대화 히스토리 (영속 세션) ----------
-- worker 가 session_id 별로 LangChain 메시지를 직렬화해 저장/복원한다.
-- messages_to_dict() 결과(JSON)를 message 컬럼에 한 메시지당 한 행으로 보관.
CREATE TABLE conversations (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    seq         INT         NOT NULL,         -- 세션 내 메시지 순서
    message     JSONB       NOT NULL,         -- messages_to_dict() 단일 항목
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);
CREATE INDEX idx_conversations_session ON conversations (session_id, seq);
