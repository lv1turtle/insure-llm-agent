"""LangChain Tools — 에이전트가 DB와 상호작용하는 함수들.

주계약(main_products) + 특약(riders) 구조의 가입 설계를 지원한다.
각 tool 은 LLM 이 호출하므로 docstring/인자 설명을 명확히 작성한다.
반환값은 LLM 이 읽기 쉽도록 한국어 키의 JSON 문자열로 통일한다.
"""

import json
from datetime import date
from typing import Optional

from langchain_core.tools import tool
from sqlalchemy import text

import database as db


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _calc_age(birth_date: date) -> int:
    today = date.today()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def _get_customer(customer_id: int) -> Optional[dict]:
    row = db.fetch_one("SELECT * FROM customers WHERE id = :id", id=customer_id)
    if row:
        row["age"] = _calc_age(row["birth_date"])
    return row


# ----------------------------------------------------------------------
# 고객
# ----------------------------------------------------------------------
@tool
def list_customers(limit: int = 20) -> str:
    """등록된 고객 목록을 조회한다. 설계사가 '내 고객들', '고객 목록',
    '전체 고객'처럼 특정 이름 없이 고객을 보고 싶어 할 때 사용한다.
    limit 은 1~100 사이로 제한한다."""
    limit = max(1, min(limit, 100))
    rows = db.fetch_all(
        "SELECT id, name, birth_date, gender, phone, job, job_class "
        "FROM customers ORDER BY id LIMIT :limit",
        limit=limit,
    )
    for r in rows:
        r["age"] = _calc_age(r["birth_date"])
    return _json(rows) if rows else "등록된 고객이 없습니다. register_customer 로 신규 등록할 수 있습니다."


@tool
def find_customers(name: str) -> str:
    """고객 이름(또는 일부)으로 고객을 검색한다. 동명이인이 있을 수 있으므로
    여러 명이 반환되면 생년월일/전화번호로 본인을 특정해야 한다."""
    rows = db.fetch_all(
        "SELECT id, name, birth_date, gender, phone, job, job_class "
        "FROM customers WHERE name LIKE :pattern ORDER BY id",
        pattern=f"%{name}%",
    )
    for r in rows:
        r["age"] = _calc_age(r["birth_date"])
    return _json(rows) if rows else "검색된 고객이 없습니다. register_customer 로 신규 등록할 수 있습니다."


@tool
def get_customer(customer_id: int) -> str:
    """고객 ID로 고객 상세 정보를 조회한다."""
    row = _get_customer(customer_id)
    if not row:
        return f"ID {customer_id} 고객을 찾을 수 없습니다."
    return _json(row)


@tool
def register_customer(
    name: str,
    birth_date: str,
    gender: str,
    phone: str = "",
    job: str = "",
    job_class: int = 1,
    email: str = "",
    address: str = "",
) -> str:
    """신규 고객을 등록한다. 기존 고객이 없을 때 사용한다.
    birth_date 는 'YYYY-MM-DD' 형식, gender 는 '남' 또는 '여',
    job_class(직업급수)는 1(사무)~3(고위험) 사이여야 한다.
    값이 형식에 맞지 않으면 오류 메시지를 반환하므로, 설계사에게 올바른 값을 다시 받아 호출한다."""
    try:
        bd = date.fromisoformat(birth_date)
    except ValueError:
        return _json({"success": False, "error": "birth_date 형식이 잘못되었습니다. 'YYYY-MM-DD' 로 입력하세요."})
    if gender not in ("남", "여"):
        return _json({"success": False, "error": "gender 는 '남' 또는 '여' 여야 합니다."})
    if job_class not in (1, 2, 3):
        return _json({"success": False, "error": "job_class(직업급수)는 1~3 사이여야 합니다."})

    row = db.execute(
        "INSERT INTO customers (name, birth_date, gender, phone, email, job, job_class, address) "
        "VALUES (:name, :bd, :gender, :phone, :email, :job, :jc, :addr) "
        "RETURNING id, name, birth_date, gender, phone, job, job_class",
        name=name, bd=bd, gender=gender, phone=phone or None, email=email or None,
        job=job or None, jc=job_class, addr=address or None,
    )
    row["age"] = _calc_age(row["birth_date"])
    return _json({"success": True, "message": f"{name} 고객을 등록했습니다.", "customer": row})


# ----------------------------------------------------------------------
# 상품 조회
# ----------------------------------------------------------------------
@tool
def list_main_products(category: str = "") -> str:
    """판매 중인 주계약 상품 목록을 조회한다. category(생명/건강/암/어린이/상해)를
    지정하면 해당 분류만, 비워두면 전체를 조회한다. 각 상품의 보험료 범위를 함께 보여준다."""
    base = (
        "SELECT mp.id, mp.code, mp.name, mp.category, mp.join_min_age, mp.join_max_age, "
        "mp.gender, mp.renewal_type, mp.max_job_class, "
        "MIN(pl.monthly_premium) AS min_premium, MAX(pl.monthly_premium) AS max_premium "
        "FROM main_products mp LEFT JOIN main_product_plans pl ON pl.main_product_id = mp.id "
    )
    if category:
        rows = db.fetch_all(
            base + "WHERE mp.category = :category GROUP BY mp.id ORDER BY mp.id",
            category=category,
        )
    else:
        rows = db.fetch_all(base + "GROUP BY mp.id ORDER BY mp.id")
    return _json(rows) if rows else "해당 조건의 주계약 상품이 없습니다."


@tool
def get_product_detail(main_product_id: int) -> str:
    """주계약 상품 1개의 상세 정보를 조회한다.
    선택 가능한 보기/납기 플랜 목록(plan_id 포함)과 부가 가능한 특약 목록
    (rider_id, 필수특약 여부 포함)을 함께 반환한다. 가입 설계 시 이 정보로 구성한다."""
    product = db.fetch_one("SELECT * FROM main_products WHERE id = :id", id=main_product_id)
    if not product:
        return f"ID {main_product_id} 주계약 상품을 찾을 수 없습니다."

    plans = db.fetch_all(
        "SELECT id AS plan_id, coverage_period, payment_period, coverage_amount, monthly_premium "
        "FROM main_product_plans WHERE main_product_id = :id ORDER BY id",
        id=main_product_id,
    )
    riders = db.fetch_all(
        "SELECT r.id AS rider_id, r.code, r.name, r.coverage_amount, r.monthly_premium, "
        "r.join_min_age, r.join_max_age, mpr.is_mandatory "
        "FROM main_product_riders mpr JOIN riders r ON r.id = mpr.rider_id "
        "WHERE mpr.main_product_id = :id ORDER BY mpr.is_mandatory DESC, r.id",
        id=main_product_id,
    )
    return _json({"product": product, "plans": plans, "riders": riders})


@tool
def list_eligible_products(customer_id: int) -> str:
    """특정 고객이 가입 가능한 주계약 상품 목록을 조회한다.
    고객의 나이/성별/직업급수를 상품의 가입 조건과 대조해 가능한 상품만 반환한다."""
    customer = _get_customer(customer_id)
    if not customer:
        return f"ID {customer_id} 고객을 찾을 수 없습니다."

    rows = db.fetch_all(
        "SELECT mp.id, mp.code, mp.name, mp.category, mp.join_min_age, mp.join_max_age, "
        "mp.renewal_type, MIN(pl.monthly_premium) AS min_premium "
        "FROM main_products mp LEFT JOIN main_product_plans pl ON pl.main_product_id = mp.id "
        "WHERE :age BETWEEN mp.join_min_age AND mp.join_max_age "
        "  AND (mp.gender IS NULL OR mp.gender = :gender) "
        "  AND mp.max_job_class >= :jc "
        "GROUP BY mp.id ORDER BY mp.id",
        age=customer["age"], gender=customer["gender"], jc=customer["job_class"],
    )
    return _json(
        {
            "customer": {"id": customer["id"], "name": customer["name"], "age": customer["age"]},
            "eligible_products": rows,
        }
    ) if rows else _json({"customer": customer["name"], "eligible_products": [], "message": "가입 가능한 상품이 없습니다."})


# ----------------------------------------------------------------------
# 가입 설계 검증 (check / enroll 공통 로직)
# ----------------------------------------------------------------------
def _validate_enrollment(
    customer_id: int, main_product_id: int, plan_id: int, rider_ids: Optional[list[int]]
) -> dict:
    """가입 설계의 유효성을 검증하고, 부족/불일치 항목과 보험료 내역을 구조화해 반환한다."""
    rider_ids = list(dict.fromkeys(rider_ids or []))  # 중복 제거, 순서 유지
    issues: list[str] = []

    customer = _get_customer(customer_id)
    if not customer:
        return {"ok": False, "issues": [f"고객 ID {customer_id} 을(를) 찾을 수 없습니다."]}

    product = db.fetch_one("SELECT * FROM main_products WHERE id = :id", id=main_product_id)
    if not product:
        return {"ok": False, "issues": [f"주계약 ID {main_product_id} 을(를) 찾을 수 없습니다."]}

    age = customer["age"]
    # 주계약 자격
    if not (product["join_min_age"] <= age <= product["join_max_age"]):
        issues.append(
            f"가입 연령 미달/초과: 가능 연령 {product['join_min_age']}~{product['join_max_age']}세, 고객 {age}세."
        )
    if product["gender"] and product["gender"] != customer["gender"]:
        issues.append(f"성별 조건 불일치: {product['gender']} 전용 상품입니다.")
    if customer["job_class"] > product["max_job_class"]:
        issues.append(
            f"직업급수 초과: 가입 가능 최대 {product['max_job_class']}급, 고객 {customer['job_class']}급."
        )

    # 플랜(보기/납기) 검증
    plan = db.fetch_one(
        "SELECT * FROM main_product_plans WHERE id = :pid AND main_product_id = :mid",
        pid=plan_id, mid=main_product_id,
    )
    if not plan:
        issues.append(f"플랜 ID {plan_id} 은(는) 이 주계약의 보기/납기 플랜이 아닙니다.")

    # 특약 검증 (종속성 + 연령)
    allowed = {
        r["rider_id"]: r
        for r in db.fetch_all(
            "SELECT r.id AS rider_id, r.name, r.monthly_premium, r.join_min_age, r.join_max_age, "
            "mpr.is_mandatory "
            "FROM main_product_riders mpr JOIN riders r ON r.id = mpr.rider_id "
            "WHERE mpr.main_product_id = :id",
            id=main_product_id,
        )
    }
    valid_riders: list[dict] = []
    for rid in rider_ids:
        info = allowed.get(rid)
        if not info:
            issues.append(f"특약 ID {rid} 은(는) 이 주계약에 부가할 수 없는 특약입니다.")
            continue
        if not (info["join_min_age"] <= age <= info["join_max_age"]):
            issues.append(
                f"특약 '{info['name']}' 가입 연령 미달/초과: 가능 {info['join_min_age']}~{info['join_max_age']}세."
            )
            continue
        valid_riders.append(info)

    # 필수특약 누락 검사
    missing_mandatory = [
        {"rider_id": rid, "name": info["name"]}
        for rid, info in allowed.items()
        if info["is_mandatory"] and rid not in rider_ids
    ]
    if missing_mandatory:
        names = ", ".join(m["name"] for m in missing_mandatory)
        issues.append(f"필수특약 누락: {names} (rider_id {[m['rider_id'] for m in missing_mandatory]})")

    main_premium = int(plan["monthly_premium"]) if plan else 0
    rider_premium = sum(int(r["monthly_premium"]) for r in valid_riders)

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "customer": {"id": customer["id"], "name": customer["name"], "age": age},
        "product": {"id": product["id"], "name": product["name"]},
        "plan": (
            {
                "plan_id": plan["id"],
                "coverage_period": plan["coverage_period"],
                "payment_period": plan["payment_period"],
                "coverage_amount": int(plan["coverage_amount"]),
            }
            if plan else None
        ),
        "riders": [{"rider_id": r["rider_id"], "name": r["name"], "monthly_premium": int(r["monthly_premium"])} for r in valid_riders],
        "missing_mandatory": missing_mandatory,
        "premium": {"main": main_premium, "riders": rider_premium, "total": main_premium + rider_premium},
    }


@tool
def check_enrollment(
    customer_id: int,
    main_product_id: int,
    plan_id: int,
    rider_ids: Optional[list[int]] = None,
) -> str:
    """가입 설계(고객 + 주계약 + 플랜 + 특약)의 가입 가능 여부를 검증한다.
    자격·플랜·특약 종속성·필수특약 누락을 검사하고 월 보험료 합계를 계산한다.
    enroll_policy 호출 전에 반드시 먼저 호출한다. issues 가 있으면 그 내용을
    설계사에게 알리고 보완(정보 추가 입력/특약 조정)한 뒤 다시 검증한다."""
    return _json(_validate_enrollment(customer_id, main_product_id, plan_id, rider_ids))


@tool
def enroll_policy(
    customer_id: int,
    main_product_id: int,
    plan_id: int,
    rider_ids: Optional[list[int]] = None,
) -> str:
    """고객을 주계약 + 특약 구성으로 실제 가입(계약/증권 생성)시킨다.
    되돌리기 어려운 쓰기 작업이므로, check_enrollment 로 자격을 확인하고
    설계사의 최종 동의를 받은 뒤에만 호출한다. 내부에서 자격을 재검증하며,
    issues 가 있으면 가입하지 않고 그 내역을 반환한다."""
    result = _validate_enrollment(customer_id, main_product_id, plan_id, rider_ids)
    if not result["ok"]:
        return _json({"success": False, "issues": result["issues"], "design": result})

    with db.engine.begin() as conn:
        policy = conn.execute(
            text(
                "INSERT INTO policies (customer_id, main_product_id, plan_id, main_premium, total_premium) "
                "VALUES (:cid, :mid, :pid, :main, :total) "
                "RETURNING id, policy_no, start_date"
            ),
            {
                "cid": customer_id, "mid": main_product_id, "pid": plan_id,
                "main": result["premium"]["main"], "total": result["premium"]["total"],
            },
        ).mappings().first()

        for r in result["riders"]:
            conn.execute(
                text(
                    "INSERT INTO policy_riders (policy_id, rider_id, monthly_premium) "
                    "VALUES (:pid, :rid, :prem)"
                ),
                {"pid": policy["id"], "rid": r["rider_id"], "prem": r["monthly_premium"]},
            )

    return _json(
        {
            "success": True,
            "message": f"{result['customer']['name']} 고객을 '{result['product']['name']}'에 가입 완료했습니다.",
            "policy_no": policy["policy_no"],
            "start_date": policy["start_date"],
            "design": {
                "product": result["product"]["name"],
                "plan": result["plan"],
                "riders": result["riders"],
                "premium": result["premium"],
            },
        }
    )


# ----------------------------------------------------------------------
# 가입 현황 조회
# ----------------------------------------------------------------------
@tool
def get_customer_policies(customer_id: int) -> str:
    """특정 고객의 가입 계약 현황을 조회한다.
    각 계약의 주계약/플랜/부가 특약/월 보험료를 함께 반환한다."""
    customer = _get_customer(customer_id)
    if not customer:
        return f"ID {customer_id} 고객을 찾을 수 없습니다."

    policies = db.fetch_all(
        "SELECT p.id, p.policy_no, p.status, p.main_premium, p.total_premium, p.start_date, "
        "mp.name AS product_name, mp.category, "
        "pl.coverage_period, pl.payment_period, pl.coverage_amount "
        "FROM policies p "
        "JOIN main_products mp ON mp.id = p.main_product_id "
        "JOIN main_product_plans pl ON pl.id = p.plan_id "
        "WHERE p.customer_id = :cid ORDER BY p.id",
        cid=customer_id,
    )
    for p in policies:
        p["riders"] = db.fetch_all(
            "SELECT r.name, pr.monthly_premium "
            "FROM policy_riders pr JOIN riders r ON r.id = pr.rider_id "
            "WHERE pr.policy_id = :pid ORDER BY pr.id",
            pid=p["id"],
        )

    return _json({"customer": customer["name"], "policies": policies}) if policies \
        else _json({"customer": customer["name"], "policies": [], "message": "가입된 계약이 없습니다."})


TOOLS = [
    list_customers,
    find_customers,
    get_customer,
    register_customer,
    list_main_products,
    get_product_detail,
    list_eligible_products,
    check_enrollment,
    enroll_policy,
    get_customer_policies,
]
