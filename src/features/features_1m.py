import pandas as pd
import numpy as np


def build_features_1m(panel: pd.DataFrame, rv_window: int = 60,
                      rv_min_periods: int = 60) -> pd.DataFrame:
    """Tạo features_1m từ panel_1m_wide.

    Đơn vị: ret_1m, rv_60m = log return/phút (không nhân bps);
    vol_share = tỷ lệ [0,1] trong cùng market_type; dispersion_bps_* = bps.
    Thời gian: tại t chỉ dùng dữ liệu có ts_available <= t (cửa sổ trượt quá khứ).
    Thiếu dữ liệu: giữ NaN, không nội suy/ffill.
    """
    venues = [c.split("__", 1)[1] for c in panel.columns if c.startswith("close__")]
    if not venues:
        raise ValueError("panel must contain close__{venue} columns")
    if rv_window < 1 or not 1 <= rv_min_periods <= rv_window:
        raise ValueError("require 1 <= rv_min_periods <= rv_window")

    out = {}
    for v in venues:
        ret = panel[f"log_close__{v}"].diff()
        out[f"ret_1m__{v}"] = ret
        out[f"rv_60m__{v}"] = ret.rolling(rv_window, min_periods=rv_min_periods).std()

    for mt in ("spot", "perp"):
        group_venues = [v for v in venues if v.endswith(f"_{mt}")]
        name = f"dispersion_bps_{mt}"

        if group_venues:
            volumes = panel[[f"volume_base__{v}" for v in group_venues]]
            total_volume = volumes.sum(axis=1, min_count=len(group_venues))
            total_volume = total_volume.where(total_volume > 0)

            for v in group_venues:
                out[f"vol_share__{v}"] = volumes[f"volume_base__{v}"] / total_volume

        if len(group_venues) < 2:
            out[name] = np.nan
            continue

        prices = panel[[f"close__{v}" for v in group_venues]]
        complete = prices.notna().all(axis=1)
        mean_price = prices.mean(axis=1)
        out[name] = ((prices.max(axis=1) - prices.min(axis=1)) / mean_price * 10_000).where(complete)

    features = pd.DataFrame(out, index=panel.index)
    features.attrs.update(panel.attrs)
    return features