"""Streamlit entry point. Run: streamlit run app.py."""
import json
import re
import tempfile
import threading
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="年报分析工作台", page_icon="📊", layout="wide")


@st.cache_resource
def analysis_lock():
    # The PDF parser has a process-global page cache: serialize all analyses.
    return threading.Lock()


def show_report(md, result, key):
    st.caption(f"{result['code']} · {result['name']} · 实际报告期 {result['report_date']}")
    cols = st.columns(2)
    cols[0].download_button("下载 Markdown 报告", md, f"{result['code']}_report.md", key=key + "md")
    cols[1].download_button("下载 JSON 数据", json.dumps(result, ensure_ascii=False, indent=2),
                            f"{result['code']}_analysis.json", mime="application/json", key=key + "json")
    st.markdown(md)


st.title("年报分析工作台")
st.write("从资产结构、经营效率和附注证据，理解一家公司的财务状况。")
st.caption("数据来源：东方财富、巨潮资讯网。分析结果不构成投资建议；金融企业指标需另行核验。")
online, examples = st.tabs(["生成分析", "已有报告"])

with online:
    with st.form("analysis"):
        code = st.text_input("A 股股票代码", "000807", max_chars=6)
        chosen_date = st.date_input("报告期", date(2026, 6, 30))
        peers_text = st.text_input("同业代码（可选，最多 3 个，逗号分隔）")
        history = st.slider("历年同期数量", 2, 8, 5)
        with_pdf = st.checkbox("解析报告 PDF 附注（耗时更长）", value=False)
        submitted = st.form_submit_button("开始分析", type="primary")
    st.info("请选择已经披露的季度末报告期。首次取数可能耗时数分钟；云端临时文件不作为永久存储，请下载结果。")
    if submitted:
        peers = [p.strip() for p in re.split(r"[,，]", peers_text) if p.strip()]
        if not re.fullmatch(r"[0-9]{6}", code) or any(not re.fullmatch(r"[0-9]{6}", p) for p in peers):
            st.error("股票代码必须是 6 位数字。")
        elif len(peers) > 3:
            st.error("同业公司最多 3 家。")
        elif chosen_date.strftime("%m-%d") not in {"03-31", "06-30", "09-30", "12-31"} or chosen_date > date.today():
            st.error("请选择不晚于今天的季度末日期。")
        else:
            lock = analysis_lock()
            if not lock.acquire(blocking=False):
                st.warning("当前有分析任务正在运行，请稍后重试。")
            else:
                try:
                    with st.spinner("正在获取财报、计算指标并生成报告……"):
                        import analyze
                        # Isolate every request's files; never display another user's output.
                        with tempfile.TemporaryDirectory(prefix="annual-report-") as folder:
                            analyze.DATA_DIR = Path(folder) / "data"
                            analyze.OUT_DIR = Path(folder) / "reports"
                            output = analyze.run(code, chosen_date.isoformat(), peers, with_pdf, history, quiet=True)
                            result_path = next(analyze.OUT_DIR.glob("*_analysis.json"))
                            result = json.loads(result_path.read_text(encoding="utf-8"))
                            if result["report_date"] != chosen_date.isoformat():
                                st.error("所选报告期数据不完整，请选择其他已披露报告期。")
                            else:
                                st.session_state["report"] = (output.read_text(encoding="utf-8"), result)
                except Exception:
                    st.error("分析未完成：数据源可能暂时不可访问，或该报告期尚未披露。请稍后重试，也可查看已有报告。")
                finally:
                    lock.release()
    if "report" in st.session_state:
        show_report(*st.session_state["report"], "generated")

with examples:
    files = sorted((ROOT / "examples").glob("*_analysis.json"))
    if files:
        selected = st.selectbox("选择报告", files, format_func=lambda p: p.name.replace("_analysis.json", ""))
        result = json.loads(selected.read_text(encoding="utf-8"))
        md_file = next((ROOT / "examples").glob(f"{result['code']}_*.md"), None)
        st.warning("以下为原项目中已生成的历史结果，未在本次部署中重新核验，不代表实时数据。")
        if md_file:
            show_report(md_file.read_text(encoding="utf-8"), result, "example")
