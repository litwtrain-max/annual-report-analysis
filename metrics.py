"""指标计算层：把三大报表还原成《年报分析要点》定义的三大板块指标。

对应关系：
  结构信息  -> structure()
  效率信息  -> efficiency()
  猫腻扫描  -> risk_scan()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fetch import Period


# ---------------------------------------------------------------- 工具函数
def num(d: dict, key: str) -> float:
    """取数值字段，缺失或非数值返回 0.0。"""
    v = (d or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


def safe_div(a: float, b: float) -> float | None:
    return a / b if b not in (0, None) else None


def pct(a: float, b: float) -> float | None:
    r = safe_div(a, b)
    return r * 100 if r is not None else None


def avg(cur: float, prev: float | None) -> float:
    """期末+期初平均；无上期则退回期末。"""
    if prev is None:
        return cur
    return (cur + prev) / 2


# 资产/负债科目归集 -----------------------------------------------------
OPERATING_ASSET_KEYS = [
    "FIXED_ASSET", "CIP", "USERIGHT_ASSET", "INTANGIBLE_ASSET",
    "INVENTORY", "NOTE_ACCOUNTS_RECE", "PREPAYMENT", "CONTRACT_ASSET",
    "MONETARYFUNDS", "PROJECT_MATERIAL", "PRODUCTIVE_BIOLOGY_ASSET",
]
# 严格意义的"过日子"四件套（讲座口径）
CORE_OPERATING_KEYS = ["FIXED_ASSET", "CIP", "INVENTORY", "NOTE_ACCOUNTS_RECE", "MONETARYFUNDS"]

FINANCIAL_ASSET_KEYS = [
    "TRADE_FINASSET", "TRADE_FINASSET_NOTFVTPL", "FVTPL_FINASSET",
    "OTHER_EQUITY_INVEST", "OTHER_NONCURRENT_FINASSET", "CREDITOR_INVEST",
    "OTHER_CREDITOR_INVEST", "HOLD_MATURITY_INVEST", "AVAILABLE_SALE_FINASSET",
    "DERIVE_FINASSET", "FINANCE_RECE", "LONG_EQUITY_INVEST",
]

# 有息负债科目
INTEREST_BEARING_KEYS = [
    "SHORT_LOAN", "NONCURRENT_LIAB_1YEAR", "LONG_LOAN", "BOND_PAYABLE",
    "SHORT_BOND_PAYABLE", "LEASE_LIAB", "LONG_PAYABLE",
    "SHORT_FIN_PAYABLE", "TRADE_FINLIAB", "TRADE_FINLIAB_NOTFVTPL",
    "PERPETUAL_BOND_PAYBALE", "INTEREST_PAYABLE",
]
# 无息负债科目
NON_INTEREST_KEYS = [
    "NOTE_ACCOUNTS_PAYABLE", "CONTRACT_LIAB", "ADVANCE_RECEIVABLES",
    "STAFF_SALARY_PAYABLE", "TAX_PAYABLE", "PREDICT_LIAB",
    "PREDICT_CURRENT_LIAB", "DEFER_INCOME", "DEFER_INCOME_1YEAR",
    "OTHER_PAYABLE", "DIVIDEND_PAYABLE", "SPECIAL_PAYABLE",
]

INTEREST_BEARING_LABELS = {
    "SHORT_LOAN": "短期借款", "NONCURRENT_LIAB_1YEAR": "一年内到期的非流动负债",
    "LONG_LOAN": "长期借款", "BOND_PAYABLE": "应付债券",
    "SHORT_BOND_PAYABLE": "应付短期债券", "LEASE_LIAB": "租赁负债",
    "LONG_PAYABLE": "长期应付款", "SHORT_FIN_PAYABLE": "应付短期融资款",
    "TRADE_FINLIAB": "交易性金融负债", "TRADE_FINLIAB_NOTFVTPL": "指定以公允价值计量的金融负债",
    "PERPETUAL_BOND_PAYBALE": "应付永续债", "INTEREST_PAYABLE": "应付利息",
}

CORE_LABELS = {
    "FIXED_ASSET": "固定资产", "CIP": "在建工程", "USERIGHT_ASSET": "使用权资产",
    "INTANGIBLE_ASSET": "无形资产", "INVENTORY": "存货",
    "NOTE_ACCOUNTS_RECE": "应收票据及应收账款", "PREPAYMENT": "预付款项",
    "CONTRACT_ASSET": "合同资产", "MONETARYFUNDS": "货币资金",
    "PROJECT_MATERIAL": "工程物资", "PRODUCTIVE_BIOLOGY_ASSET": "生产性生物资产",
}

FINANCIAL_LABELS = {
    "TRADE_FINASSET": "交易性金融资产", "TRADE_FINASSET_NOTFVTPL": "以公允价值计量且其变动计入其他综合收益的金融资产",
    "FVTPL_FINASSET": "以公允价值计量且其变动计入当期损益的金融资产",
    "OTHER_EQUITY_INVEST": "其他权益工具投资",
    "OTHER_NONCURRENT_FINASSET": "其他非流动金融资产",
    "CREDITOR_INVEST": "债权投资", "OTHER_CREDITOR_INVEST": "其他债权投资",
    "HOLD_MATURITY_INVEST": "持有至到期投资", "AVAILABLE_SALE_FINASSET": "可供出售金融资产",
    "DERIVE_FINASSET": "衍生金融资产", "FINANCE_RECE": "应收款项融资",
    "LONG_EQUITY_INVEST": "长期股权投资",
}


@dataclass
class Finding:
    """一条风险/提示项。"""

    level: str        # 高 / 中 / 提示
    title: str
    detail: str
    evidence: str = ""
    metric: str = ""


@dataclass
class Analysis:
    code: str
    name: str
    period: Period
    history: list[Period] = field(default_factory=list)
    structure: dict[str, Any] = field(default_factory=dict)
    efficiency: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    peers: dict[str, Any] = field(default_factory=dict)
    quote: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- 结构分析
def structure(p: Period, hist: list[Period]) -> dict[str, Any]:
    b, i = p.balance, p.income
    ta = num(b, "TOTAL_ASSETS")
    tl = num(b, "TOTAL_LIABILITIES")
    te = num(b, "TOTAL_EQUITY")

    # 金融企业（银行/保险/券商）的资产以贷款、金融投资为主，不存在"存货/应收账款"
    # 这类经营资产概念，周转率与经营资产占比口径不适用，需单独标记。
    # 注意：F10 接口会返回全部科目键（未适用者为 null），因此必须按"有值"判断，
    # 不能用键是否存在来判断。
    is_financial = any(num(b, k) > 0 for k in
                       ("LOAN_ADVANCE", "ACCEPT_DEPOSIT_INTERBANK", "BORROW_FUND",
                        "LOAN_PBC", "LEND_FUND", "INSURANCE_CONTRACT_RESERVE"))

    core = {k: num(b, k) for k in CORE_OPERATING_KEYS}
    core_total = sum(core.values())
    op_total = sum(num(b, k) for k in OPERATING_ASSET_KEYS)
    fin = {k: num(b, k) for k in FINANCIAL_ASSET_KEYS if num(b, k)}
    fin_total = sum(fin.values())

    ib = {k: num(b, k) for k in INTEREST_BEARING_KEYS if num(b, k)}
    ib_total = sum(ib.values())
    non_ib = sum(num(b, k) for k in NON_INTEREST_KEYS)

    # 上期（同一报告期，去年同期）
    prev = hist[1] if len(hist) > 1 else None
    prev_ta = num(prev.balance, "TOTAL_ASSETS") if prev else None

    # 年化总资产报酬率：(利润总额 + 利息费用) / 平均总资产
    months = p.months
    annualize = 12 / months if months else 1
    ebit = num(i, "TOTAL_PROFIT") + num(i, "FE_INTEREST_EXPENSE")
    avg_ta = avg(ta, prev_ta)
    roa = safe_div(ebit * annualize, avg_ta)

    # 货币资金受限需从附注补充，这里只算报表口径
    return {
        "total_assets": ta,
        "total_liabilities": tl,
        "total_equity": te,
        "is_financial": is_financial,
        "core_assets": core,
        "core_total": core_total,
        "core_pct": pct(core_total, ta),
        "operating_total": op_total,
        "operating_pct": pct(op_total, ta),
        "financial_total": fin_total,
        "financial_pct": pct(fin_total, ta),
        "financial_detail": fin,
        "debt_ratio": pct(tl, ta),
        "interest_bearing": ib,
        "interest_bearing_total": ib_total,
        "interest_bearing_pct_assets": pct(ib_total, ta),
        "interest_bearing_pct_liab": pct(ib_total, tl),
        "non_interest_total": non_ib,
        "non_interest_pct_liab": pct(non_ib, tl),
        "roa_annualized": roa,
        "annualize_factor": annualize,
        "months": months,
        "total_assets_yoy": pct(ta - prev_ta, prev_ta) if prev_ta else None,
        "total_liabilities_yoy": (
            pct(tl - num(prev.balance, "TOTAL_LIABILITIES"), num(prev.balance, "TOTAL_LIABILITIES"))
            if prev and num(prev.balance, "TOTAL_LIABILITIES") else None),
        "interest_expense": num(i, "FE_INTEREST_EXPENSE"),
    }


# ---------------------------------------------------------------- 效率分析
def efficiency(p: Period, hist: list[Period]) -> dict[str, Any]:
    b, i = p.balance, p.income
    months = p.months
    annualize = 12 / months if months else 1
    days = months * 30.4375  # 期间天数（半年=182.6）

    ta = num(b, "TOTAL_ASSETS")
    prev = hist[1] if len(hist) > 1 else None
    prev_b = prev.balance if prev else None

    revenue = num(i, "OPERATE_INCOME")
    cost = num(i, "OPERATE_COST")

    avg_ta = avg(ta, num(prev_b, "TOTAL_ASSETS") if prev_b else None)
    turn = safe_div(revenue * annualize, avg_ta)

    def turnover_days(cur_key: str, base: float) -> tuple[float | None, float | None]:
        cur = num(b, cur_key)
        a = avg(cur, num(prev_b, cur_key) if prev_b else None)
        d = safe_div(a * days, base)
        t = safe_div(base * annualize, a)
        return d, t

    ar_days, ar_turn = turnover_days("NOTE_ACCOUNTS_RECE", revenue)
    inv_days, inv_turn = turnover_days("INVENTORY", cost or revenue)
    ap_days, _ = turnover_days("NOTE_ACCOUNTS_PAYABLE", cost or revenue)
    fa_days, fa_turn = turnover_days("FIXED_ASSET", revenue)

    core_total = sum(num(b, k) for k in CORE_OPERATING_KEYS)
    prev_core = sum(num(prev_b, k) for k in CORE_OPERATING_KEYS) if prev_b else None
    avg_core = avg(core_total, prev_core)
    core_turn = safe_div(revenue * annualize, avg_core)

    ccc = None
    if ar_days is not None and inv_days is not None and ap_days is not None:
        ccc = ar_days + inv_days - ap_days

    return {
        "revenue": revenue,
        "revenue_annualized": revenue * annualize,
        "revenue_yoy": num(i, "OPERATE_INCOME_YOY"),
        "total_asset_turnover": turn,
        "revenue_per_1e8_asset": safe_div(revenue * annualize, avg_ta),
        "asset_needed_per_100m_revenue": safe_div(1e8, turn) if turn else None,
        "ar_days": ar_days, "ar_turnover": ar_turn,
        "inventory_days": inv_days, "inventory_turnover": inv_turn,
        "ap_days": ap_days,
        "fixed_asset_turnover": fa_turn,
        "core_asset_turnover": core_turn,
        "cash_conversion_cycle": ccc,
        "days_in_period": days,
    }


# ---------------------------------------------------------------- 猫腻扫描
def risk_scan(p: Period, hist: list[Period], s: dict, e: dict,
              notes: dict | None = None) -> list[Finding]:
    """按《年报分析要点》阶段 4 的 R1–R10 检查项扫描。"""
    notes = notes or {}
    b, i, c = p.balance, p.income, p.cashflow
    out: list[Finding] = []
    prev = hist[1] if len(hist) > 1 else None

    # --- R1 受限现金 ---
    r1 = notes.get("restricted_cash") or {}
    mf = num(b, "MONETARYFUNDS")
    if r1.get("amount") and mf:
        ratio = r1["amount"] / mf * 100
        lvl = "高" if ratio > 30 and r1.get("is_frozen") else ("中" if ratio > 15 else "提示")
        out.append(Finding(
            lvl, "受限货币资金",
            f"受限货币资金 {r1['amount'] / 1e8:.2f} 亿元，占货币资金 {ratio:.1f}%。"
            f"受限原因：{r1.get('reason', '未明确')}",
            evidence=r1.get("raw", ""),
            metric=f"{ratio:.1f}%"))

    # --- R2 应收增速 vs 收入增速 ---
    ar_yoy = num(b, "NOTE_ACCOUNTS_RECE_YOY")
    rev_yoy = num(i, "OPERATE_INCOME_YOY")
    ar = num(b, "NOTE_ACCOUNTS_RECE")
    ar_share = pct(ar, num(b, "TOTAL_ASSETS"))
    if ar and (ar_yoy - rev_yoy) > 20:
        # 余额占比过低时增速信号意义有限，降级为提示并说明
        lvl = "提示" if (ar_share or 0) < 5 else "中"
        out.append(Finding(
            lvl, "应收账款增速显著高于收入",
            f"应收票据及应收账款同比 {ar_yoy:+.1f}%，收入同比 {rev_yoy:+.1f}%，"
            f"差额 {ar_yoy - rev_yoy:.1f} 个百分点。"
            + (f"但该科目仅占总资产 {ar_share:.1f}%，绝对规模小，对整体资产质量影响有限。"
               if lvl == "提示" else
               "需关注是否通过放宽信用政策换取收入，以及后续回款情况。"),
            metric=f"{ar_yoy:+.1f}% vs {rev_yoy:+.1f}%"))

    # --- R3/R4 账龄结构与坏账计提 ---
    aging = notes.get("ar_aging") or {}
    prov = notes.get("ar_provision") or {}
    if aging.get("buckets"):
        buckets = aging["buckets"]
        total = sum(v for _, v in buckets) or aging.get("total") or 0
        over2 = sum(v for label, v in buckets if any(t in label for t in ("2 至3", "2至3", "3 年", "3年")))
        if total > 0 and over2 / total > 0.15:
            out.append(Finding(
                "高", "长账龄应收账款占比偏高",
                f"2 年以上应收账款 {over2 / 1e8:.2f} 亿元，占应收账款余额 {over2 / total * 100:.1f}%。"
                f"回收概率显著下降，坏账风险较高。",
                evidence=aging.get("raw", ""), metric=f"{over2 / total * 100:.1f}%"))
        elif total > 0 and over2 / total > 0.05:
            out.append(Finding(
                "中", "存在一定长账龄应收账款",
                f"2 年以上应收账款占应收账款余额 {over2 / total * 100:.1f}%，需持续跟踪回收情况。",
                metric=f"{over2 / total * 100:.1f}%"))
        if total > 0 and prov.get("amount"):
            pr = prov["amount"] / total * 100
            out.append(Finding(
                "提示", "坏账准备计提水平",
                f"坏账准备 {prov['amount'] / 1e8:.2f} 亿元，占应收账款余额 {pr:.1f}%。"
                f"需结合账龄结构判断计提是否充分。",
                evidence=prov.get("raw", ""), metric=f"{pr:.1f}%"))

    # --- R5 存货异常 ---
    inv_yoy = num(b, "INVENTORY_YOY")
    if num(b, "INVENTORY"):
        if inv_yoy - rev_yoy > 30:
            out.append(Finding(
                "中", "存货增速远超收入",
                f"存货同比 {inv_yoy:+.1f}%，收入同比 {rev_yoy:+.1f}%。"
                f"可能滞销或跌价准备计提不足，需关注存货构成与库龄。",
                metric=f"{inv_yoy:+.1f}% vs {rev_yoy:+.1f}%"))
        elif inv_yoy < -20 and rev_yoy > 0:
            out.append(Finding(
                "提示", "存货大幅下降而收入增长",
                f"存货同比 {inv_yoy:+.1f}%，收入同比 {rev_yoy:+.1f}%。"
                f"通常反映去库存与产销改善，属正常经营向好信号。",
                metric=f"{inv_yoy:+.1f}%"))

    # --- R6 利润含金量 ---
    npf = num(i, "NETPROFIT")
    ocf = num(c, "NETCASH_OPERATE")
    if npf > 0:
        quality = ocf / npf
        lvl = "高" if quality < 0.3 else ("中" if quality < 0.6 else "提示")
        if quality < 0.6:
            out.append(Finding(
                lvl, "利润含金量偏低",
                f"经营活动现金流净额 {ocf / 1e8:.2f} 亿元 / 净利润 {npf / 1e8:.2f} 亿元 = {quality:.2f}。"
                f"利润未能同步转化为现金，可能存在应收或存货占用。",
                metric=f"{quality:.2f}"))
        else:
            out.append(Finding(
                "提示", "利润含金量良好",
                f"经营活动现金流净额 / 净利润 = {quality:.2f}，利润有真实现金支撑。",
                metric=f"{quality:.2f}"))
    elif npf < 0 and ocf > 0:
        out.append(Finding(
            "提示", "亏损但经营现金流为正",
            f"净利润 {npf / 1e8:.2f} 亿元，经营活动现金流净额 {ocf / 1e8:.2f} 亿元。"
            f"亏损主要来自非付现项目（如折旧、减值），需看具体构成。"))

    # --- R7 折旧政策（附注） ---
    dep = notes.get("depreciation") or {}
    if dep.get("years_range"):
        out.append(Finding(
            "提示", "固定资产折旧政策",
            f"主要类别折旧年限：{dep['years_range']}；折旧方法：{dep.get('method', '未明确')}。"
            f"重资产行业折旧年限的微小差异会显著影响利润，建议与同业横向对比。",
            evidence=dep.get("raw", "")))

    # 隐含折旧年数（用现金流量表折旧额，非付现成本）
    fa = num(b, "FIXED_ASSET")
    dep_amount = num(c, "FA_IR_DEPR")
    if fa and dep_amount:
        implied_years = fa / dep_amount
        months = p.months
        implied_years_annualized = implied_years * (months / 12)
        out.append(Finding(
            "提示", "固定资产隐含折旧年数",
            f"固定资产账面价值 {fa / 1e8:.2f} 亿元 ÷ 当期折旧 {dep_amount / 1e8:.2f} 亿元"
            f"（年化后约 {dep_amount * (12 / months) / 1e8:.2f} 亿元）= 约 {implied_years_annualized:.1f} 年折完。"
            f"数值越大说明折旧越慢、当期利润越宽松。",
            metric=f"{implied_years_annualized:.1f} 年"))

    # --- R8 减值损失波动 ---
    ai = num(i, "ASSET_IMPAIRMENT_INCOME")
    ci = num(i, "CREDIT_IMPAIRMENT_INCOME")
    if ai or ci:
        total_imp = ai + ci
        if total_imp < 0:
            out.append(Finding(
                "提示", "本期计提减值损失",
                f"资产减值损失 {ai / 1e8:.2f} 亿元、信用减值损失 {ci / 1e8:.2f} 亿元，"
                f"合计 {total_imp / 1e8:.2f} 亿元。关注是否存在大额一次性计提或后续转回（跨期调节利润痕迹）。"))
        else:
            out.append(Finding(
                "提示", "本期减值损失为转回",
                f"资产减值损失 {ai / 1e8:.2f} 亿元、信用减值损失 {ci / 1e8:.2f} 亿元，合计为正。"
                f"转回会直接增厚当期利润，需核实转回依据是否充分。"))

    # --- R9 其他综合收益 ---
    oci = num(i, "PARENT_OCI")
    if oci < 0 and abs(oci) > num(b, "TOTAL_PARENT_EQUITY") * 0.01:
        out.append(Finding(
            "中", "其他综合收益大额负值",
            f"归属于母公司其他综合收益 {oci / 1e8:.2f} 亿元，"
            f"占归母净资产 {abs(pct(oci, num(b, 'TOTAL_PARENT_EQUITY')) or 0):.1f}%。"
            f"利润表未体现，但已侵蚀净资产，属典型的资产负债表演示的隐藏信息。",
            evidence=f"其他权益工具投资公允价值变动 {num(i, 'OTHERRIGHT_FAIRVALUE_CHANGE') / 1e8:.2f} 亿元",
            metric=f"{oci / 1e8:.2f} 亿元"))

    # --- R10 利息保障倍数 ---
    ie = num(i, "FE_INTEREST_EXPENSE")
    tp = num(i, "TOTAL_PROFIT")
    if ie > 0:
        cover = (tp + ie) / ie
        if cover < 3:
            out.append(Finding(
                "高" if cover < 1.5 else "中", "利息保障倍数偏低",
                f"(利润总额 {tp / 1e8:.2f} 亿 + 利息费用 {ie / 1e8:.2f} 亿) / 利息费用 = {cover:.1f} 倍。"
                f"覆盖利息的能力偏弱，需关注有息负债的可持续性。",
                metric=f"{cover:.1f} 倍"))
    else:
        out.append(Finding(
            "提示", "利息费用极低或无",
            f"当期利息费用 {ie / 1e8:.2f} 亿元，公司有息负债负担轻，财务风险低。"))

    # --- 补充：无息负债依赖度（结构风险的延伸判断） ---
    ib_pct = s.get("interest_bearing_pct_liab")
    if ib_pct is not None and s.get("debt_ratio") and s["debt_ratio"] > 50 and ib_pct < 20:
        out.append(Finding(
            "提示", "高负债率主要来自无息负债",
            f"资产负债率 {s['debt_ratio']:.1f}%，但有息负债仅占总负债 {ib_pct:.1f}%，"
            f"负债主要来自对上游的占款（应付账款等）。财务费用压力小，但依赖持续经营与供应商信用。"))

    # --- 补充：金融资产占比高 ---
    if s.get("financial_pct") and s["financial_pct"] > 20:
        out.append(Finding(
            "提示", "账上金融资产占比较高",
            f"金融资产 {s['financial_total'] / 1e8:.2f} 亿元，占总资产 {s['financial_pct']:.1f}%，"
            f"属手里的余粮，说明维持日常经营并不需要这么多资产。"))

    # 排序：高 → 中 → 提示
    order = {"高": 0, "中": 1, "提示": 2}
    out.sort(key=lambda f: order.get(f.level, 3))
    return out
