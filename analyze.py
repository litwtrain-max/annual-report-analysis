"""年报自动化分析流程 —— 主入口。

用法：
    python analyze.py 000807                  # 自动取最新报告期
    python analyze.py 000807 --date 2026-06-30
    python analyze.py 000807 --peers 600219,601600,000933
    python analyze.py 000807 --no-pdf         # 跳过 PDF 附注解析

流程（对应《年报分析要点》阶段 1–6）：
    ① 取数  → ② 结构 → ③ 效率 → ④ 猫腻扫描 → ⑤ 同业对照 → ⑥ 出报告
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from fetch import FinancialFetcher, normalize_code, recent_report_dates
from metrics import (Analysis, CORE_OPERATING_KEYS, INTEREST_BEARING_KEYS,
                     efficiency, risk_scan, structure, num, safe_div)
from notes import parse_notes
from report import render

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "reports"

# 报告期锚点：自动探测时尝试的顺序（最新在前）
PERIOD_ANCHORS = [(2026, 6, 30), (2025, 12, 31), (2025, 9, 30), (2025, 6, 30),
                  (2024, 12, 31), (2024, 9, 30), (2024, 6, 30)]


def detect_latest_period(fetcher: FinancialFetcher, code: str) -> str:
    """探测该公司最新可得报告期。"""
    _, full = normalize_code(code)
    for y, m, d in PERIOD_ANCHORS:
        rd = f"{y:04d}-{m:02d}-{d:02d}"
        try:
            ps = fetcher.periods(code, [rd])
        except Exception:  # noqa: BLE001
            continue
        if ps and ps[0].balance.get("TOTAL_ASSETS"):
            return rd
    raise RuntimeError(f"无法获取 {code} 的任何报告期数据")


def build_peer_row(m: dict) -> dict:
    """把同业公司单期数据压成对照行。m 为 peer_metrics 返回的 {name, balance, income, cashflow}。"""
    b, i = m["balance"], m["income"]
    ta = num(b, "TOTAL_ASSETS")
    tl = num(b, "TOTAL_LIABILITIES")
    ib = sum(num(b, k) for k in INTEREST_BEARING_KEYS)
    core = sum(num(b, k) for k in CORE_OPERATING_KEYS)
    months = int(m["report_date"][5:7]) if m.get("report_date") else 12
    ann = 12 / months if months else 1
    rev = num(i, "OPERATE_INCOME")
    return {
        "name": m.get("name") or "—",
        "total_assets": ta,
        "debt_ratio": (tl / ta * 100) if ta else None,
        "ib_pct_liab": (ib / tl * 100) if tl else None,
        "turnover": safe_div(rev * ann, ta),
        "core_pct": (core / ta * 100) if ta else None,
    }


def run(code: str, report_date: str | None = None, peers: list[str] | None = None,
        with_pdf: bool = True, history_n: int = 5, quiet: bool = False) -> Path:
    log = (lambda *a: None) if quiet else (lambda *a: print(*a, file=sys.stderr))
    fetcher = FinancialFetcher(DATA_DIR)

    c, full = normalize_code(code)
    if not report_date:
        report_date = detect_latest_period(fetcher, c)
    log(f"[1/6] 取数：{full} 报告期 {report_date}，回溯 {history_n} 期")

    dates = recent_report_dates(report_date, history_n)
    hist = fetcher.periods(c, dates)
    if not hist:
        raise RuntimeError(f"未获取到 {c} 于 {report_date} 的财务数据")
    period = next((p for p in hist if p.report_date == report_date), None)
    if period is None or not all((period.balance, period.income, period.cashflow)):
        raise RuntimeError(f"{c} 于 {report_date} 的三大报表不完整")
    name = (period.balance.get("SECURITY_NAME_ABBR")
            or period.income.get("SECURITY_NAME_ABBR") or c)
    log(f"      {name}，取得 {len(hist)} 期数据")

    a = Analysis(c, name, period, hist)

    log("[2/6] 结构分析")
    a.structure = structure(period, hist)

    log("[3/6] 效率分析")
    a.efficiency = efficiency(period, hist)

    log("[4/6] 猫腻扫描")
    notes: dict = {}
    if with_pdf and report_date[5:7] in {"06", "12"}:
        try:
            year = int(report_date[:4])
            plabel = "半年报" if report_date[5:7] == "06" else (
                "年报" if report_date[5:7] == "12" else "三季报")
            pdf = fetcher.download_report_pdf(c, year, plabel)
            if pdf:
                log(f"      附注解析：{pdf.name}")
                notes = parse_notes(pdf)
                a.notes_source = str(pdf)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log(f"      附注解析失败（继续）：{exc}")
    a.notes = notes
    a.findings = risk_scan(period, hist, a.structure, a.efficiency, notes)

    log("[5/6] 同业对照")
    if peers:
        rows = fetcher.peer_metrics(peers, report_date)
        a.peers = {pc: build_peer_row(pp) for pc, pp in rows.items()}

    try:
        a.quote = fetcher.quote(c)
    except Exception:  # noqa: BLE001
        pass

    log("[6/6] 生成报告")
    md = render(a, notes)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = report_date.replace("-", "")
    out = OUT_DIR / f"{c}_{name}_{tag}_资产负债表分析.md"
    out.write_text(md, encoding="utf-8")

    # 落一份 JSON，便于二次开发与回归比对
    dump = {
        "code": c, "name": name, "report_date": report_date,
        "structure": a.structure, "efficiency": a.efficiency,
        "findings": [asdict(f) for f in a.findings],
        "notes": notes, "peers": a.peers, "quote": a.quote,
        "history": [{"report_date": p.report_date, "report_name": p.report_name,
                     "total_assets": num(p.balance, "TOTAL_ASSETS"),
                     "revenue": num(p.income, "OPERATE_INCOME"),
                     "netprofit": num(p.income, "NETPROFIT")} for p in hist],
    }
    (OUT_DIR / f"{c}_{tag}_analysis.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    log(f"完成 → {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="年报自动化分析流程")
    ap.add_argument("code", help="股票代码，如 000807")
    ap.add_argument("--date", default=None, help="报告期，如 2026-06-30；默认自动探测最新")
    ap.add_argument("--peers", default=None, help="同业代码，逗号分隔")
    ap.add_argument("--no-pdf", action="store_true", help="跳过 PDF 附注解析")
    ap.add_argument("--history", type=int, default=5, help="回溯期数，默认 5")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    peers = [s.strip() for s in args.peers.split(",") if s.strip()] if args.peers else None
    out = run(args.code, args.date, peers, not args.no_pdf, args.history, args.quiet)
    if not args.quiet:
        print(f"\n报告已生成：{out}")
        print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
