"""附注解析层：从定期报告 PDF 中提取报表看不到的关键信息。

对应《年报分析要点》阶段 4 中需要附注支撑的检查项：
  - R1 受限货币资金（资产权利受限情况）
  - R3/R4 应收账款账龄结构与坏账准备
  - R7 固定资产折旧政策

这些信息不在结构化财务接口里，必须回到 PDF 原文提取，并保留原文片段作为证据。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

# 页面词组坐标缓存：load_pages 填充，列对齐类解析器读取
_WORDS_CACHE: dict[int, list] = {}

# 金额：1,234,567.89
NUM = r"-?[\d,]+\.\d{2}"
# 金额（宽松）：允许无小数位——以千元为单位披露时通常不带小数，如 27,212,271。
# 分支顺序很重要：必须先匹配「带千分位且带小数」的完整形式，否则 17,188,254.45
# 会被前一个分支截成 17,188,254 + 残留 ".45"。
# 且整体不含裸整数分支，避免与账龄标签里的年份数字（"1 年以内" 的 1）混淆。
AMT = r"-?(?:\d{1,3}(?:,\d{3})+\.\d+|\d+\.\d+|\d{1,3}(?:,\d{3})+)"
NUM_RE = re.compile(NUM)


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _unit_multiplier(text: str) -> float:
    """识别报表金额单位。多数公司以「元」披露，但部分公司（如上海电气）以「千元」披露，
    不换算会导致附注金额与资产负债表口径相差三个数量级。
    """
    m = re.search(r"单位\s*[:：]\s*(千元|万元|百万元|元)", text)
    if not m:
        return 1.0
    return {"千元": 1e3, "万元": 1e4, "百万元": 1e6, "元": 1.0}[m.group(1)]


def load_pages(pdf_path: str | Path) -> list[str]:
    doc = pymupdf.open(str(pdf_path))
    try:
        pages = [p.get_text() for p in doc]
        # 同步缓存每页的词组坐标，供需要列对齐的表格解析使用
        _WORDS_CACHE.clear()
        for i, p in enumerate(doc):
            _WORDS_CACHE[i] = p.get_text("words")
        return pages
    finally:
        doc.close()


def _find_pages(pages: list[str], *keywords: str) -> list[int]:
    """返回同时包含所有关键词的页码（0 基）。"""
    out = []
    for idx, text in enumerate(pages):
        if all(k in text for k in keywords):
            out.append(idx)
    return out


# ------------------------------------------------------------ R1 受限资产
def extract_restricted_cash(pages: list[str]) -> dict:
    """提取「资产权利受限情况」中的货币资金受限金额与原因。

    注意：同一页常同时披露土地使用权/固定资产的抵押，判断货币资金的受限性质时
    必须限定在描述该笔货币资金的句子内，否则会把别的资产的抵押误判为现金冻结。
    """
    for idx in _find_pages(pages, "资产权利受限", "货币资金"):
        text = pages[idx]
        unit = _unit_multiplier(text)
        seg = text.split("资产权利受限", 1)[1]
        m = re.search(r"货币资金\s*(" + AMT + r")\s*(受限情况|详见下述说明|详见)", seg)
        amount = _to_float(m.group(1)) * unit if m else None
        # 原因说明段（以该笔货币资金为主语的句子）
        reason_raw = ""
        reason_m = re.search(
            r"(" + AMT + r")\s*元货币资金为受限制的货币资金[，,]?(.{0,160})",
            text, re.S)
        if reason_m:
            amount = amount or _to_float(reason_m.group(1)) * unit
            reason_raw = re.sub(r"\s+", "", reason_m.group(2))
        if amount is None:
            alt = re.search(r"受限[^\d]{0,20}(" + AMT + r")", text)
            if alt:
                amount = _to_float(alt.group(1)) * unit
        # 仅在该笔现金的描述范围内判断是否被冻结/质押
        scope = reason_raw or ""
        frozen = any(k in scope for k in ("冻结", "质押", "查封"))
        reason = _classify_restrict(scope or text)
        return {
            "amount": amount,
            "reason": reason,
            "is_frozen": frozen,
            "raw": reason_raw or text.strip()[:300],
            "page": idx + 1,
        }
    return {}


def _classify_restrict(text: str) -> str:
    hits = []
    for kw, label in [("冻结", "银行冻结"), ("质押", "质押"), ("查封", "查封"),
                      ("抵押", "抵押"), ("土地复垦", "土地复垦及环境治理恢复金"),
                      ("保证金", "保证金"), ("预售", "预售监管资金")]:
        if kw in text:
            hits.append(label)
    return "、".join(dict.fromkeys(hits)) if hits else "未明确"


# ------------------------------------------------------------ R3/R4 应收
def _aging_range(label: str) -> tuple[float, float] | None:
    """把账龄标签映射为 (下界, 上界)，用于识别小计与明细的包含关系。"""
    label = label.replace(" ", "")
    m = re.match(r"^(\d+)年以内", label)
    if m:
        return 0.0, float(m.group(1))
    m = re.match(r"^(\d+)至(\d+)年$", label)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.match(r"^(\d+)年以上$", label)
    if m:
        return float(m.group(1)), float("inf")
    return None


def extract_ar_aging(pages: list[str]) -> dict:
    """提取应收账款按账龄披露表的**期末**余额。

    关键：账龄表中存在「本期无余额」的空白单元格（如茅台只有「5 年以上」一行有
    期初数、期末为空）。按文本行解析会把期初数字误当成期末数，从而虚增长账龄占比、
    产生假的高风险结论。因此必须用 PDF 文字坐标做列对齐：
      1. 由「期末账面余额 / 期初账面余额」表头定位两列的 x 中心；
      2. 每个数字按 x 坐标归属到最近的表头列；
      3. 只采纳落在「期末」列的数字。
    若无法定位表头则放弃解析，宁缺勿错。
    """
    for idx in _find_pages(pages, "应收账款", "按账龄披露"):
        words = _page_words(pages, idx)
        if not words:
            continue
        unit = _unit_multiplier(pages[idx])

        # 定位表头行与两列中心
        hdr_end = None
        cur_x = prior_x = None
        for w in words:
            if "期末账面余额" in w[4]:
                cur_x, hdr_end = (w[0] + w[2]) / 2, w[3]
            elif "期初账面余额" in w[4]:
                prior_x = (w[0] + w[2]) / 2
        if cur_x is None or prior_x is None:
            continue

        # 表头下方、按 y 聚合成行
        rows: dict[float, list] = {}
        for w in words:
            if w[1] <= hdr_end:
                continue
            key = round(w[1] / 4)
            rows.setdefault(key, []).append(w)

        raw_buckets: list[tuple[str, float]] = []
        for key in sorted(rows):
            ws = sorted(rows[key], key=lambda w: w[0])
            label, values = _split_aging_row(ws, cur_x, prior_x)
            if not label or _aging_range(label) is None:
                continue
            if values["current"] is not None:
                raw_buckets.append((label, values["current"] * unit))

        # 剔除等宽重复行与被更宽区间包含的明细行
        buckets: list[tuple[str, float]] = []
        for label, val in raw_buckets:
            rng = _aging_range(label)
            redundant = False
            for kept_label, _ in buckets:
                kr = _aging_range(kept_label)
                if kr and rng and (kr == rng or (kr[0] <= rng[0] and rng[1] <= kr[1])):
                    redundant = True
                    break
            if not redundant:
                buckets.append((label, val))

        if not buckets:
            continue
        # 合计行：与「合计」同 y 的期末列数字
        total = None
        for key in sorted(rows):
            ws = rows[key]
            if any("合计" in w[4] for w in ws):
                nums = [w for w in ws if re.fullmatch(AMT, w[4])]
                if nums:
                    closest = min(nums, key=lambda w: abs((w[0] + w[2]) / 2 - cur_x))
                    total = _to_float(closest[4]) * unit
                break
        if total is None:
            total = sum(v for _, v in buckets)
        return {"buckets": buckets, "total": total, "unit_multiplier": unit,
                "raw": _aging_raw(pages[idx]), "page": idx + 1}
    return {}


def _page_words(pages: list[str], idx: int) -> list:
    """取该页带坐标的词组。需要在 load_pages 之外单独取，故此处重新打开文档。"""
    return _WORDS_CACHE.get(idx) or []


def _split_aging_row(ws: list, cur_x: float, prior_x: float) -> tuple[str, dict]:
    """把一行词组拆成 账龄标签 + 期末/期初数值。"""
    label_parts, values = [], {"current": None, "prior": None}
    for w in ws:
        txt = w[4]
        if re.fullmatch(AMT, txt):
            cx = (w[0] + w[2]) / 2
            which = "current" if abs(cx - cur_x) <= abs(cx - prior_x) else "prior"
            if values[which] is None:
                values[which] = _to_float(txt)
        else:
            # 标签列位于两数值列左侧
            if (w[0] + w[2]) / 2 < min(cur_x, prior_x) - 10:
                label_parts.append(txt)
    return re.sub(r"\s+", "", "".join(label_parts)), values


def _aging_raw(text: str) -> str:
    seg = text.split("按账龄披露", 1)[-1]
    seg = re.split(r"（2）|按坏账计提方法|按欠款方归集", seg)[0]
    return re.sub(r"\n+", " / ", seg.strip()[:500])


def extract_ar_provision(pages: list[str]) -> dict:
    """提取应收账款坏账准备期末余额（按披露单位换算为元）。

    唯一可靠的版式是「本期计提、收回或转回的坏账准备情况」变动表：
        应收账款坏账准备 / 期初余额 / 计提 / 收回或转回 / 核销 / 其他 / 期末余额
    因此取该科目名下、出现在「合计」之前的最后一个数字作为期末余额。
    其他版式（按组合计提表）常与应收票据、合同资产同页混排，数字序列含义
    随公司而变（账面余额/计提比例/坏账准备顺序不固定），故不做兜底猜测——
    宁可留空，也不报告一个可能错误的风险指标。
    """
    for idx in _find_pages(pages, "应收账款坏账准备"):
        text = pages[idx]
        unit = _unit_multiplier(text)
        # 数值之间可能以换行分隔，也可能因版式压缩在同一行以空格分隔
        for m in re.finditer(r"应收账款坏账准备\s*((?:\s*" + AMT + r"){2,8})", text):
            nums = [_to_float(v) for v in re.findall(AMT, m.group(1))]
            nums = [n for n in nums if n is not None]
            if len(nums) < 2:
                continue
            tail = text[m.end():m.end() + 40]
            # 若紧随「合计」，则科目行末值即期末余额；否则取最后一个数
            val = nums[-1]
            if val:
                return {"amount": val * unit, "unit_multiplier": unit,
                        "values": nums,
                        "raw": re.sub(r"\s+", " ", text[m.start():m.end() + 60]),
                        "page": idx + 1}
    return {}


# ------------------------------------------------------------ R7 折旧政策
DEP_METHODS = ("年限平均法", "直线法", "工作量法", "双倍余额递减法", "年数总和法", "不适用")
# 类别名通常以这些词结尾；用于在无序文本中识别折旧表的行首
DEP_CAT_HINT = ("房屋及建筑物", "建筑物", "机器设备", "设备", "运输工具", "运输设备",
                "电子设备", "办公设备", "工具", "器具", "家具", "土地", "其他")
YEARS_PAT = re.compile(r"^(?:永久|\d+\s*-\s*\d+\s*年?|\d+\s*年?)$")


def extract_depreciation(pages: list[str]) -> dict:
    """提取固定资产折旧方法、折旧年限、残值率。

    折旧表各行依次为「类别 / 折旧方法 / 折旧年限 / 残值率 / 年折旧率」，但公司间
    差异很大：类别名不固定（电子设备、专用设备、办公及其他设备…）、方法有年限平均法与
    直线法、年限可能不带「年」字（10-50）、残值率有 3%至5% 与 0.00-5.00 两种写法。
    因此不做逐列正则，而是定位表头后按行滑动，用「类别词 + 已知方法 + 年限型 token」
    的组合识别行，兼容性更好。
    """
    for idx in _find_pages(pages, "折旧年限"):
        text = pages[idx]
        method_hint = next((m for m in DEP_METHODS if m in text), "未明确")
        hdr = re.search(r"类别\s*\n?\s*折旧方法\s*\n?\s*折旧年限", text)
        seg = text[hdr.end():hdr.end() + 1200] if hdr else text
        # 主表之后常紧跟「减值测试」等小节，或另起一张研发设备折旧表；
        # 截断可避免把附加表（如研发仪器设备）误并入固定资产折旧政策
        cut = re.search(r"减值测试|减值准备计提|如下：|新购进的专门用于研发", seg)
        if cut:
            seg = seg[:cut.start()]
        lines = [ln.strip() for ln in seg.split("\n") if ln.strip()]

        cats: list[dict] = []
        i = 0
        while i < len(lines):
            token = lines[i]
            # 行首须是类别名（含类别提示词且本身不是方法/年限/纯数字）
            is_cat = (any(h in token for h in DEP_CAT_HINT)
                      and not any(m in token for m in DEP_METHODS)
                      and not YEARS_PAT.match(token)
                      and not re.fullmatch(AMT, token)
                      and len(token) <= 20)
            if not is_cat:
                i += 1
                continue
            # 向后扫最多 4 行，收集 方法 / 年限 / 残值率
            meth = years = residual = None
            for j in range(i + 1, min(i + 5, len(lines))):
                tk = lines[j]
                if meth is None and any(m in tk for m in DEP_METHODS):
                    meth = next(m for m in DEP_METHODS if m in tk)
                    continue
                norm = tk.replace(" ", "")
                if years is None and YEARS_PAT.match(norm):
                    years = norm
                    continue
                if years is not None and residual is None and ("%" in tk or re.fullmatch(r"[\d.\-]+", norm)):
                    residual = norm
                    break
                if any(h in tk for h in DEP_CAT_HINT) and years is not None:
                    break
            if years:
                cats.append({"category": token, "method": meth or method_hint,
                             "years": years, "residual": residual or "未披露"})
                i += 1
            else:
                i += 1

        years_range = "、".join(dict.fromkeys(
            c["years"] for c in cats if c["years"] and c["years"] != "永久"))
        if cats or years_range:
            return {"categories": cats, "method": method_hint, "years_range": years_range,
                    "raw": re.sub(r"\n+", " / ", (hdr.string[hdr.start():hdr.start() + 320]
                                                  if hdr else seg[:320])),
                    "page": idx + 1}
    return {}


def parse_notes(pdf_path: str | Path) -> dict:
    """解析一份定期报告 PDF，返回结构化附注信息。"""
    pages = load_pages(pdf_path)
    return {
        "restricted_cash": extract_restricted_cash(pages),
        "ar_aging": extract_ar_aging(pages),
        "ar_provision": extract_ar_provision(pages),
        "depreciation": extract_depreciation(pages),
        "page_count": len(pages),
    }
