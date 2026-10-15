"""报告生成层：把 Analysis 渲染成 Markdown 分析报告。"""

from __future__ import annotations

from datetime import date

from metrics import Analysis, CORE_LABELS, FINANCIAL_LABELS, INTEREST_BEARING_LABELS


def yi(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v / 1e8:,.{digits}f}"


def pct_s(v: float | None, digits: int = 1, sign: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"


def num_s(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v:,.{digits}f}"


def period_label(report_date: str) -> str:
    y, m = report_date[:4], report_date[5:7]
    return {3: "一季报", 6: "半年报", 9: "三季报", 12: "年报"}.get(int(m), "定期报告")


def num_any(d: dict, key: str) -> float:
    """与 metrics.num 同义，本地取数用，避免跨模块循环依赖。"""
    v = (d or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


def pct_any(a: float, b: float) -> float | None:
    return a / b * 100 if b else None


LEVEL_ICON = {"高": "🔴 高", "中": "🟡 中", "提示": "🔵 提示"}


def render(a: Analysis, notes: dict, include_evidence: bool = True) -> str:
    s, e = a.structure, a.efficiency
    p = a.period
    L: list[str] = []

    # 章节号按实际出现的章节动态编号（同业/趋势可能缺省），避免跳号
    cn = "一二三四五六七八九十"
    counter = {"n": 0}

    def sec(title: str) -> str:
        idx = counter["n"]
        counter["n"] += 1
        return f"## {cn[idx]}、{title}"

    L.append(f"# {a.name}（{a.code}）{p.report_name} 资产负债表分析报告")
    L.append("")
    L.append(f"> 分析框架：《年报分析要点》（肖星《资产负债表的秘密》）  ")
    L.append(f"> 报告期：{p.report_date}（{period_label(p.report_date)}）  ")
    L.append(f"> 数据来源：东方财富 F10 财务报表接口、巨潮资讯网定期报告原文  ")
    L.append(f"> 生成时间：{date.today().isoformat()}")
    L.append("")

    # ---------------- 一、核心结论 ----------------
    L.append(sec("核心结论"))
    L.append("")
    if s.get("is_financial"):
        L.append("> ⚠️ **口径提示**：本公司为金融类企业，资产以贷款与金融投资为主，"
                 "不存在工商企业的「存货 / 应收账款」概念，因此**经营资产占比、"
                 "总资产周转率、现金转换周期等指标不适用于同业比较**，"
                 "下文相关数值仅供参考。")
        L.append("")
    hi = [f for f in a.findings if f.level == "高"]
    mid = [f for f in a.findings if f.level == "中"]
    L.append(f"- **资产结构**：经营资产（固定资产+在建工程+存货+应收+货币资金）"
             f"{yi(s['core_total'])} 亿元，占总资产 **{pct_s(s['core_pct'])}**；"
             f"金融资产 {yi(s['financial_total'])} 亿元，占 {pct_s(s['financial_pct'])}。")
    L.append(f"- **资金来源**：资产负债率 **{pct_s(s['debt_ratio'])}**，"
             f"其中有息负债 {yi(s['interest_bearing_total'])} 亿元，仅占总负债 "
             f"{pct_s(s['interest_bearing_pct_liab'])}。")
    L.append(f"- **经营效率**：总资产周转率 **{num_s(e['total_asset_turnover'], 3)} 次/年**，"
             f"每 1 亿元收入需投入资产约 "
             f"{num_s((e['asset_needed_per_100m_revenue'] or 0) / 1e8, 2)} 亿元。")
    L.append(f"- **风险扫描**：识别到高风险项 **{len(hi)}** 项、中风险项 **{len(mid)}** 项。")
    L.append("")

    # ---------------- 二、结构信息 ----------------
    L.append(sec("结构信息：业务模式与资金缺口"))
    L.append("")
    L.append("### 2.1 资产结构")
    L.append("")
    L.append("| 项目 | 金额（亿元） | 占总资产 |")
    L.append("|---|---:|---:|")
    for k, label in CORE_LABELS.items():
        v = s["core_assets"].get(k, 0)
        if v:
            L.append(f"| {label} | {yi(v)} | {pct_s(s['core_pct'] and v / s['total_assets'] * 100)} |")
    L.append(f"| **经营资产合计** | **{yi(s['core_total'])}** | **{pct_s(s['core_pct'])}** |")
    if s["financial_total"]:
        L.append(f"| 金融资产合计（余粮） | {yi(s['financial_total'])} | {pct_s(s['financial_pct'])} |")
    L.append(f"| **总资产** | **{yi(s['total_assets'])}** | 100.0% |")
    L.append("")
    if s["financial_detail"]:
        L.append("金融资产构成：")
        for k, v in sorted(s["financial_detail"].items(), key=lambda kv: -kv[1]):
            L.append(f"- {FINANCIAL_LABELS.get(k, k)}：{yi(v)} 亿元")
        L.append("")
    L.append("**解读**：经营资产占比反映公司维持日常经营需要占用多少资产。"
             f"本报告期经营资产占比 {pct_s(s['core_pct'])}"
             + ("，属于典型的重资产模式，需要持续大量资金投入。"
                if (s["core_pct"] or 0) > 60 else
                "，资产中相当部分是可持续变现的金融资产。"))
    L.append("")

    L.append("### 2.2 资金来源结构")
    L.append("")
    L.append("| 项目 | 金额（亿元） | 占比 |")
    L.append("|---|---:|---:|")
    L.append(f"| 总负债 | {yi(s['total_liabilities'])} | 总资产 {pct_s(s['debt_ratio'])} |")
    L.append(f"| 其中：有息负债 | {yi(s['interest_bearing_total'])} | 总负债 {pct_s(s['interest_bearing_pct_liab'])} |")
    L.append(f"| 　　　无息负债 | {yi(s['non_interest_total'])} | 总负债 {pct_s(s['non_interest_pct_liab'])} |")
    L.append(f"| 股东权益 | {yi(s['total_equity'])} | — |")
    L.append("")
    if s["interest_bearing"]:
        L.append("有息负债明细：")
        for k, v in sorted(s["interest_bearing"].items(), key=lambda kv: -kv[1]):
            L.append(f"- {INTEREST_BEARING_LABELS.get(k, k)}：{yi(v)} 亿元")
        L.append("")
    L.append(f"**资金回报**：年化总资产报酬率（利润总额+利息费用）/ 平均总资产 = "
             f"**{pct_s((s['roa_annualized'] or 0) * 100)}**"
             f"（{p.months} 个月数据按 ×{s['annualize_factor']:.2f} 年化）。")
    L.append("")

    # ---------------- 三、效率信息 ----------------
    L.append(sec("效率信息：周转率与资金闭环"))
    L.append("")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("|---|---:|---|")
    L.append(f"| 营业收入（期间） | {yi(e['revenue'])} 亿元 | 同比 {pct_s(e['revenue_yoy'], sign=True)} |")
    L.append(f"| 年化营业收入 | {yi(e['revenue_annualized'])} 亿元 | 按 {p.months} 个月年化 |")
    L.append(f"| **总资产周转率** | **{num_s(e['total_asset_turnover'], 3)} 次/年** | 收入 / 平均总资产 |")
    L.append(f"| 做 1 亿收入需投入资产 | {num_s((e['asset_needed_per_100m_revenue'] or 0) / 1e8, 2)} 亿元 | 1 亿 ÷ 周转率 |")
    L.append(f"| 应收账款周转天数 | {num_s(e['ar_days'], 1)} 天 | 反映收款能力 |")
    L.append(f"| 存货周转天数 | {num_s(e['inventory_days'], 1)} 天 | 按营业成本计算 |")
    L.append(f"| 应付账款周转天数 | {num_s(e['ap_days'], 1)} 天 | 对上游占款能力 |")
    L.append(f"| **现金转换周期** | **{num_s(e['cash_conversion_cycle'], 1)} 天** | 存货天数 + 应收天数 − 应付天数 |")
    L.append(f"| 固定资产周转率 | {num_s(e['fixed_asset_turnover'], 2)} 次/年 | — |")
    L.append("")
    ccc = e["cash_conversion_cycle"]
    if ccc is not None:
        L.append(f"**解读**：现金转换周期 {ccc:.1f} 天，意味着每完成一轮"
                 f"「付款→采购→生产→销售→收款」的循环，"
                 + (f"平均需垫付 {ccc:.0f} 天资金；" if ccc > 0 else "平均可占用上下游资金，资金闭环较快；")
                 + f"总资产周转率 {num_s(e['total_asset_turnover'], 3)} 次/年，"
                 + ("资产使用效率偏低，扩张对资金投入的依赖较强。"
                    if (e["total_asset_turnover"] or 0) < 0.5 else
                    "资产使用效率处于较好水平。"))
        L.append("")

    # ---------------- 四、风险与猫腻 ----------------
    L.append(sec("猫腻与风险扫描"))
    L.append("")
    if not a.findings:
        L.append("未识别到需特别提示的项目。")
        L.append("")
    for f in a.findings:
        L.append(f"### {LEVEL_ICON.get(f.level, f.level)}｜{f.title}")
        L.append("")
        L.append(f.detail)
        if include_evidence and f.evidence:
            ev = f.evidence.strip().replace("\n", " ")
            L.append("")
            L.append(f"> 报告原文：{ev[:400]}")
        L.append("")

    # ---------------- 五、附注信息 ----------------
    if notes:
        L.append(sec("附注提取信息"))
        L.append("")
        rc = notes.get("restricted_cash") or {}
        if rc:
            L.append(f"**受限资产**（报告第 {rc.get('page', '—')} 页）："
                     f"金额 {yi(rc.get('amount'))} 亿元，原因：{rc.get('reason', '—')}。")
            L.append("")
        aging = notes.get("ar_aging") or {}
        if aging.get("buckets"):
            L.append(f"**应收账款账龄**（报告第 {aging.get('page', '—')} 页）：")
            L.append("")
            L.append("| 账龄 | 期末余额（亿元） | 占比 |")
            L.append("|---|---:|---:|")
            total = aging.get("total") or sum(v for _, v in aging["buckets"])
            for label, v in aging["buckets"]:
                share = f"{v / total * 100:.1f}%" if total else "—"
                L.append(f"| {label} | {yi(v)} | {share} |")
            L.append(f"| **合计** | **{yi(total)}** | 100.0% |")
            L.append("")
        dep = notes.get("depreciation") or {}
        if dep.get("categories"):
            L.append(f"**固定资产折旧政策**（报告第 {dep.get('page', '—')} 页）："
                     f"方法 {dep.get('method', '—')}")
            L.append("")
            L.append("| 类别 | 折旧方法 | 折旧年限 | 残值率 |")
            L.append("|---|---|---|---|")
            for c in dep["categories"]:
                L.append(f"| {c['category']} | {c['method']} | {c['years']} | {c['residual']} |")
            L.append("")

    # ---------------- 六、同业对照 ----------------
    if a.peers:
        L.append(sec("同业横向对照"))
        L.append("")
        L.append("| 公司 | 总资产(亿) | 资产负债率 | 有息负债/总负债 | 总资产周转率 | 经营资产占比 |")
        L.append("|---|---:|---:|---:|---:|---:|")
        L.append(f"| **{a.name}** | {yi(s['total_assets'])} | {pct_s(s['debt_ratio'])} | "
                 f"{pct_s(s['interest_bearing_pct_liab'])} | {num_s(e['total_asset_turnover'], 3)} | "
                 f"{pct_s(s['core_pct'])} |")
        for code, m in a.peers.items():
            L.append(f"| {m['name']} | {yi(m['total_assets'])} | {pct_s(m['debt_ratio'])} | "
                     f"{pct_s(m['ib_pct_liab'])} | {num_s(m['turnover'], 3)} | {pct_s(m['core_pct'])} |")
        L.append("")
        L.append("> 单一公司的负债率、周转率无法判断好坏，必须横向比：上表可看出本公司在"
                 "杠杆水平与资产使用效率上相对同业的位置。")
        L.append("")

    # ---------------- 七、历史趋势 ----------------
    if len(a.history) > 1:
        L.append(sec("历年同期趋势"))
        L.append("")
        L.append("| 报告期 | 总资产(亿) | 营业收入(亿) | 净利润(亿) | 资产负债率 |")
        L.append("|---|---:|---:|---:|---:|")
        for hp in a.history:
            hb, hi = hp.balance, hp.income
            hta = num_any(hb, "TOTAL_ASSETS")
            htl = num_any(hb, "TOTAL_LIABILITIES")
            L.append(f"| {hp.report_name} | {yi(hta)} | {yi(num_any(hi, 'OPERATE_INCOME'))} | "
                     f"{yi(num_any(hi, 'NETPROFIT'))} | {pct_s(pct_any(htl, hta))} |")
        L.append("")
        L.append("> 均为同一季度报告期，可纵向观察资产、收入与杠杆的演变。")
        L.append("")

    # ---------------- 八、口径说明 ----------------
    L.append(sec("数据与口径说明"))
    L.append("")
    L.append(f"1. 报告期 {p.report_date} 覆盖 {p.months} 个月，"
             f"流量指标（收入、周转率、回报率）按 ×{s['annualize_factor']:.2f} 年化。")
    L.append("2. 平均余额采用（期末 + 上年同期期末）/ 2；无同期数据时退回期末余额。")
    L.append("3. 同业对照使用同一报告期数据，避免季节性错配。")
    L.append("4. 附注信息取自定期报告 PDF 原文，已标注页码，可回溯核对。")
    L.append("5. 本报告为财务分析方法演示，不构成任何投资建议。")
    L.append("")
    return "\n".join(L)
