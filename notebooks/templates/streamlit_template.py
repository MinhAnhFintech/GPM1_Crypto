"""TV2's dashboard starter. Run: python -m streamlit run this_file.py."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import streamlit as st
from src.utils.io_utils import read_parquet
from src.utils.schema import data_kind, venues_from_panel


def main():
    """Seven research views; unavailable upstream estimates stay unavailable."""
    st.set_page_config(page_title="Crypto research", layout="wide")
    st.title("Crypto price discovery")
    st.caption("Mô phỏng nghiên cứu, không khuyến nghị giao dịch.")
    source = st.sidebar.text_input("Panel Parquet", str(ROOT/"data/fixtures/multi/panel_1m_wide.parquet"))
    try:
        panel = read_parquet(source, "D3")
    except (OSError, ValueError) as exc:
        st.error(f"Không đọc được panel: {exc}")
        return
    kind = data_kind(panel)
    st.info(f"data_kind = {kind}")
    venues = venues_from_panel(panel)
    last = panel.iloc[-1]
    st.subheader("1. Báo giá theo venue")
    st.dataframe(pd.DataFrame([{"venue": v, "market_type": v.rsplit("_", 1)[1], "close_usdt": last[f"close__{v}"], "missing": last[f"missing__{v}"]} for v in venues]))
    st.subheader("2. Độ phân tán giá")
    for market in ("spot", "perp"):
        columns = [f"close__{v}" for v in venues if v.endswith("_"+market)]
        if len(columns) >= 2:
            prices = panel[columns]
            dispersion = (prices.max(axis=1)-prices.min(axis=1))/prices.mean(axis=1)*1e4
            dispersion = dispersion.where(prices.notna().all(axis=1))
            st.line_chart(dispersion.rename(f"dispersion_bps_{market}"))
    st.subheader("3. Venue dẫn hiện tại")
    st.write("Chờ rolling_is từ TV5; chỉ công nhận leader khi bounds không chồng nhau.")
    st.subheader("4. Xếp hạng rolling IS")
    st.write("Chờ D6 (IS, CS và ILS).")
    st.subheader("5. Gap gross (bps)")
    st.write("Chờ gap_events; chỉ ghép cùng market_type.")
    st.subheader("6. Gap net ước tính (bps)")
    st.write("Chờ phí và mô hình chi phí của TV6.")
    st.subheader("7. Cờ arbitrage")
    st.write("Chờ kết quả TV6; không suy ra cơ hội từ dữ liệu còn thiếu.")


if __name__ == "__main__":
    main()
