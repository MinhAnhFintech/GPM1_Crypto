"""C3.09 (BẮT BUỘC) — Funding rate + Open interest của hợp đồng perpetual (BTC/USDT:USDT) qua CCXT, phục vụ RQ3 (spot hay perp dẫn giá), bảng T1 và C5.09.


Funding và OI là BIẾN MÔ TẢ. KHÔNG đưa funding vào net_gap_bps (cash-and-carry nằm ngoài phạm vi bài).


Funding chỉ chốt mỗi kỳ (thường 8 giờ: 00:00, 08:00, 16:00 UTC) nên KHÔNG cần thu mỗi 5 giây: tải LỊCH SỬ các kỳ đã chốt bằng
fetch_funding_rate_history. Open interest tải bằng fetch_open_interest_history (mặc định nến 1 giờ, 29 ngày gần nhất vì Binance
chỉ giữ ~30 ngày). Mỗi venue perp ghi:


    data/raw/live/funding/{venue}_funding.parquet      (venue = binance_perp, okx_perp, bybit_perp)
    data/raw/live/funding/{venue}_oi.parquet
    data/raw/live/funding/funding_report.csv           (số kỳ, khoảng thiếu, khoảng thời gian, thống kê funding)
    data/raw/live/funding/oi_report.csv                (số nến, khoảng thiếu, median OI, giá ngầm định = USDT/BTC, unit_check)
    data/raw/live/funding/funding_snapshots.csv        (chỉ khi dùng --snapshot: funding tạm tính + OI hiện tại, mark/index price)
    data/processed/funding_oi.parquet                  (BẢNG GỘP: mỗi quan sát một dòng; --hourly cho lưới 1 giờ)


GHI FILE KHÔNG LÀM MẤT DỮ LIỆU ĐÃ TẢI (gộp, không ghi đè):
  * Mỗi lần chạy, kết quả mới được GỘP với file cũ theo mốc giờ (trùng thì lấy bản mới tải). Dòng cũ nằm ngoài cửa sổ vừa tải
    (vd OI cũ hơn 29 ngày mà sàn không còn trả) được GIỮ NGUYÊN: đây là bộ đệm (buffer) tích lũy của OI.
  * Lần tải thiếu (cửa sổ lỗi, sàn trả ít hơn) không làm file ngắn đi: phần thiếu vẫn là dòng cũ.
  * File cũ không đọc được hoặc sai đơn vị OI không bị xóa: được sao lưu sang *.corrupt-* / *.stale-units* rồi mới ghi lại.
  * Ghi nguyên tử (file tạm rồi đổi tên).


ĐƠN VỊ OI (open_interest_base luôn là BTC):
  * Hệ số quy đổi theo thứ tự: --oi-mult  >  DEFAULT_OI_MULT (okx_perp = 0.01: 1 hợp đồng = 0.01 BTC)  >  không nhân.
    Chạy MẶC ĐỊNH (không cần --oi-mult) vì vậy đã quy đổi OKX về BTC.
  * Cổng kiểm tra đơn vị trước khi ghi: giá ngầm định = open_interest_usdt / open_interest_base phải xấp xỉ giá BTC (so với giá
    ticker hiện tại, sai lệch tối đa 3 lần; không lấy được ticker thì dùng khoảng UNIT_BAND_FALLBACK). KHÔNG đạt -> không ghi file,
    in lỗi, thoát mã 1. Sàn có contractSize khác 1 mà chưa có hệ số quy đổi -> cũng từ chối. Sàn không trả USDT thì không kiểm được:
    unit_check = "unverified" trong oi_report.csv.
  * Dựng funding_oi.parquet cũng kiểm lại file OI trên đĩa: file sai đơn vị bị loại khỏi bảng gộp (không đưa số hợp đồng vào cột BTC).


Cột file funding: ts_funding_utc, ts_available, exchange, market_type, symbol, funding_rate, funding_rate_bps, funding_interval_h,
          data_kind, in_live_window, source.
  * ts_funding_utc = giờ chốt kỳ (làm tròn giây). ts_available = ts_funding_utc: funding của một kỳ chỉ BIẾT tại giờ chốt kỳ đó,
    nên mọi phép ghép/hồi quy phải dùng dữ liệu có ts_available <= t (không look-ahead).
  * funding_rate = tỷ lệ THEO KỲ ở dạng số thập phân (0.0001 = 1 bps cho một kỳ); funding_rate_bps = funding_rate * 1e4.
  * funding_interval_h = khoảng cách giờ tới kỳ trước (phát hiện sàn đổi 8h -> 4h). Kỳ đầu tiên để trống.
  * data_kind = "historical" cho TOÀN BỘ file (dữ liệu lấy qua API lịch sử, một nhãn nguồn cho cả file). Thông tin giai đoạn live giữ ở
    cột in_live_window (True nếu mốc >= ngày bắt đầu thu live trong live_sessions.yaml: start_date).
  * Không nội suy, không lấp kỳ thiếu: kỳ thiếu được đếm trong funding_report.csv.


Cột file OI: ts_oi_utc, ts_available, exchange, market_type, symbol, timeframe, open_interest_base (BTC), open_interest_usdt,
          data_kind, in_live_window, source.
  * ts_oi_utc = mốc sàn gán cho điểm OI; để KHÔNG look-ahead (sàn không nói rõ mốc là đầu hay cuối nến) lấy bảo thủ
    ts_available = ts_oi_utc + độ dài nến.


BẢNG GỘP data/processed/funding_oi.parquet — MẶC ĐỊNH MỘT DÒNG MỖI QUAN SÁT, KHÔNG LẶP, KHÔNG LẤP:
  * dòng funding nằm tại giờ chốt kỳ (funding_* có giá trị, OI trống nếu không trùng giờ nến OI);
  * dòng OI nằm tại ts_available của nến (OI có giá trị, funding_* trống); nếu trùng giờ thì gộp một dòng;
  * ts_utc = ts_available. Muốn đếm số kỳ funding: dòng có funding_rate không trống (= số dòng của file *_funding).
  * Muốn lưới 1 giờ để ghép chuỗi giờ: chạy với --hourly (khi đó funding bị chép lại 8 hàng/kỳ), cấu trúc như dưới:
  Lưới 1 giờ (--hourly), mỗi hàng chỉ dùng thông tin có ts_available <= ts_utc:
    ts_utc, ts_available (= ts_utc), exchange, market_type, venue, symbol,
    funding_rate, funding_rate_bps, funding_interval_h, funding_ts_utc   (kỳ funding đã chốt GẦN NHẤT, không quá 2 kỳ),
    open_interest_base, open_interest_usdt, oi_ts_utc                     (nến OI đã khép GẦN NHẤT, không quá 2 nến; ngoài đó để NaN),
    data_kind (= "historical"), in_live_window
  * Không nội suy, không lấp: ô nào không có thông tin hợp lệ thì để NaN.


Mặc định tải funding từ sample.start (config.yaml) đến hiện tại; chạy lại bao nhiêu lần cũng được (gộp với file cũ, kết quả không mất dòng).


KIỂM ĐẦU VÀO TRƯỚC KHI DỰNG funding_oi.parquet (không bao giờ ghi đè bảng đủ 3 sàn bằng bảng thiếu):
  * Mọi venue perp trong config.yaml phải có file funding (và file OI, trừ khi --no-oi). Thiếu bất kỳ venue nào -> KHÔNG ghi
    funding_oi.parquet, file cũ giữ nguyên, báo LỖI, mã thoát 1.
  * Kiểm nguồn từng file: chỉ nhận data_kind == "historical". Nhãn "live" do bản script cũ ghi cho dòng trong cửa sổ live
    (source == "ccxt", vẫn là API lịch sử) được chuẩn hóa về historical. Nhãn khác (synthetic, ...), thiếu cột data_kind hay
    source khác ccxt -> file bị từ chối, không ghi bảng gộp, báo LỖI, mã thoát 1. Không dòng nào được "đổi nhãn" thành historical.
  * Khi gộp với file cũ (merge_with_existing) cũng kiểm nguồn của file cũ: file cũ có dòng không phải historical bị sao lưu
    sang *.bad-source* và KHÔNG được gộp.

Mã thoát: 0 = ổn; 1 = có vấn đề (không tải được bản ghi nào, thiếu đầu vào, nguồn không hợp lệ, sai đơn vị OI, lỗi tải).


Dùng:
    python src\\collectors\\live\\fetch_funding.py --probe       # thử, KHÔNG ghi file: sàn trả được từ khi nào, bao nhiêu kỳ/nến
    python src\\collectors\\live\\fetch_funding.py               # tải funding + OI cả 3 sàn perp, rồi dựng funding_oi.parquet
    python src\\collectors\\live\\fetch_funding.py --venues okx_perp --since 2026-10-01
    python src\\collectors\\live\\fetch_funding.py --oi-mult okx_perp=0.01     # ghi đè hệ số mặc định (vd =1 nếu sàn đã trả theo BTC)
    python src\\collectors\\live\\fetch_funding.py --snapshot    # thêm 1 dòng funding + OI hiện tại mỗi venue
    python src\\collectors\\live\\fetch_funding.py --build-only  # chỉ dựng lại funding_oi.parquet từ các file đã tải
"""
from __future__ import annotations


import argparse
import random
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


import yaml


HERE = Path(__file__).resolve().parent
UTC = timezone.utc
H_MS = 3_600_000
TF_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}




def _find_project_root() -> Path:
    for p in [HERE, *HERE.parents]:
        if any((p / m).exists() for m in (".git", "config", "data", ".venv")):
            return p
    return Path.cwd()




ROOT = _find_project_root()
DEFAULT_OUT = ROOT / "data" / "raw" / "live" / "funding"
DEFAULT_PROCESSED = ROOT / "data" / "processed" / "funding_oi.parquet"
LIMITS = {"binance": 1000, "binanceusdm": 1000, "bybit": 200, "okx": 100}
DEFAULT_LIMIT = 100
OI_LIMITS = {"binance": 500, "binanceusdm": 500, "bybit": 200, "okx": 100}
OI_STEP_DAYS = 7           # cửa sổ OI 7 ngày (168 nến 1h); đầy limit thì tự chia đôi
WINDOW_DAYS = 30            # mỗi lần gọi một cửa sổ 30 ngày (khoảng 90 kỳ 8h); đầy limit thì tự chia đôi
# Hệ số quy đổi OI -> BTC mặc định cho sàn trả theo HỢP ĐỒNG (1 hợp đồng BTC-USDT-SWAP của OKX = 0.01 BTC). --oi-mult ghi đè.
DEFAULT_OI_MULT = {"okx_perp": 0.01}
UNIT_REF_RATIO = 3.0                      # giá ngầm định so với giá ticker: trong khoảng (1/3, 3) lần
UNIT_BAND_FALLBACK = (5_000.0, 1_000_000.0)   # khi không lấy được ticker; hợp đồng 0.01 BTC cho ra giá ~ giá/100, bị bắt
REPORT_COLS = ["venue", "n_rows", "first_utc", "last_utc", "requested_since_utc", "n_expected_est", "n_missing_est",
               "median_interval_h", "funding_bps_min", "funding_bps_median", "funding_bps_max", "n_windows",
               "n_failed_windows", "fetched_at_utc"]
OI_REPORT_COLS = ["venue", "n_rows", "first_utc", "last_utc", "timeframe", "n_expected_est", "n_missing_est", "oi_base_median",
                  "oi_usdt_median", "implied_price_median", "pct_usdt_missing", "oi_mult", "n_windows", "n_failed_windows",
                  "fetched_at_utc", "unit_check", "contract_size"]
PROCESSED_COLS = ["ts_utc", "ts_available", "exchange", "market_type", "venue", "symbol", "funding_rate", "funding_rate_bps",
                  "funding_interval_h", "funding_ts_utc", "open_interest_base", "open_interest_usdt", "oi_ts_utc", "data_kind",
                  "in_live_window"]




def tf_ms(tf: str) -> int:
    return int(tf[:-1]) * TF_MS[tf[-1].lower()]




def parse_mults(items) -> dict:
    out = {}
    for it in items or []:
        for part in it.split(","):
            if part.strip():
                k, _, x = part.partition("=")
                val = float(x)
                if not val > 0:
                    sys.exit(f"--oi-mult {k.strip()}={x}: hệ số phải > 0")
                out[k.strip()] = val
    return out




def resolve_oi_mult(venue: str, mults: dict | None):
    """--oi-mult > DEFAULT_OI_MULT > None (không nhân). Gọi ở MỌI nơi dùng hệ số (kể cả snapshot) để mặc định luôn an toàn."""
    if mults and venue in mults:
        return mults[venue]
    return DEFAULT_OI_MULT.get(venue)




def _problem(a, msg: str):
    """Ghi nhận lỗi để main thoát mã 1 (CI/pipeline nhìn thấy), ngoài việc in ra màn hình."""
    print("    LỖI: " + msg, flush=True)
    p = getattr(a, "problems", None)
    if p is not None:
        p.append(msg)


def check_source(df, label: str):
    """Kiểm nguồn của MỘT file funding/OI trước khi dùng. Trả (df | None, lý do | None).
    Chỉ nhận data_kind == "historical" (một nhãn nguồn cho cả file). Ngoại lệ duy nhất: nhãn "live" do bản script cũ gán cho dòng
    trong cửa sổ live, khi cột source == "ccxt" (nguồn thật vẫn là API lịch sử) -> chuẩn hóa về historical.
    Thiếu cột/ô trống data_kind, nhãn khác (synthetic, ...) hoặc source khác ccxt -> từ chối (None)."""
    if df is None or not len(df):
        return df, None
    if "data_kind" not in df.columns or df["data_kind"].isna().any():
        return None, f"{label}: thiếu nhãn data_kind (không xác nhận được nguồn)"
    kinds = set(df["data_kind"].astype(str).str.strip().str.lower())
    if kinds <= {"historical"}:
        return df, None
    srcs = set(df["source"].astype(str).str.strip().str.lower()) if "source" in df.columns else set()
    if kinds <= {"historical", "live"} and srcs == {"ccxt"}:
        df = df.copy()
        df["data_kind"] = "historical"
        print(f"[{label}] LƯU Ý: nhãn data_kind='live' của bản cũ (source=ccxt) được chuẩn hóa về 'historical'")
        return df, None
    return None, f"{label}: nguồn không hợp lệ, data_kind={sorted(kinds)} (chỉ nhận 'historical')"




# ----------------------------------------------------------------------------- cấu hình
def parse_ts(s) -> datetime:
    if isinstance(s, datetime):
        dt = s
    elif not isinstance(s, str):                      # datetime.date từ yaml
        return datetime(s.year, s.month, s.day, tzinfo=UTC)
    else:
        s = s.strip()
        if len(s) <= 10:                              # chỉ có ngày
            return datetime.fromisoformat(s).replace(tzinfo=UTC)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)




def load_cfg(config_dir: Path):
    cfg = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
    exch = yaml.safe_load((config_dir / "exchanges.yaml").read_text(encoding="utf-8"))
    by_id = {f"{v['exchange']}_{v.get('market_type', 'spot')}": v for v in exch["venues"]}
    venues = []
    for vid in cfg["venues"]:
        v = by_id.get(vid)
        if v is None:
            sys.exit(f"venue '{vid}' có trong config.yaml nhưng không có trong exchanges.yaml")
        if v.get("market_type", "spot") != "perp":
            continue                                  # funding chỉ có ở perp
        venues.append({"venue": vid, "exchange": v["exchange"], "market_type": "perp",
                       "ccxt_id": v.get("ccxt_id", v["exchange"]), "symbol": v["symbol"]})
    return cfg, venues




def live_start(config_dir: Path) -> datetime:
    """Ngày bắt đầu thu live (live_sessions.yaml: start_date 00:00 giờ VN); không có file thì 2026-10-17 00:00 +07."""
    p = config_dir / "live_sessions.yaml"
    try:
        c = yaml.safe_load(p.read_text(encoding="utf-8"))
        d = c["start_date"]
        d = datetime.fromisoformat(d) if isinstance(d, str) else datetime(d.year, d.month, d.day)
        return d.replace(tzinfo=timezone(timedelta(hours=7))).astimezone(UTC)
    except Exception:  # noqa: BLE001
        return datetime(2026, 10, 16, 17, 0, tzinfo=UTC)




# ----------------------------------------------------------------------------- sàn + retry
def make_exchange(ccxt_id: str):
    import ccxt
    return getattr(ccxt, ccxt_id)({"enableRateLimit": True, "timeout": 15000})




def is_transient(e: Exception) -> bool:
    n, msg = type(e).__name__.lower(), str(e).lower()
    return (isinstance(e, (TimeoutError, ConnectionError))
            or any(k in n for k in ("timeout", "network", "notavailable", "unavailable", "ddos", "ratelimit", "connection"))
            or "too many" in msg or "429" in msg or "503" in msg or "502" in msg)




def with_retry(fn, tries: int = 7, label: str = ""):
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if not is_transient(e) or k == tries - 1:
                raise
            wait = min(60.0, 2.0 ** k) * (0.8 + 0.4 * random.random())
            print(f"    [{label}] lỗi tạm ({type(e).__name__}), thử lại sau {wait:.0f}s ({k + 1}/{tries - 1})", flush=True)
            time.sleep(wait)




def fetch_window(ex, symbol, s_ms, e_ms, limit):
    """Các kỳ chốt trong [s, e): gửi cả since và until để sàn trả đúng cửa sổ (Binance endTime, OKX after, Bybit endTime)."""
    rows = ex.fetch_funding_rate_history(symbol, s_ms, limit, {"until": e_ms})
    keep = {}
    for r in rows:
        t = r.get("timestamp")
        if t is not None and s_ms <= int(t) < e_ms and r.get("fundingRate") is not None:
            keep[int(t)] = float(r["fundingRate"])
    return keep




def fetch_window_oi(ex, symbol, s_ms, e_ms, limit, tf="1h"):
    """Các nến OI có mốc trong [s, e): {ts_ms: (open_interest_base, open_interest_usdt)}."""
    rows = ex.fetch_open_interest_history(symbol, tf, s_ms, limit, {"until": e_ms})
    keep = {}
    for r in rows:
        t, amt, val = r.get("timestamp"), r.get("openInterestAmount"), r.get("openInterestValue")
        if t is not None and s_ms <= int(t) < e_ms and (amt is not None or val is not None):
            keep[int(t)] = (None if amt is None else float(amt), None if val is None else float(val))
    return keep




def fetch_span(ex, symbol, s_ms, e_ms, limit, label, stats, depth=0, win=None, soft=False):
    """Một cửa sổ; nếu sàn trả đủ `limit` bản ghi (có thể bị cắt bớt) thì chia đôi cửa sổ rồi gọi lại.
    soft=True (dùng cho OI): lỗi nào của một cửa sổ cũng chỉ bỏ cửa sổ đó (vd Binance từ chối mốc quá cũ)."""
    win = win or fetch_window
    stats["windows"] += 1
    try:
        got = with_retry(lambda: win(ex, symbol, s_ms, e_ms, limit), label=label)
    except Exception as e:  # noqa: BLE001
        if not soft and not is_transient(e):
            raise
        stats["failed"] += 1
        print(f"    [{label}] BỎ cửa sổ {datetime.fromtimestamp(s_ms/1000, UTC):%Y-%m-%d} -> "
              f"{datetime.fromtimestamp(e_ms/1000, UTC):%Y-%m-%d}: {e}", flush=True)
        return {}
    if len(got) >= limit and (e_ms - s_ms) > 12 * H_MS and depth < 12:
        mid = (s_ms + e_ms) // 2
        got.update(fetch_span(ex, symbol, s_ms, mid, limit, label, stats, depth + 1, win, soft))
        got.update(fetch_span(ex, symbol, mid, e_ms, limit, label, stats, depth + 1, win, soft))
    return got




def fetch_all(ex, symbol, s_ms, e_ms, limit, label, stats, win=None, soft=False, step_days=WINDOW_DAYS):
    out, cur = {}, s_ms
    step = step_days * 24 * H_MS
    while cur < e_ms:
        nxt = min(cur + step, e_ms)
        out.update(fetch_span(ex, symbol, cur, nxt, limit, label, stats, 0, win, soft))
        cur = nxt
    return out




# ----------------------------------------------------------------------------- bảng + báo cáo
def to_frame(rec: dict, v: dict, live0: datetime):
    import pandas as pd
    df = pd.DataFrame(sorted(rec.items()), columns=["ts", "funding_rate"])
    df["ts_funding_utc"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.round("s")
    df = df.drop_duplicates("ts_funding_utc", keep="last").reset_index(drop=True)
    df["ts_available"] = df["ts_funding_utc"]
    df["funding_rate"] = df["funding_rate"].astype("float64")
    df["funding_rate_bps"] = df["funding_rate"] * 1e4
    df["funding_interval_h"] = df["ts_funding_utc"].diff().dt.total_seconds() / 3600.0
    df["data_kind"] = "historical"                              # lấy qua API lịch sử: một nhãn nguồn cho cả file
    df["in_live_window"] = df["ts_funding_utc"] >= pd.Timestamp(live0)   # giữ thông tin giai đoạn live
    df["exchange"], df["market_type"], df["symbol"], df["source"] = v["exchange"], "perp", "BTC/USDT", "ccxt"
    for c in ("ts_funding_utc", "ts_available"):
        df[c] = df[c].astype("datetime64[ns, UTC]")
    return df[["ts_funding_utc", "ts_available", "exchange", "market_type", "symbol", "funding_rate", "funding_rate_bps",
               "funding_interval_h", "data_kind", "in_live_window", "source"]]




def to_frame_oi(rec: dict, v: dict, tf: str, live0: datetime, mult: float | None):
    import pandas as pd
    df = pd.DataFrame([(t, a, b) for t, (a, b) in sorted(rec.items())], columns=["ts", "open_interest_base", "open_interest_usdt"])
    df["ts_oi_utc"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.round("s")
    df = df.drop_duplicates("ts_oi_utc", keep="last").reset_index(drop=True)
    df["ts_oi_utc"] = df["ts_oi_utc"].astype("datetime64[ns, UTC]")
    df["ts_available"] = (df["ts_oi_utc"] + pd.Timedelta(milliseconds=tf_ms(tf))).astype("datetime64[ns, UTC]")
    for c in ("open_interest_base", "open_interest_usdt"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    if mult is not None:                                        # quy đổi hợp đồng -> BTC (hệ số đã qua resolve_oi_mult)
        df["open_interest_base"] = df["open_interest_base"] * mult
    df["timeframe"] = tf
    df["data_kind"] = "historical"                              # lấy qua API lịch sử: một nhãn nguồn cho cả file
    df["in_live_window"] = df["ts_oi_utc"] >= pd.Timestamp(live0)        # giữ thông tin giai đoạn live
    df["exchange"], df["market_type"], df["symbol"], df["source"] = v["exchange"], "perp", "BTC/USDT", "ccxt"
    return df[["ts_oi_utc", "ts_available", "exchange", "market_type", "symbol", "timeframe", "open_interest_base",
               "open_interest_usdt", "data_kind", "in_live_window", "source"]]




def oi_unit_check(df, ref_price: float | None = None):
    """Soát đơn vị open_interest_base = BTC bằng giá ngầm định (USDT / BTC).
    Trả (trạng thái, giá ngầm định trung vị): "ok" | "fail" | "unverified" (sàn không trả USDT nên không kiểm được).
    Có giá tham chiếu (ticker): đạt nếu giá ngầm định nằm trong (1/UNIT_REF_RATIO, UNIT_REF_RATIO) lần giá đó;
    không có: đạt nếu nằm trong UNIT_BAND_FALLBACK."""
    ok = df["open_interest_base"].gt(0) & df["open_interest_usdt"].gt(0)
    if not ok.any():
        return "unverified", float("nan")
    implied = float((df.loc[ok, "open_interest_usdt"] / df.loc[ok, "open_interest_base"]).median())
    if ref_price and ref_price > 0:
        good = (1.0 / UNIT_REF_RATIO) < implied / ref_price < UNIT_REF_RATIO
    else:
        good = UNIT_BAND_FALLBACK[0] < implied < UNIT_BAND_FALLBACK[1]
    return ("ok" if good else "fail"), implied




def make_oi_report(df, v, tf, since_ms, until_ms, stats, mult) -> dict:
    step = tf_ms(tf)
    gaps = df["ts_oi_utc"].diff().dropna().dt.total_seconds() * 1000
    missing = int(sum(round(g / step) - 1 for g in gaps if g > 1.5 * step))
    ok = df["open_interest_base"].gt(0) & df["open_interest_usdt"].gt(0)
    implied = (df.loc[ok, "open_interest_usdt"] / df.loc[ok, "open_interest_base"]).median() if ok.any() else float("nan")
    return {"venue": v["venue"], "n_rows": len(df), "first_utc": df["ts_oi_utc"].min(), "last_utc": df["ts_oi_utc"].max(),
            "timeframe": tf, "n_expected_est": int((until_ms - since_ms) / step), "n_missing_est": missing,
            "oi_base_median": float(df["open_interest_base"].median()), "oi_usdt_median": float(df["open_interest_usdt"].median()),
            "implied_price_median": float(implied), "pct_usdt_missing": float(df["open_interest_usdt"].isna().mean() * 100),
            "oi_mult": mult if mult is not None else 1.0, "n_windows": stats["windows"], "n_failed_windows": stats["failed"],
            "fetched_at_utc": datetime.now(UTC).isoformat(timespec="seconds")}




def make_report(df, v, since_ms, until_ms, stats) -> dict:
    iv = float(df["funding_interval_h"].median()) if len(df) > 1 else float("nan")
    step_h = iv if iv == iv and iv > 0 else 8.0
    n_exp = int((until_ms - since_ms) / (step_h * H_MS)) if len(df) else 0
    first, last = df["ts_funding_utc"].min(), df["ts_funding_utc"].max()
    # số kỳ thiếu ước tính theo khoảng cách giữa các kỳ liên tiếp (> 1,5 lần chu kỳ)
    gaps = df["funding_interval_h"].dropna()
    missing = int(sum(round(g / step_h) - 1 for g in gaps if g > 1.5 * step_h))
    return {"venue": v["venue"], "n_rows": len(df), "first_utc": first, "last_utc": last,
            "requested_since_utc": datetime.fromtimestamp(since_ms / 1000, UTC).isoformat(timespec="seconds"),
            "n_expected_est": n_exp, "n_missing_est": missing, "median_interval_h": iv,
            "funding_bps_min": float(df["funding_rate_bps"].min()), "funding_bps_median": float(df["funding_rate_bps"].median()),
            "funding_bps_max": float(df["funding_rate_bps"].max()), "n_windows": stats["windows"],
            "n_failed_windows": stats["failed"], "fetched_at_utc": datetime.now(UTC).isoformat(timespec="seconds")}




def write_frame(df, path: Path, fmt: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    (df.to_parquet(tmp, index=False) if fmt == "parquet" else df.to_csv(tmp, index=False))
    tmp.replace(path)




def save_report(out_dir: Path, rows, name="funding_report.csv", cols=None):
    import pandas as pd
    path = out_dir / name
    new = pd.DataFrame(rows, columns=cols or REPORT_COLS)
    if path.exists():
        old = pd.read_csv(path)
        new = pd.concat([old[~old["venue"].isin(new["venue"])], new], ignore_index=True)
    new.sort_values("venue").to_csv(path, index=False)




def _backup(path: Path, tag: str) -> Path:
    """Sao lưu (không xóa, không ghi đè bản sao lưu cũ) trước khi thay file thật."""
    bak = path.with_name(f"{path.stem}.{tag}{path.suffix}")
    if bak.exists():
        bak = path.with_name(f"{path.stem}.{tag}-{datetime.now(UTC):%Y%m%dT%H%M%S}{path.suffix}")
    shutil.copy2(path, bak)
    return bak




def merge_with_existing(new, path: Path, key: str, time_cols: list, live0: datetime, label: str, check_units: bool = False,
                        on_problem=None):
    """GỘP kết quả vừa tải với file cũ (cùng mốc giờ thì lấy bản mới). Không bao giờ làm mất dòng đã có:
    dòng cũ nằm ngoài cửa sổ vừa tải (vd OI cũ hơn 29 ngày) và các cửa sổ tải lỗi vẫn còn nguyên trong file."""
    import pandas as pd
    if not path.exists():
        return new
    try:
        old = _read(path, time_cols)
    except Exception as e:  # noqa: BLE001
        bak = _backup(path, "corrupt")
        print(f"[{label}] file cũ không đọc được ({type(e).__name__}); đã sao lưu {bak.name}, ghi lại từ dữ liệu mới")
        return new
    old, why = check_source(old, label)
    if why:                                                      # file cũ có nguồn không phải historical: không gộp
        bak = _backup(path, "bad-source")
        msg = f"{why}; đã sao lưu {bak.name}, KHÔNG gộp dòng cũ"
        print(f"[{label}] {msg}")
        if on_problem:
            on_problem(msg)
        return new
    if check_units and len(old) and oi_unit_check(old)[0] == "fail":
        bak = _backup(path, "stale-units")
        print(f"[{label}] file OI cũ sai đơn vị (số hợp đồng trong cột BTC?); đã sao lưu {bak.name}, KHÔNG gộp dòng cũ")
        return new
    old = old.reindex(columns=new.columns)
    merged = (pd.concat([old, new], ignore_index=True).drop_duplicates(key, keep="last")
              .sort_values(key).reset_index(drop=True))
    for c in time_cols:
        merged[c] = pd.to_datetime(merged[c], utc=True).astype("datetime64[ns, UTC]")
    merged["in_live_window"] = merged[key] >= pd.Timestamp(live0)
    if key == "ts_funding_utc":                                  # khoảng cách kỳ tính lại trên chuỗi đã gộp
        merged["funding_interval_h"] = merged[key].diff().dt.total_seconds() / 3600.0
    kept = len(merged) - len(new)
    if kept > 0:
        print(f"[{label}] gộp với file cũ: giữ {kept:,} dòng cũ ngoài cửa sổ vừa tải (tổng {len(merged):,} dòng)")
    return merged




# ----------------------------------------------------------------------------- thao tác
def run_venue(v, a, since_ms, until_ms, live0, make_ex=None):
    limit = a.limit or LIMITS.get(v["ccxt_id"], DEFAULT_LIMIT)
    ex = (make_ex or make_exchange)(v["ccxt_id"])
    stats = {"windows": 0, "failed": 0}
    print(f"[{v['venue']}] tải funding {datetime.fromtimestamp(since_ms/1000, UTC):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(until_ms/1000, UTC):%Y-%m-%d %H:%M} (limit {limit}/lần, ccxt={v['ccxt_id']})")
    rec = fetch_all(ex, v["symbol"], since_ms, until_ms, limit, v["venue"], stats)
    if not rec:
        _problem(a, f"[{v['venue']}] KHÔNG tải được kỳ funding nào (kiểm tra symbol/endpoint, hoặc dùng --probe); "
                    f"file cũ giữ nguyên, dữ liệu thiếu -> mã thoát 1")
        return None
    path = a.out / f"{v['venue']}_funding.{'parquet' if a.format == 'parquet' else 'csv'}"
    if stats["failed"]:
        print(f"[{v['venue']}] CẢNH BÁO: {stats['failed']} cửa sổ tải lỗi -> dữ liệu mới chưa đầy đủ; dòng cũ được giữ, chạy lại để bù")
    df = merge_with_existing(to_frame(rec, v, live0), path, "ts_funding_utc", ["ts_funding_utc", "ts_available"],
                             live0, v["venue"], on_problem=lambda m: _problem(a, f"[{v['venue']}] {m}"))
    first_ms = int(df["ts_funding_utc"].min().timestamp() * 1000)
    rep = make_report(df, v, min(since_ms, first_ms), until_ms, stats)
    write_frame(df, path, a.format)
    print(f"[{v['venue']}] xong: {rep['n_rows']:,} kỳ, {rep['first_utc']} -> {rep['last_utc']}, "
          f"thiếu ước tính {rep['n_missing_est']}, chu kỳ trung vị {rep['median_interval_h']:.1f}h -> {path.name}")
    if rep["first_utc"] > datetime.fromtimestamp(since_ms / 1000, UTC) + timedelta(days=2):
        print(f"[{v['venue']}] LƯU Ý: sàn chỉ trả lịch sử từ {rep['first_utc']:%Y-%m-%d}, muộn hơn mốc yêu cầu "
              f"({rep['requested_since_utc'][:10]}) -> mẫu dài của sàn này thiếu phần đầu, ghi vào báo cáo.")
    return rep




def oi_since_ms(a, since_ms, until_ms):
    return max(since_ms, until_ms - int(a.oi_days * 24 * H_MS))




def _contract_size(ex, symbol):
    try:
        return ex.market(symbol).get("contractSize")
    except Exception:  # noqa: BLE001 — chưa load markets / sàn giả: không biết
        return None




def _ref_price(ex, symbol):
    """Giá BTC hiện tại làm mốc soát đơn vị OI; không lấy được thì trả None (dùng khoảng UNIT_BAND_FALLBACK)."""
    try:
        t = with_retry(lambda: ex.fetch_ticker(symbol), tries=3, label=symbol)
        p = t.get("last") or t.get("close") or t.get("mark")
        return float(p) if p else None
    except Exception:  # noqa: BLE001
        return None




def run_oi(v, a, since_ms, until_ms, live0, mults, make_ex=None):
    tf = a.oi_timeframe
    limit = a.oi_limit or OI_LIMITS.get(v["ccxt_id"], DEFAULT_LIMIT)
    s_ms = oi_since_ms(a, since_ms, until_ms)
    ex = (make_ex or make_exchange)(v["ccxt_id"])
    stats = {"windows": 0, "failed": 0}
    print(f"[{v['venue']}] tải OI {tf} {datetime.fromtimestamp(s_ms/1000, UTC):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(until_ms/1000, UTC):%Y-%m-%d %H:%M} (limit {limit}/lần)")
    rec = fetch_all(ex, v["symbol"], s_ms, until_ms, limit, v["venue"] + "/oi", stats,
                    win=lambda e_, sy, s, e, li: fetch_window_oi(e_, sy, s, e, li, tf), soft=True, step_days=OI_STEP_DAYS)
    if not rec:
        _problem(a, f"[{v['venue']}] OI: KHÔNG tải được nến OI nào (kiểm tra --probe; sàn có thể không hỗ trợ "
                    f"fetch_open_interest_history); file cũ giữ nguyên, dữ liệu thiếu -> mã thoát 1")
        return None
    mult = resolve_oi_mult(v["venue"], mults)                 # --oi-mult > mặc định theo venue > None
    csize = _contract_size(ex, v["symbol"])
    if mult is None and csize not in (None, 1, 1.0):
        _problem(a, f"[{v['venue']}] contractSize={csize} nhưng chưa có hệ số quy đổi -> KHÔNG ghi OI "
                    f"(số hợp đồng sẽ lọt vào cột BTC). Thêm --oi-mult {v['venue']}=<BTC mỗi hợp đồng>.")
        return None
    new = to_frame_oi(rec, v, tf, live0, mult)
    status, implied = oi_unit_check(new, _ref_price(ex, v["symbol"]))
    if status == "fail":
        _problem(a, f"[{v['venue']}] ĐƠN VỊ OI KHÔNG KHỚP: USDT/BTC = {implied:,.2f} không giống giá BTC (hệ số đang dùng "
                    f"{mult if mult is not None else 1.0}; giá ngầm định nếu bỏ hệ số = "
                    f"{implied * (mult if mult is not None else 1.0):,.2f}) -> KHÔNG ghi file, file cũ giữ nguyên. "
                    f"Chỉnh bằng --oi-mult {v['venue']}=<BTC mỗi hợp đồng> (=1 nếu sàn đã trả theo BTC).")
        return None
    path = a.out / f"{v['venue']}_oi.{'parquet' if a.format == 'parquet' else 'csv'}"
    df = merge_with_existing(new, path, "ts_oi_utc", ["ts_oi_utc", "ts_available"], live0, v["venue"] + "/oi", check_units=True,
                             on_problem=lambda m: _problem(a, f"[{v['venue']}/oi] {m}"))
    first_ms = int(df["ts_oi_utc"].min().timestamp() * 1000)
    rep = make_oi_report(df, v, tf, min(s_ms, first_ms), until_ms, stats, mult)
    rep["unit_check"], rep["contract_size"] = status, csize
    write_frame(df, path, a.format)
    print(f"[{v['venue']}] OI xong: {rep['n_rows']:,} nến, {rep['first_utc']} -> {rep['last_utc']}, thiếu ước tính "
          f"{rep['n_missing_est']}, OI median {rep['oi_base_median']:,.0f} BTC / {rep['oi_usdt_median']:,.0f} USDT, "
          f"giá ngầm định {rep['implied_price_median']:,.0f}, unit_check={status} -> {path.name}")
    if status == "unverified":
        print(f"[{v['venue']}] LƯU Ý: sàn không trả open_interest_usdt nên KHÔNG kiểm được đơn vị; hệ số đã dùng = "
              f"{mult if mult is not None else 1.0}. Đối chiếu OI của sàn với sàn khác trước khi dùng.")
    if rep["pct_usdt_missing"] > 50:
        print(f"[{v['venue']}] LƯU Ý: sàn không trả open_interest_usdt ({rep['pct_usdt_missing']:.0f}% trống) -> "
              f"so sánh OI giữa các sàn bằng open_interest_base (BTC), không tự suy USDT.")
    return rep




def probe_oi(v, a, since_ms, until_ms, make_ex=None):
    ex = (make_ex or make_exchange)(v["ccxt_id"])
    tf = a.oi_timeframe
    limit = a.oi_limit or OI_LIMITS.get(v["ccxt_id"], DEFAULT_LIMIT)
    s0 = oi_since_ms(a, since_ms, until_ms)
    for name, s, e in (("OI 2 ngày gần nhất", until_ms - 48 * H_MS, until_ms), ("OI đầu cửa sổ", s0, s0 + 48 * H_MS)):
        try:
            got = with_retry(lambda: fetch_window_oi(ex, v["symbol"], s, e, limit, tf), label=v["venue"])
        except Exception as ex_:  # noqa: BLE001
            print(f"    {name}: LỖI {type(ex_).__name__}: {str(ex_)[:200]}"); continue
        if not got:
            print(f"    {name}: 0 nến"); continue
        ts = sorted(got)
        b = sorted(x[0] for x in got.values() if x[0] is not None)
        u = sorted(x[1] for x in got.values() if x[1] is not None)
        print(f"    {name}: {len(ts)} nến, {datetime.fromtimestamp(ts[0]/1000, UTC):%Y-%m-%d %H:%M} -> "
              f"{datetime.fromtimestamp(ts[-1]/1000, UTC):%Y-%m-%d %H:%M}, OI base median "
              f"{b[len(b)//2] if b else float('nan'):,.1f}, usdt median {u[len(u)//2] if u else float('nan'):,.0f}")




def _read(path: Path, cols):
    import pandas as pd
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    for c in cols:
        df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[ns, UTC]")
    return df.sort_values(cols[0]).reset_index(drop=True)




def _events(v, fu, oi, live0):
    """Dạng sự kiện: dòng funding (tại giờ chốt kỳ) và dòng OI (tại ts_available) xếp chung một bảng; trùng giờ thì gộp một dòng."""
    import pandas as pd
    fcols = ["ts_available", "ts_funding_utc", "funding_rate", "funding_rate_bps", "funding_interval_h"]
    ocols = ["ts_available", "ts_oi_utc", "open_interest_base", "open_interest_usdt"]
    f2 = fu[fcols] if fu is not None else pd.DataFrame({c: [] for c in fcols})
    o2 = oi[ocols] if oi is not None else pd.DataFrame({c: [] for c in ocols})
    for d in (f2, o2):
        d["ts_available"] = d["ts_available"].astype("datetime64[ns, UTC]")
    g = pd.merge(f2, o2, on="ts_available", how="outer").sort_values("ts_available").reset_index(drop=True)
    g = g.rename(columns={"ts_funding_utc": "funding_ts_utc", "ts_oi_utc": "oi_ts_utc"})
    for c in ("funding_ts_utc", "oi_ts_utc"):
        g[c] = pd.to_datetime(g[c], utc=True).astype("datetime64[ns, UTC]")
    g["ts_utc"] = g["ts_available"]
    g["exchange"], g["market_type"], g["venue"], g["symbol"] = v["exchange"], "perp", v["venue"], "BTC/USDT"
    g["data_kind"] = "historical"                              # đầu vào đã qua check_source: chỉ còn nguồn historical
    g["in_live_window"] = g["ts_utc"] >= pd.Timestamp(live0)   # giữ thông tin giai đoạn live
    return g[PROCESSED_COLS]




def build_processed(a, venues, live0):
    """Gộp funding + OI thành lưới 1 giờ mỗi venue bằng ghép as-of (chỉ dùng thông tin ts_available <= ts_utc)."""
    import pandas as pd
    ext = "parquet" if a.format == "parquet" else "csv"
    parts = []
    bad = False                                           # có đầu vào thiếu/không hợp lệ -> KHÔNG ghi đè bảng gộp cũ
    need_oi = not getattr(a, "no_oi", False)
    for v in venues:
        fp, op = a.out / f"{v['venue']}_funding.{ext}", a.out / f"{v['venue']}_oi.{ext}"
        fu = _read(fp, ["ts_funding_utc", "ts_available"]) if fp.exists() else None
        oi = _read(op, ["ts_oi_utc", "ts_available"]) if op.exists() else None
        if fu is None:
            _problem(a, f"[{v['venue']}] thiếu file funding {fp.name}; không dựng funding_oi (tránh ghi đè bảng đủ sàn bằng bảng thiếu)")
            bad = True
        if oi is None and need_oi:
            _problem(a, f"[{v['venue']}] thiếu file OI {op.name}; không dựng funding_oi (tránh ghi đè bảng đủ sàn bằng bảng thiếu)")
            bad = True
        fu, why = check_source(fu, fp.name)
        if why:
            _problem(a, f"[{v['venue']}] bỏ file funding: {why}")
            fu, bad = None, True
        oi, why = check_source(oi, op.name)
        if why:
            _problem(a, f"[{v['venue']}] bỏ file OI: {why}")
            oi, bad = None, True
        if oi is not None and len(oi):
            st, implied = oi_unit_check(oi)               # file trên đĩa (có thể từ bản code cũ) không được lọt số hợp đồng vào cột BTC
            if st == "fail":
                _problem(a, f"[{v['venue']}] file {op.name} sai đơn vị (USDT/BTC = {implied:,.2f}); không dựng funding_oi. "
                            f"Tải lại OI (bản mới tự sao lưu file cũ) hoặc đặt --oi-mult đúng.")
                oi, bad = None, True
        if fu is None and oi is None:
            continue
        if not a.hourly:                                   # mặc định: MỖI QUAN SÁT MỘT DÒNG, không lặp, không lấp
            parts.append(_events(v, fu, oi, live0))
            continue
        starts, ends = [], []
        if fu is not None:
            starts.append(fu["ts_funding_utc"].min()); ends.append(fu["ts_funding_utc"].max())
        if oi is not None:
            starts.append(oi["ts_available"].min()); ends.append(oi["ts_available"].max())
        g = pd.DataFrame({"ts_utc": pd.date_range(min(starts).ceil("h"), max(ends).floor("h"), freq="h", tz="UTC")})
        g["ts_utc"] = g["ts_utc"].astype("datetime64[ns, UTC]")
        if fu is not None:
            iv = fu["funding_interval_h"].median()
            iv = iv if iv == iv and iv > 0 else 8.0
            f2 = fu[["ts_funding_utc", "funding_rate", "funding_rate_bps", "funding_interval_h"]]
            g = pd.merge_asof(g, f2, left_on="ts_utc", right_on="ts_funding_utc", direction="backward",
                              tolerance=pd.Timedelta(hours=2 * iv)).rename(columns={"ts_funding_utc": "funding_ts_utc"})
        else:
            for c in ("funding_rate", "funding_rate_bps", "funding_interval_h"):
                g[c] = float("nan")
            g["funding_ts_utc"] = pd.Series(pd.NaT, index=g.index, dtype="datetime64[ns, UTC]")
        if oi is not None:
            tf = oi["timeframe"].iloc[0]
            o2 = oi[["ts_oi_utc", "ts_available", "open_interest_base", "open_interest_usdt"]].rename(
                columns={"ts_available": "oi_avail"})
            g = pd.merge_asof(g, o2, left_on="ts_utc", right_on="oi_avail", direction="backward",
                              tolerance=pd.Timedelta(milliseconds=2 * tf_ms(tf))).drop(columns="oi_avail").rename(
                columns={"ts_oi_utc": "oi_ts_utc"})
        else:
            for c in ("open_interest_base", "open_interest_usdt"):
                g[c] = float("nan")
            g["oi_ts_utc"] = pd.Series(pd.NaT, index=g.index, dtype="datetime64[ns, UTC]")
        g = g[g["funding_rate"].notna() | g["open_interest_base"].notna()].copy()
        g["ts_available"] = g["ts_utc"]
        g["exchange"], g["market_type"], g["venue"], g["symbol"] = v["exchange"], "perp", v["venue"], "BTC/USDT"
        g["data_kind"] = "historical"                              # đầu vào đã qua check_source: chỉ còn nguồn historical
        g["in_live_window"] = g["ts_utc"] >= pd.Timestamp(live0)   # giữ thông tin giai đoạn live
        parts.append(g[PROCESSED_COLS])
    if bad:
        print("KHÔNG ghi funding_oi: đầu vào thiếu hoặc không hợp lệ (xem các dòng LỖI ở trên); file cũ (nếu có) giữ nguyên")
        return None
    if not parts:
        print("Chưa có file funding/OI nào để dựng funding_oi")
        return None
    out = pd.concat(parts, ignore_index=True).sort_values(["venue", "ts_utc"]).reset_index(drop=True)
    path = a.processed.with_suffix(".parquet" if a.format == "parquet" else ".csv")
    write_frame(out, path, a.format)
    summ = out.groupby("venue").agg(rows=("ts_utc", "size"), first=("ts_utc", "min"), last=("ts_utc", "max"),
                                    funding_ok=("funding_rate", lambda x: int(x.notna().sum())),
                                    oi_ok=("open_interest_base", lambda x: int(x.notna().sum())))
    print(f"\nĐã ghi {path} ({len(out):,} dòng)")
    print(summ.to_string())
    return out




def probe(v, since_ms, until_ms, limit, make_ex=None):
    ex = (make_ex or make_exchange)(v["ccxt_id"])
    limit = limit or LIMITS.get(v["ccxt_id"], DEFAULT_LIMIT)
    print(f"[{v['venue']}] ccxt={v['ccxt_id']} symbol={v['symbol']} limit={limit}")
    probes = (("30 ngày gần nhất", until_ms - WINDOW_DAYS * 24 * H_MS, until_ms),
              ("đầu mẫu (30 ngày từ mốc since)", since_ms, since_ms + WINDOW_DAYS * 24 * H_MS))
    for name, s, e in probes:
        try:
            got = with_retry(lambda: fetch_window(ex, v["symbol"], s, e, limit), label=v["venue"])
        except Exception as ex_:  # noqa: BLE001
            print(f"    {name}: LỖI {type(ex_).__name__}: {str(ex_)[:200]}"); continue
        if not got:
            print(f"    {name}: 0 kỳ (sàn không trả dữ liệu ở đoạn này?)"); continue
        ts = sorted(got)
        print(f"    {name}: {len(ts)} kỳ, {datetime.fromtimestamp(ts[0]/1000, UTC):%Y-%m-%d %H:%M} -> "
              f"{datetime.fromtimestamp(ts[-1]/1000, UTC):%Y-%m-%d %H:%M}, funding {min(got.values())*1e4:.3f} .. "
              f"{max(got.values())*1e4:.3f} bps/kỳ")




def snapshot(v, a, make_ex=None, mults=None):
    import pandas as pd
    ex = (make_ex or make_exchange)(v["ccxt_id"])
    r = with_retry(lambda: ex.fetch_funding_rate(v["symbol"]), label=v["venue"])
    row = {"ts_recv_utc": datetime.now(UTC).isoformat(timespec="seconds"), "venue": v["venue"],
           "funding_rate": r.get("fundingRate"), "next_funding_utc": r.get("nextFundingDatetime") or r.get("fundingDatetime"),
           "mark_price": r.get("markPrice"), "index_price": r.get("indexPrice"), "interest_rate": r.get("interestRate")}
    try:
        o = with_retry(lambda: ex.fetch_open_interest(v["symbol"]), label=v["venue"])
        row["open_interest_base"], row["open_interest_usdt"] = o.get("openInterestAmount"), o.get("openInterestValue")
        k = resolve_oi_mult(v["venue"], mults)             # cùng quy tắc với run_oi: mặc định okx_perp = 0.01
        if k is not None and row["open_interest_base"] is not None:
            row["open_interest_base"] = float(row["open_interest_base"]) * k
    except Exception as e:  # noqa: BLE001 — snapshot funding vẫn ghi dù OI lỗi
        print(f"[{v['venue']}] snapshot OI lỗi {type(e).__name__}: {str(e)[:120]}")
        row["open_interest_base"] = row["open_interest_usdt"] = None
    path = a.out / "funding_snapshots.csv"
    a.out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)
    print(f"[{v['venue']}] snapshot: funding={row['funding_rate']} mark={row['mark_price']} index={row['index_price']} "
          f"oi={row['open_interest_base']}")




def main(argv=None, make_ex=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venues", help="danh sách venue perp, cách nhau dấu phẩy (mặc định: mọi perp trong config.yaml)")
    ap.add_argument("--config-dir", type=Path, default=ROOT / "config")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    ap.add_argument("--since", help="ghi đè mốc bắt đầu (ISO, UTC); mặc định sample.start trong config.yaml")
    ap.add_argument("--until", help="ghi đè mốc kết thúc (ISO, UTC); mặc định bây giờ")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--oi-timeframe", default="1h", help="độ dài nến OI (mặc định 1h)")
    ap.add_argument("--oi-days", type=float, default=29, help="số ngày OI gần nhất (mặc định 29; Binance chỉ giữ ~30 ngày)")
    ap.add_argument("--oi-limit", type=int, help="ghi đè limit mỗi lần gọi OI")
    ap.add_argument("--oi-mult", action="append",
                    help="nhân open_interest_base để ra BTC, vd okx_perp=0.01 (sàn trả theo hợp đồng); ghi đè DEFAULT_OI_MULT")
    ap.add_argument("--no-oi", action="store_true", help="bỏ qua open interest (chỉ funding)")
    ap.add_argument("--processed", type=Path, default=DEFAULT_PROCESSED, help="đường dẫn funding_oi.parquet")
    ap.add_argument("--hourly", action="store_true",
                    help="funding_oi dạng lưới 1 giờ (funding lặp lại 8 lần/kỳ; chỉ để ghép chuỗi giờ). Mặc định: 1 dòng mỗi quan sát")
    ap.add_argument("--build-only", action="store_true", help="chỉ dựng lại funding_oi từ các file đã tải, không gọi sàn")
    ap.add_argument("--probe", action="store_true", help="chỉ thử, không ghi file")
    ap.add_argument("--snapshot", action="store_true", help="thêm funding tạm tính hiện tại vào funding_snapshots.csv")
    a = ap.parse_args(argv)
    a.problems = []                                    # lỗi cần làm main thoát mã 1 (sai đơn vị OI, lỗi tải, ...)
    cfg, venues = load_cfg(a.config_dir)
    all_venues = venues
    if not venues:
        sys.exit("config.yaml không có venue perp nào")
    if a.venues:
        want = [x.strip() for x in a.venues.split(",") if x.strip()]
        unknown = [w for w in want if w not in {v["venue"] for v in venues}]
        if unknown:
            sys.exit(f"venue perp không có trong config.yaml: {unknown}")
        venues = [v for v in venues if v["venue"] in want]
    since = parse_ts(a.since) if a.since else parse_ts(cfg["sample"]["start"])
    until = parse_ts(a.until) if a.until else datetime.now(UTC)
    since_ms, until_ms = int(since.timestamp() * 1000), int(until.timestamp() * 1000)
    live0 = live_start(a.config_dir)
    a.out.mkdir(parents=True, exist_ok=True)
    reports, oi_reports = [], []
    mults = parse_mults(a.oi_mult)                     # chỉ phần người dùng gõ; mặc định theo venue do resolve_oi_mult thêm
    if a.build_only:
        build_processed(a, all_venues, live0)
        return 1 if a.problems else 0
    for v in venues:
        try:
            if a.probe:
                probe(v, since_ms, until_ms, a.limit, make_ex)
                if not a.no_oi:
                    probe_oi(v, a, since_ms, until_ms, make_ex)
            elif a.snapshot and not (a.since or a.until):
                snapshot(v, a, make_ex, mults)
            else:
                rep = run_venue(v, a, since_ms, until_ms, live0, make_ex)
                if rep:
                    reports.append(rep)
                if not a.no_oi:
                    orep = run_oi(v, a, since_ms, until_ms, live0, mults, make_ex)
                    if orep:
                        oi_reports.append(orep)
                if a.snapshot:
                    snapshot(v, a, make_ex, mults)
        except Exception as e:  # noqa: BLE001 — một venue lỗi không làm hỏng venue khác
            _problem(a, f"[{v['venue']}] DỪNG do lỗi {type(e).__name__}: {str(e)[:300]}")
    if reports:
        save_report(a.out, reports)
        import pandas as pd
        pd.set_option("display.width", 250)
        print("\n" + pd.DataFrame(reports)[REPORT_COLS[:11]].to_string(index=False))
        print(f"\nBáo cáo: {a.out / 'funding_report.csv'}")
    if oi_reports:
        save_report(a.out, oi_reports, "oi_report.csv", OI_REPORT_COLS)
        import pandas as pd
        pd.set_option("display.width", 250)
        print("\n" + pd.DataFrame(oi_reports)[OI_REPORT_COLS[:12]].to_string(index=False))
        print(f"\nBáo cáo: {a.out / 'oi_report.csv'}")
    if (reports or oi_reports) and not a.probe:
        build_processed(a, all_venues, live0)
    if a.problems:
        print(f"\n{len(a.problems)} vấn đề cần xử lý (xem các dòng LỖI ở trên) -> mã thoát 1")
    return 1 if a.problems else 0




if __name__ == "__main__":
    sys.exit(main())