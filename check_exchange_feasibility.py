"""
C3.01 - KIEM TRA KHA THI DU LIEU CAC SAN (TV3)
================================================
Chay:   python c3_01_exchange_feasibility.py
        python c3_01_exchange_feasibility.py --only binance,okx --skip-perp

Moi san duoc kiem tra:
  1. Spot BTC/USDT (hoac BTC/USD): so nen toi da moi lan goi, `since` co duoc ton trong khong
  2. Do sau lich su 1 phut thuc te (7/30/90/180/365 ngay) - nen dau tien phai o gan moc yeu cau
  3. Phan trang 3 trang lien tiep: co lo hong/trung nen khong, toc do, uoc tinh thoi gian tai 1 nam
  4. Order book: limit nao hop le, co phu duoc +-10 bps quanh mid khong (cho collector live)
  5. Perpetual: lich su nen, lich su funding rate, lich su open interest (muc nang cao, RQ3)

Dau ra (thu muc docs/):
  EXCHANGE_FEASIBILITY.md    bao cao cho nhom (ket luan sinh tu du lieu, KHONG viet tay)
  EXCHANGE_FEASIBILITY.csv   bang phang
  EXCHANGE_FEASIBILITY.json  ket qua chi tiet (de truy vet)

Luu y:
  - Ket qua phu thuoc IP/vung dia ly cua may chay. Neu bi chan (403/451) se duoc danh dau.
  - OFFICIAL_LIMITS ben duoi la so TU TAI LIEU, can tv3 xac nhan lai tren URL va dien ngay tra.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

# ============================================================
# CAU HINH
# ============================================================
TIMEFRAME = "1m"
MS_MIN = 60_000
MS_HOUR = 3_600_000
MS_DAY = 86_400_000

# ten hien thi -> id ccxt. Coinbase co 2 API khac nhau nen kiem tra ca hai.
EXCHANGES = {
    "Binance": "binance",
    "OKX": "okx",
    "Bybit": "bybit",
    "Kraken": "kraken",
    "Coinbase Exchange": "coinbaseexchange",
}

# Gioi han so nen/lan goi THEO TAI LIEU. TV3 PHAI xac nhan lai tren URL, dien verified_on.
# (Khong tin so nay mu quang: bao cao in kem co "chua xac nhan" neu verified_on trong.)
OFFICIAL_LIMITS = {
    "binance": {"limit": 1000, "url": "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints"},
    "okx": {"limit": 300, "url": "https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks"},
    "bybit": {"limit": 1000, "url": "https://bybit-exchange.github.io/docs/v5/market/kline"},
    "kraken": {"limit": 720, "url": "https://docs.kraken.com/exchange/guides/general/historical-data"},
    "coinbaseexchange": {"limit": 300, "url": "https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles"},
}

QUOTES_ALLOWED = ("USDT", "USD", "USDC")          # thu tu uu tien
TEST_LIMITS = [100, 300, 500, 720, 1000, 1500]     # thu so nen/lan goi
HISTORY_DAYS = [7, 30, 90, 180, 365]
HISTORY_TOLERANCE_MIN = 10                         # nen dau tien lech > 10 phut = KHONG DU
REQUIRED_HISTORY_DAYS = 90                         # nguong de coi san du dieu kien lam Design A
PAGINATION_PAGES = 3
OB_LIMITS = [20, 50, 100, 200, 500]
OB_TARGET_BPS = 10
FUNDING_TOLERANCE_H = 24
OI_TOLERANCE_H = 2
MAX_RETRIES = 3
RETRY_DELAY_SEC = 2

REPORT_DIR = Path("docs")
REPORT_MD = REPORT_DIR / "EXCHANGE_FEASIBILITY.md"
REPORT_CSV = REPORT_DIR / "EXCHANGE_FEASIBILITY.csv"
REPORT_JSON = REPORT_DIR / "EXCHANGE_FEASIBILITY.json"

GEO_RE = re.compile(r"\b(403|451)\b|restricted|forbidden|not available in your|unavailable for legal", re.I)


# ============================================================
# TIEN ICH
# ============================================================
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_ms() -> int:
    return int(utc_now().timestamp() * 1000)


def classify(e: Exception) -> str:
    """Phan loai loi: geo_blocked | rate_limit | network | other."""
    if GEO_RE.search(str(e)):
        return "geo_blocked"
    if isinstance(e, ccxt.RateLimitExceeded):
        return "rate_limit"
    if isinstance(e, ccxt.NetworkError):
        return "network"
    return "other"


def call(func, *args, **kwargs):
    """Goi API. Chi retry loi mang/rate limit. Tra (ket_qua, loi_str, loai_loi)."""
    last, kind = "", "network"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs), None, None
        except Exception as e:  # noqa: BLE001
            kind = classify(e)
            last = f"{type(e).__name__}: {str(e)[:160]}"
            if kind in ("network", "rate_limit") and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SEC * attempt)
                continue
            return None, last, kind
    return None, last, kind


def pick_symbol(markets: dict, perp: bool):
    """Chon BTC/<quote> dung loai. Spot: bat buoc quote trong QUOTES_ALLOWED (khong lay BTC/EUR...).
    Perp: swap tuyen tinh (linear), khong het han, settle == quote."""
    cands = []
    for m in markets.values():
        if m.get("base") != "BTC" or m.get("quote") not in QUOTES_ALLOWED:
            continue
        if m.get("active") is False:
            continue
        if perp:
            if m.get("swap") and m.get("linear") and m.get("expiry") is None \
                    and m.get("settle") == m.get("quote"):
                cands.append(m)
        elif m.get("spot"):
            cands.append(m)
    cands.sort(key=lambda m: QUOTES_ALLOWED.index(m["quote"]))
    return cands[0]["symbol"] if cands else None


# ============================================================
# CAC BAI TEST
# ============================================================
def test_max_limit(ex, symbol: str) -> dict:
    """So nen thuc te tra ve theo tung `limit` (san co the am tham cat bot)."""
    returned, errors = {}, {}
    for lim in TEST_LIMITS:
        c, err, _ = call(ex.fetch_ohlcv, symbol, timeframe=TIMEFRAME, limit=lim)
        if err:
            errors[lim] = err
        else:
            returned[lim] = len(c or [])
    return {"returned": returned, "errors": errors,
            "max_returned": max(returned.values()) if returned else 0}


def test_history_depth(ex, symbol: str) -> list[dict]:
    """Do sau lich su that. Nen dau tien lech xa moc `since` = san bo qua since / het lich su => KHONG DU."""
    out, t_now = [], now_ms()
    for days in HISTORY_DAYS:
        target = t_now - days * MS_DAY
        c, err, kind = call(ex.fetch_ohlcv, symbol, timeframe=TIMEFRAME, since=target, limit=5)
        if err:
            out.append({"days": days, "status": "LOI", "detail": err[:80], "kind": kind})
        elif not c:
            out.append({"days": days, "status": "RONG", "detail": "", "kind": None})
        else:
            off_min = (c[0][0] - target) / MS_MIN
            ok = abs(off_min) <= HISTORY_TOLERANCE_MIN
            out.append({"days": days, "status": "OK" if ok else "KHONG DU",
                        "offset_h": round(off_min / 60, 1), "kind": None,
                        "detail": "" if ok else f"nen dau cach moc {off_min / 60:+.0f}h"})
    return out


def max_ok_days(hist: list[dict]) -> int:
    return max((h["days"] for h in hist if h["status"] == "OK"), default=0)


def fmt_hist(h: dict) -> str:
    return "OK" if h["status"] == "OK" else f"{h['status']} ({h['detail']})" if h["detail"] else h["status"]


def test_pagination(ex, symbol: str, page_size: int) -> dict:
    """Tai PAGINATION_PAGES trang lien tiep bang since: kiem tra lo hong, trung lap, toc do."""
    page_size = max(1, min(page_size, 1000))
    since = now_ms() - 3 * MS_DAY
    first_since, ts, t0, pages_ok = since, [], time.time(), 0
    for _ in range(PAGINATION_PAGES):
        c, err, kind = call(ex.fetch_ohlcv, symbol, timeframe=TIMEFRAME, since=since, limit=page_size)
        if err:
            return {"ok": False, "error": err, "kind": kind, "page_size": page_size}
        if not c:
            break
        ts += [x[0] for x in c]
        since = c[-1][0] + MS_MIN
        pages_ok += 1
    if not ts:
        return {"ok": False, "error": "khong co nen nao", "kind": None, "page_size": page_size}
    steps = [b - a for a, b in zip(ts, ts[1:])]
    gaps = sum(1 for s in steps if s > MS_MIN)
    dups = sum(1 for s in steps if s <= 0)
    sec_page = (time.time() - t0) / max(pages_ok, 1)
    first_off_min = (ts[0] - first_since) / MS_MIN
    since_ok = abs(first_off_min) <= HISTORY_TOLERANCE_MIN
    return {
        "ok": gaps == 0 and dups == 0 and since_ok, "error": None, "kind": None,
        "page_size": page_size, "n_candles": len(ts), "gaps": gaps, "dups": dups,
        "since_ok": since_ok, "sec_per_page": round(sec_page, 2),
        "eta_1y_min": round(525_600 / page_size * sec_page / 60, 1),
    }


def test_orderbook(ex, symbol: str) -> dict:
    """Limit nao hop le va co phu duoc +-OB_TARGET_BPS quanh mid khong (cho collector live)."""
    rows = []
    for lim in OB_LIMITS:
        ob, err, _ = call(ex.fetch_order_book, symbol, lim)
        if err:
            rows.append({"limit": lim, "ok": False, "error": err[:80]})
            continue
        bids, asks = ob.get("bids") or [], ob.get("asks") or []
        if not bids or not asks:
            rows.append({"limit": lim, "ok": False, "error": "so lenh rong"})
            continue
        mid = (bids[0][0] + asks[0][0]) / 2
        span_bid = (mid - bids[-1][0]) / mid * 1e4
        span_ask = (asks[-1][0] - mid) / mid * 1e4
        rows.append({"limit": lim, "ok": True, "n_bids": len(bids), "n_asks": len(asks),
                     "span_bid_bps": round(span_bid, 1), "span_ask_bps": round(span_ask, 1),
                     "covers": span_bid >= OB_TARGET_BPS and span_ask >= OB_TARGET_BPS})
    ok_rows = [r for r in rows if r["ok"]]
    covering = [r for r in ok_rows if r["covers"]]
    rec = covering[0] if covering else (ok_rows[-1] if ok_rows else None)
    return {"rows": rows,
            "recommended_limit": rec["limit"] if rec else None,
            "covers_target": bool(covering),
            "rec_span_bid_bps": rec["span_bid_bps"] if rec else None,
            "rec_span_ask_bps": rec["span_ask_bps"] if rec else None}


def _earliest_check(rows, target, tol_h):
    if not rows:
        return None
    ts = rows[0].get("timestamp")
    if ts is None:
        return None
    return abs(ts - target) / MS_HOUR <= tol_h


def test_derivs(ex, perp_symbol: str) -> dict:
    """Lich su funding rate va open interest (muc nang cao, RQ3). Chi kiem tra `has` la CHUA DU."""
    t_now = now_ms()
    res = {"has_funding_history": bool(ex.has.get("fetchFundingRateHistory")),
           "has_oi_history": bool(ex.has.get("fetchOpenInterestHistory")),
           "funding_days_ok": 0, "oi_days_ok": 0, "errors": []}
    for days in (30, 90, 365):
        target = t_now - days * MS_DAY
        if res["has_funding_history"]:
            rows, err, _ = call(ex.fetch_funding_rate_history, perp_symbol, since=target, limit=10)
            if err:
                res["errors"].append(f"funding {days}d: {err[:70]}")
            elif _earliest_check(rows, target, FUNDING_TOLERANCE_H):
                res["funding_days_ok"] = days
        if res["has_oi_history"]:
            rows, err, _ = call(ex.fetch_open_interest_history, perp_symbol, "1h", target, 10)
            if err:
                res["errors"].append(f"OI {days}d: {err[:70]}")
            elif _earliest_check(rows, target, OI_TOLERANCE_H):
                res["oi_days_ok"] = days
    return res


# ============================================================
# CHAY CHO MOT SAN
# ============================================================
def check_exchange(name: str, ex_id: str, skip_perp: bool) -> dict:
    off = OFFICIAL_LIMITS.get(ex_id, {})
    r = {
        "san": name, "ma_ccxt": ex_id, "thoi_gian_kt": utc_now().isoformat(),
        "status": "OK", "geo_blocked": False, "errors": [],
        "rate_limit_ms": None,
        "official_limit": off.get("limit"), "official_verified": bool(off.get("verified_on")),
        "spot_symbol": None, "spot_quote": None, "spot_max_returned": 0,
        "pagination": None, "hist_spot": [], "hist_spot_max_ok_days": 0, "orderbook": None,
        "perp_symbol": None, "perp_quote": None, "hist_perp": [], "hist_perp_max_ok_days": 0,
        "derivs": None,
    }
    if not hasattr(ccxt, ex_id):
        r["status"] = "KHONG CO TRONG CCXT"
        return r
    ex = getattr(ccxt, ex_id)({"enableRateLimit": True, "timeout": 20000})

    def step(label, fn, default=None):
        """Chay mot buoc; loi khong lam hong ca san, duoc ghi lai."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            r["errors"].append(f"{label}: {type(e).__name__}: {str(e)[:120]}")
            return default

    try:
        r["rate_limit_ms"] = round(float(ex.rateLimit)) if getattr(ex, "rateLimit", None) else None
        markets, err, kind = call(ex.load_markets)
        if err:
            r["status"] = "LOI (CHAN VUNG?)" if kind == "geo_blocked" else "LOI"
            r["geo_blocked"] = kind == "geo_blocked"
            r["errors"].append(f"load_markets: {err}")
            return r

        # ---- SPOT ----
        spot = pick_symbol(markets, perp=False)
        if spot:
            r["spot_symbol"], r["spot_quote"] = spot, markets[spot]["quote"]
            lim = step("max_limit", lambda: test_max_limit(ex, spot), {})
            r["spot_max_returned"] = (lim or {}).get("max_returned", 0)
            for lv, msg in ((lim or {}).get("errors") or {}).items():
                if GEO_RE.search(msg):
                    r["geo_blocked"] = True
            r["limit_detail"] = lim
            r["pagination"] = step("pagination", lambda: test_pagination(ex, spot, r["spot_max_returned"] or 100))
            r["hist_spot"] = step("hist_spot", lambda: test_history_depth(ex, spot), [])
            r["hist_spot_max_ok_days"] = max_ok_days(r["hist_spot"])
            r["orderbook"] = step("orderbook", lambda: test_orderbook(ex, spot))
        else:
            r["errors"].append(f"Khong tim thay BTC spot voi quote {QUOTES_ALLOWED}")

        # ---- PERP ----
        if not skip_perp:
            perp = pick_symbol(markets, perp=True)
            if perp:
                r["perp_symbol"], r["perp_quote"] = perp, markets[perp]["quote"]
                r["hist_perp"] = step("hist_perp", lambda: test_history_depth(ex, perp), [])
                r["hist_perp_max_ok_days"] = max_ok_days(r["hist_perp"])
                r["derivs"] = step("derivs", lambda: test_derivs(ex, perp))
            else:
                r["errors"].append("Khong tim thay perpetual tuyen tinh BTC")

        if r["geo_blocked"]:
            r["status"] = "CO DAU HIEU CHAN VUNG"
        elif not spot:
            r["status"] = "KHONG CO SPOT PHU HOP"
    except Exception as e:  # noqa: BLE001
        r["status"] = "LOI"
        r["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")
    finally:
        try:
            ex.close()
        except Exception:  # noqa: BLE001
            pass
    return r


# ============================================================
# TIEU CHI DU DIEU KIEN (sinh ket luan tu du lieu)
# ============================================================
def spot_eligibility(r: dict) -> tuple[bool, list[str]]:
    why = []
    if r["geo_blocked"]:
        why.append("co dau hieu chan vung (403/451): doi IP/VPN hop le roi chay lai")
    if r["status"].startswith("KHONG CO TRONG CCXT") or r["status"].startswith("LOI"):
        return False, why + [f"trang thai: {r['status']}"]
    if not r["spot_symbol"]:
        return False, why + ["khong co BTC spot phu hop"]
    if r["spot_quote"] != "USDT":
        why.append(f"quote {r['spot_quote']} (khac USDT, khong dong nhat quote voi Design A)")
    pg = r["pagination"] or {}
    if not pg.get("ok"):
        why.append("phan trang khong dat: " + (pg.get("error") or
                   f"gaps={pg.get('gaps')}, dups={pg.get('dups')}, since_ok={pg.get('since_ok')}"))
    if r["hist_spot_max_ok_days"] < REQUIRED_HISTORY_DAYS:
        why.append(f"lich su 1 phut toi da {r['hist_spot_max_ok_days']}d < yeu cau {REQUIRED_HISTORY_DAYS}d")
    return not why, why


def perp_eligibility(r: dict) -> tuple[bool, list[str]]:
    why = []
    if not r["perp_symbol"]:
        return False, ["khong co perp tuyen tinh"]
    d = r["derivs"] or {}
    if r["hist_perp_max_ok_days"] < REQUIRED_HISTORY_DAYS:
        why.append(f"nen perp toi da {r['hist_perp_max_ok_days']}d")
    if d.get("funding_days_ok", 0) < REQUIRED_HISTORY_DAYS:
        why.append(f"funding history toi da {d.get('funding_days_ok', 0)}d")
    return not why, why


# ============================================================
# XUAT BAO CAO
# ============================================================
def flat_row(r: dict) -> dict:
    pg, ob, dv = r["pagination"] or {}, r["orderbook"] or {}, r["derivs"] or {}
    row = {
        "san": r["san"], "ma_ccxt": r["ma_ccxt"], "thoi_gian_kt": r["thoi_gian_kt"], "status": r["status"],
        "geo_blocked": r["geo_blocked"], "rate_limit_ms": r["rate_limit_ms"],
        "official_limit": r["official_limit"], "official_verified": r["official_verified"],
        "spot_symbol": r["spot_symbol"], "spot_quote": r["spot_quote"],
        "spot_max_returned": r["spot_max_returned"],
        "page_ok": pg.get("ok"), "page_size": pg.get("page_size"), "page_gaps": pg.get("gaps"),
        "page_dups": pg.get("dups"), "since_ok": pg.get("since_ok"),
        "sec_per_page": pg.get("sec_per_page"), "eta_1y_min": pg.get("eta_1y_min"),
        "hist_spot_max_ok_days": r["hist_spot_max_ok_days"],
        "ob_recommended_limit": ob.get("recommended_limit"), "ob_covers_10bps": ob.get("covers_target"),
        "perp_symbol": r["perp_symbol"], "perp_quote": r["perp_quote"],
        "hist_perp_max_ok_days": r["hist_perp_max_ok_days"],
        "has_funding_history": dv.get("has_funding_history"), "funding_days_ok": dv.get("funding_days_ok"),
        "has_oi_history": dv.get("has_oi_history"), "oi_days_ok": dv.get("oi_days_ok"),
        "errors": " | ".join(r["errors"]),
    }
    for h in r["hist_spot"]:
        row[f"hist_spot_{h['days']}d"] = h["status"]
    return row


def md_table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    out += ["| " + " | ".join("" if c is None else str(c) for c in row) + " |" for row in rows]
    return out + [""]


def yn(v) -> str:
    return "co" if v else ("khong" if v is not None else "n/a")


def export_reports(results: list[dict]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    flat = [flat_row(r) for r in results]
    fields = []
    for row in flat:                      # hop cac khoa, giu thu tu xuat hien
        fields += [k for k in row if k not in fields]
    with open(REPORT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    L = ["# Bao cao kha thi du lieu cac san (C3.01)", "",
         f"- Thoi gian chay (UTC): `{utc_now():%Y-%m-%d %H:%M:%S}`",
         f"- ccxt: `{ccxt.__version__}`  |  Timeframe: `{TIMEFRAME}`  |  Nguong do sau yeu cau: **{REQUIRED_HISTORY_DAYS} ngay**",
         "- Ket qua phu thuoc IP/vung cua may chay. Cac so 'chinh thuc' chi dung de doi chieu, xem muc 6.", ""]

    L += ["## 1. Spot: gioi han goi, phan trang, do sau lich su", ""]
    L += md_table(
        ["San", "Symbol", "Quote", "Official*", "Max thuc te", "since dung?", "Lo hong", "Trung", "s/trang",
         "ETA 1 nam (phut)", "Lich su OK toi da"],
        [[r["san"], f"`{r['spot_symbol']}`" if r["spot_symbol"] else "-", r["spot_quote"] or "-",
          r["official_limit"], r["spot_max_returned"],
          yn((r["pagination"] or {}).get("since_ok")), (r["pagination"] or {}).get("gaps"),
          (r["pagination"] or {}).get("dups"), (r["pagination"] or {}).get("sec_per_page"),
          (r["pagination"] or {}).get("eta_1y_min"), f"{r['hist_spot_max_ok_days']}d"] for r in results])
    L += ["*Official = so tu tai lieu, " + ("da xac nhan" if all(r["official_verified"] for r in results)
          else "CHUA xac nhan het, xem muc 6") + ".", ""]

    L += ["## 2. Chi tiet do sau lich su spot (nen 1 phut)", ""]
    L += md_table(["San"] + [f"{d}d" for d in HISTORY_DAYS],
                  [[r["san"]] + [(fmt_hist(next((h for h in r["hist_spot"] if h["days"] == d), {"status": "n/a", "detail": ""})))
                                 for d in HISTORY_DAYS] for r in results])

    L += [f"## 3. Order book (cho collector live, muc tieu phu +-{OB_TARGET_BPS} bps)", ""]
    rows = []
    for r in results:
        ob = r["orderbook"] or {}
        rows.append([r["san"], ob.get("recommended_limit"), yn(ob.get("covers_target")),
                     ob.get("rec_span_bid_bps"), ob.get("rec_span_ask_bps"),
                     ", ".join(str(x["limit"]) for x in ob.get("rows", []) if x["ok"]) or "-"])
    L += md_table(["San", "Limit khuyen nghi", "Phu duoc 10bps?", "Span bid (bps)", "Span ask (bps)", "Limit hop le"], rows)
    L += ["Neu 'Phu duoc 10bps' = khong, cot `depth_censored` trong collector se =True o san do. Ghi vao muc Han che.", ""]

    L += ["## 4. Perpetual va phai sinh (nang cao, RQ3)", ""]
    rows = []
    for r in results:
        dv = r["derivs"] or {}
        rows.append([r["san"], f"`{r['perp_symbol']}`" if r["perp_symbol"] else "-", r["perp_quote"] or "-",
                     f"{r['hist_perp_max_ok_days']}d", f"{yn(dv.get('has_funding_history'))} / {dv.get('funding_days_ok', 0)}d",
                     f"{yn(dv.get('has_oi_history'))} / {dv.get('oi_days_ok', 0)}d"])
    L += md_table(["San", "Perp symbol", "Quote", "Nen perp OK toi da", "Funding history (has / OK)", "OI history (has / OK)"], rows)

    L += ["## 5. Trang thai, rate limit, loi", ""]
    L += md_table(["San", "Ma ccxt", "Trang thai", "Rate limit (ms)", "Loi/ghi chu"],
                  [[r["san"], f"`{r['ma_ccxt']}`", r["status"], r["rate_limit_ms"],
                    "; ".join(r["errors"])[:300] or "-"] for r in results])

    L += ["## 6. Nguon so lieu chinh thuc can xac nhan", ""]
    L += md_table(["Ma ccxt", "Limit tai lieu", "URL"],
                  [[k, v["limit"], v["url"]] for k, v in OFFICIAL_LIMITS.items()])

    L += ["## 7. Ket luan sinh tu du lieu", ""]
    spot_ok = [(r["san"], spot_eligibility(r)) for r in results]
    L.append(f"**Du dieu kien lam Design A (spot, quote USDT, lich su >= {REQUIRED_HISTORY_DAYS}d, phan trang sach):** "
             + (", ".join(n for n, (ok, _) in spot_ok if ok) or "khong co san nao"))
    L.append("")
    L.append("**Chua dat, kem ly do:**")
    for n, (ok, why) in spot_ok:
        if not ok:
            L.append(f"- {n}: " + "; ".join(why))
    L.append("")
    perp_ok = [(r["san"], perp_eligibility(r)) for r in results if r["perp_symbol"] or not r["errors"]]
    L.append(f"**Du dieu kien perp cho RQ3 (nang cao):** " + (", ".join(n for n, (ok, _) in perp_ok if ok) or "khong co san nao"))
    L += ["", "**Quyet dinh chot san (dien tay sau khi ca nhom doc bang):** ...", ""]

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\nDa ghi: {REPORT_MD}, {REPORT_CSV}, {REPORT_JSON}")


# ============================================================
# MAIN
# ============================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C3.01 kiem tra kha thi du lieu san")
    ap.add_argument("--only", default="", help="danh sach id ccxt, cach nhau bang dau phay (vd binance,okx)")
    ap.add_argument("--skip-perp", action="store_true", help="bo qua phan perpetual/funding/OI")
    args = ap.parse_args(argv)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    targets = {n: i for n, i in EXCHANGES.items() if not only or i in only}
    if not targets:
        print("Khong co san nao khop --only")
        return 1

    print("=" * 60)
    print(f"C3.01 - kiem tra {len(targets)} san (ccxt {ccxt.__version__})")
    print("=" * 60)
    results = []
    for name, ex_id in targets.items():
        t0 = time.time()
        print(f"[{name}] dang kiem tra ...", flush=True)
        r = check_exchange(name, ex_id, args.skip_perp)
        print(f"[{name}] xong sau {time.time() - t0:.0f}s: {r['status']}", flush=True)
        results.append(r)
    export_reports(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())