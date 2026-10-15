"""数据获取层：从东方财富 F10 与巨潮资讯网抓取财务报表与定期报告。

设计原则：
- 每一类数据落到 data/ 目录下的 JSON/PDF，抓取与分析解耦，便于复现与离线调试；
- 带磁盘缓存，同一报告期不重复请求；
- 所有金额单位统一为「元」。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

EM_F10 = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"
EM_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"
CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_TOPSEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_STATIC = "http://static.cninfo.com.cn/"

# F10 接口需要先按公司类型选择科目模板；拿不到时按通用型重试
COMPANY_TYPES = [4, 3, 2, 1]

STATEMENTS = {
    "balance": "zcfzbAjaxNew",       # 资产负债表
    "income": "lrbAjaxNew",          # 利润表
    "cashflow": "xjllbAjaxNew",      # 现金流量表
}


def _http(url: str, data: bytes | None = None, headers: dict | None = None,
          timeout: int = 30, retries: int = 3) -> bytes:
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - 重试所有网络异常
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求失败 {url}: {last}")


def _http_json(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    raw = _http(url, data=data, headers=headers)
    return json.loads(raw.decode("utf-8", errors="replace"))


def normalize_code(code: str) -> tuple[str, str]:
    """返回 (6 位代码, 带市场前缀的代码如 SZ000807)。"""
    code = code.strip().upper()
    for pfx in ("SH", "SZ", "BJ"):
        if code.startswith(pfx):
            code = code[2:]
            break
    code = "".join(ch for ch in code if ch.isdigit()).zfill(6)
    if code.startswith(("60", "68", "90", "5")):
        market = "SH"
    elif code.startswith(("00", "30", "20", "15", "16")):
        market = "SZ"
    elif code.startswith(("43", "83", "87", "92")):
        market = "BJ"
    else:
        market = "SZ"
    return code, f"{market}{code}"


def em_secid(code: str) -> str:
    """东方财富行情接口的 secid。"""
    c, _ = normalize_code(code)
    if c.startswith(("60", "68", "90", "5", "11")):
        return f"1.{c}"
    return f"0.{c}"


@dataclass
class Period:
    """单个报告期的三大报表数据。"""

    report_date: str          # 如 2026-06-30
    report_name: str          # 如 2026中报
    balance: dict = field(default_factory=dict)
    income: dict = field(default_factory=dict)
    cashflow: dict = field(default_factory=dict)

    @property
    def is_annual(self) -> bool:
        return self.report_date.endswith("12-31")

    @property
    def months(self) -> int:
        """报告期覆盖的月份数，用于年化。"""
        return int(self.report_date[5:7])


class FinancialFetcher:
    """财报抓取器。"""

    def __init__(self, data_dir: str | Path = "data", use_cache: bool = True):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        self._company_type: dict[str, int] = {}
        self._org_ids: dict[str, str] = {}

    # ---------- 通用缓存 ----------
    def _cache_path(self, name: str) -> Path:
        return self.data_dir / name

    def _cached_json(self, path: Path, url: str) -> dict:
        if self.use_cache and path.exists() and path.stat().st_size > 2:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        payload = _http_json(url)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    # ---------- 公司类型探测 ----------
    def company_type(self, code: str, report_date: str = "2024-12-31") -> int:
        if code in self._company_type:
            return self._company_type[code]
        _, full = normalize_code(code)
        best, best_fields = 4, -1
        for ctype in COMPANY_TYPES:
            url = (f"{EM_F10}/zcfzbAjaxNew?companyType={ctype}&reportDateType=0"
                   f"&reportType=1&dates={report_date}&code={full}")
            try:
                payload = _http_json(url)
            except Exception:  # noqa: BLE001
                continue
            rows = payload.get("data") or []
            if rows:
                n = sum(1 for v in rows[0].values() if isinstance(v, (int, float)))
                if n > best_fields:
                    best, best_fields = ctype, n
        self._company_type[code] = best
        return best

    # ---------- 三大报表 ----------
    def _fetch_statement(self, code: str, kind: str, dates: list[str]) -> dict[str, dict]:
        _, full = normalize_code(code)
        endpoint = STATEMENTS[kind]
        ctype = self.company_type(code)
        joined = ",".join(dates)
        url = (f"{EM_F10}/{endpoint}?companyType={ctype}&reportDateType=0"
               f"&reportType=1&dates={joined}&code={full}")
        fname = f"{full}_{kind}_{dates[0]}_{len(dates)}.json"
        payload = self._cached_json(self._cache_path(fname), url)
        out: dict[str, dict] = {}
        for row in payload.get("data") or []:
            rd = str(row.get("REPORT_DATE", ""))[:10]
            if rd:
                out[rd] = row
        return out

    def periods(self, code: str, report_dates: list[str]) -> list[Period]:
        """抓取指定报告期的三大报表，按时间倒序返回。"""
        bal = self._fetch_statement(code, "balance", report_dates)
        inc = self._fetch_statement(code, "income", report_dates)
        cfs = self._fetch_statement(code, "cashflow", report_dates)
        result = []
        for rd in sorted(set(bal) | set(inc) | set(cfs), reverse=True):
            b = bal.get(rd, {})
            i = inc.get(rd, {})
            name = (b or i).get("REPORT_DATE_NAME") or rd
            result.append(Period(rd, name, b, i, cfs.get(rd, {})))
        return result

    # ---------- 行情与估值 ----------
    def quote(self, code: str) -> dict:
        fields = "f43,f57,f58,f116,f117,f162,f167,f168,f169,f170,f173,f184"
        url = f"{EM_QUOTE}?secid={em_secid(code)}&fields={fields}"
        _, full = normalize_code(code)
        payload = self._cached_json(self._cache_path(f"{full}_quote.json"), url)
        return payload.get("data") or {}

    # ---------- 定期报告 PDF ----------
    def org_id(self, code: str) -> str | None:
        """解析巨潮资讯网的内部机构 ID（orgId）。

        orgId 无统一规律（中国铝业=9900002901、宁德时代=GD165627），
        必须通过巨潮搜索接口解析，不能按代码前缀拼接。
        """
        c, _ = normalize_code(code)
        if c in self._org_ids:
            return self._org_ids[c]
        body = urllib.parse.urlencode({"keyWord": c, "maxNum": 10}).encode()
        try:
            payload = _http_json(CNINFO_TOPSEARCH, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                          "Referer": "http://www.cninfo.com.cn/"})
        except Exception:  # noqa: BLE001
            return None
        for item in (payload if isinstance(payload, list) else []):
            if str(item.get("code")) == c:
                self._org_ids[c] = item["orgId"]
                return item["orgId"]
        return None

    def announcements(self, code: str, category: str = "category_bndbg_szsh",
                      start: str = "", end: str = "") -> list[dict]:
        c, _ = normalize_code(code)
        org = self.org_id(code) or ""
        column = "sse" if c.startswith(("60", "68", "90")) else "szse"
        body = urllib.parse.urlencode({
            "pageNum": 1, "pageSize": 30, "column": column, "tabName": "fulltext",
            "stock": f"{c},{org}", "searchkey": "", "secid": "", "plate": "",
            "category": category, "trade": "",
            "seDate": f"{start}~{end}" if start else "", "sortName": "", "sortType": "",
            "isHLtitle": "true",
        }).encode()
        url = f"{CNINFO_QUERY}?_={int(time.time() * 1000)}"
        payload = _http_json(url, data=body,
                             headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                      "Referer": "http://www.cninfo.com.cn/"})
        return payload.get("announcements") or []

    def download_report_pdf(self, code: str, year: int, period: str = "半年报") -> Path | None:
        """下载指定年份的定期报告 PDF，返回本地路径。period: 半年报 | 年报 | 三季报。"""
        c, full = normalize_code(code)
        category = "category_bndbg_szsh" if period != "年报" else "category_ndbg_szsh"
        anns = self.announcements(code, category, f"{year}-01-01", f"{year}-12-31")
        # 公告标题形如「2026年半年度报告」，与"半年报"并非子串关系，需按关键词组匹配
        title_kw = {"半年报": ("半年度报告", "中期报告"), "年报": ("年度报告", "年年度报告"),
                    "三季报": ("第三季度报告", "三季度报告")}.get(period, (period,))
        skip = ("摘要", "英文", "更正", "补充", "公告", "提示性", "问询", "说明")
        target = None
        for a in anns:
            title = re.sub(r"</?em>", "", a.get("announcementTitle") or "")
            if any(k in title for k in title_kw) and not any(k in title for k in skip):
                target = a
                break
        if not target:
            return None
        url = CNINFO_STATIC + target["adjunctUrl"]
        out = self.data_dir / f"{full}_{year}{period}.pdf"
        if self.use_cache and out.exists() and out.stat().st_size > 10_000:
            return out
        out.write_bytes(_http(url, headers={"Referer": "http://www.cninfo.com.cn/"}, timeout=90))
        return out

    def peer_metrics(self, codes: list[str], report_date: str) -> dict[str, dict]:
        """抓取同业公司单期核心报表数据，用于横向对照。"""
        out: dict[str, dict] = {}
        for c in codes:
            try:
                ps = self.periods(c, [report_date])
            except Exception:  # noqa: BLE001
                continue
            if ps:
                p = ps[0]
                out[c] = {"name": p.balance.get("SECURITY_NAME_ABBR")
                          or p.income.get("SECURITY_NAME_ABBR") or c,
                          "report_date": p.report_date,
                          "balance": p.balance, "income": p.income, "cashflow": p.cashflow}
        return out


def recent_report_dates(anchor: str, n: int, same_quarter_only: bool = True) -> list[str]:
    """由锚定报告期倒推 n 个报告期。

    same_quarter_only=True 时返回同一季度的历年期数（如全部 06-30），
    可避免季节性错配——半年报对照历年半年报。
    """
    y, m, d = int(anchor[:4]), int(anchor[5:7]), int(anchor[8:10])
    out = []
    for k in range(n):
        out.append(f"{y - k:04d}-{m:02d}-{d:02d}")
    return out
