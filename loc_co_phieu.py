"""
Bộ lọc cổ phiếu Việt Nam kết hợp Phân tích cơ bản (PTCB) + Phân tích kỹ thuật (PTKT).

Nguồn dữ liệu (API công khai, miễn phí):
  - Vietcap (VCI): danh sách công ty + ngành ICB, giá lịch sử, chỉ số tài chính TTM, BCKQKD theo quý
  - VNDirect: P/E, P/B, tỷ suất cổ tức, beta... cập nhật theo ngày

Cách dùng:
  python loc_co_phieu.py                 # lọc toàn thị trường theo cau_hinh.json
  python loc_co_phieu.py --san HOSE      # chỉ sàn HOSE
  python loc_co_phieu.py --ma FPT,HPG    # phân tích chi tiết vài mã
  python loc_co_phieu.py --lam-moi       # bỏ qua cache, tải lại toàn bộ
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import requests

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CACHE = ROOT / "cache"
OUT = ROOT / "ket_qua"

VCI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
}
VCI_IQ = "https://iq.vietcap.com.vn/api/iq-insight-service"
VCI_CHART = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
DNSE_CHART = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"
VND_RATIOS = "https://api-finfo.vndirect.com.vn/v4/ratios/latest"

# Chỉ tiêu doanh thu khác nhau theo loại hình doanh nghiệp
REVENUE_FIELD = {"NH": "isb38", "BH": "isi64", "CK": "isa3", "CT": "isa3"}
PROFIT_FIELD = "isa22"  # LNST của cổ đông công ty mẹ

def now_vn() -> datetime:
    return datetime.now(VN_TZ)


_session = requests.Session()
_session.headers.update(VCI_HEADERS)


# ----------------------------------------------------------------------------
# Tải dữ liệu + cache
# ----------------------------------------------------------------------------
LOI_NGUON: Counter = Counter()  # (máy chủ, mã lỗi) -> số lần, để in vào nhật ký


def http(method: str, url: str, retries: int = 4, **kw):
    host, err = urlparse(url).netloc, "?"
    for i in range(retries):
        try:
            r = _session.request(method, url, timeout=30, **kw)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except (requests.RequestException, ValueError) as e:
            err = type(e).__name__
        time.sleep(1.5 * (i + 1))
    LOI_NGUON[(host, err)] += 1
    return None


def bao_loi_nguon(buoc: str):
    if LOI_NGUON:
        print(f"   ! Lỗi nguồn dữ liệu ({buoc}): " + ", ".join(f"{h} {e} ×{n}" for (h, e), n in LOI_NGUON.most_common()))
        LOI_NGUON.clear()


def cached(key: str, ttl_hours: float, fetch, refresh: bool = False):
    """Đọc cache còn hạn; nếu hết hạn thì tải mới. Tải lỗi thì dùng lại bản cũ (nếu có) thay vì bỏ trống."""
    path = CACHE / f"{key}.json"
    old = None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    if not refresh and old is not None and time.time() - path.stat().st_mtime < ttl_hours * 3600:
        return old
    data = fetch()
    if data is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    return old


def fetch_universe():
    d = http("GET", f"{VCI_IQ}/v2/company/search-bar?language=1")
    if not d:
        # Dự phòng: danh sách lưu sẵn trong kho (ít thay đổi; cập nhật khi chạy trên máy cá nhân)
        snap = ROOT / "danh_sach_ma.json"
        return json.loads(snap.read_text(encoding="utf-8")) if snap.exists() else None
    return [
        {
            "ma": x["code"],
            "ten": x.get("shortName") or x.get("name"),
            "san": x.get("floor"),
            "loai": x.get("comTypeCode"),
            "nganh": (x.get("icbLv2") or {}).get("name"),
            "nganh_chi_tiet": (x.get("icbLv3") or {}).get("name"),
        }
        for x in d.get("data", [])
    ]


def fetch_ohlc(sym: str, bars: int = 400):
    d = http("POST", VCI_CHART, json={"timeFrame": "ONE_DAY", "symbols": [sym],
                                      "to": int(time.time()), "countBack": bars})
    if d and d[0].get("c"):
        x = d[0]
        return {"t": [int(v) for v in x["t"]], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x["v"]}
    # Dự phòng: DNSE (giá theo nghìn đồng)
    now = int(time.time())
    d = http("GET", f"{DNSE_CHART}?from={now - int(bars * 1.6) * 86400}&to={now}&symbol={sym}&resolution=1D")
    if d and d.get("c"):
        k = 1000 if np.median(d["c"]) < 1000 else 1
        return {"t": d["t"], "o": [v * k for v in d["o"]], "h": [v * k for v in d["h"]],
                "l": [v * k for v in d["l"]], "c": [v * k for v in d["c"]], "v": d["v"]}
    return None


def fetch_fundamental(sym: str):
    stats = http("GET", f"{VCI_IQ}/v1/company/{sym}/statistics-financial")
    inc = http("GET", f"{VCI_IQ}/v1/company/{sym}/financial-statement?section=INCOME_STATEMENT")
    if not stats or not inc:
        return None
    rows = stats.get("data") or []
    last = max(rows, key=lambda r: (r.get("yearReport") or 0, r.get("quarter") or 0)) if rows else {}
    quarters = [
        {k: v for k, v in q.items() if k in ("yearReport", "lengthReport", "isa3", "isb38", "isi64", "isa22")}
        for q in (inc.get("data") or {}).get("quarters", [])
        if q.get("lengthReport") in (1, 2, 3, 4)
    ]
    return {"ratio": last, "quarters": quarters}


def fetch_vnd_ratios(symbols: list[str]):
    """P/E, P/B, cổ tức, vốn hoá, beta theo giá hiện tại (VNDirect), gọi theo lô 40 mã."""
    codes = "MARKETCAP,PRICE_TO_EARNINGS,PRICE_TO_BOOK,DIVIDEND_YIELD,BETA"
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 40):
        chunk = ",".join(symbols[i:i + 40])
        try:
            r = requests.get(f"{VND_RATIOS}?filter=ratioCode:{codes}&where=code:{chunk}"
                             f"&order=reportDate&size=1000&fields=code,ratioCode,value",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            for x in r.json().get("data", []):
                out.setdefault(x["code"], {})[x["ratioCode"]] = x["value"]
        except (requests.RequestException, ValueError):
            LOI_NGUON[("api-finfo.vndirect.com.vn", "lỗi kết nối")] += 1
            continue
    return out or None


def parallel(fn, items, workers, label):
    res, n = {}, len(items)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for i, f in enumerate(as_completed(futs), 1):
            res[futs[f]] = f.result()
            if i % 50 == 0 or i == n:
                print(f"  {label}: {i}/{n}", end="\r", flush=True)
    print()
    return res


# ----------------------------------------------------------------------------
# Chỉ báo kỹ thuật
# ----------------------------------------------------------------------------
def to_frame(raw) -> pd.DataFrame:
    df = pd.DataFrame({"open": raw["o"], "high": raw["h"], "low": raw["l"],
                       "close": raw["c"], "volume": raw["v"]},
                      index=pd.to_datetime(raw["t"], unit="s"), dtype=float)
    return df[~df.index.duplicated()].sort_index()


def rsi(close: pd.Series, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def ret(close: pd.Series, n: int):
    return close.iloc[-1] / close.iloc[-n - 1] - 1 if len(close) > n else np.nan


def technical(df: pd.DataFrame) -> dict:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    ma20, ma50, ma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    r = rsi(c)
    win = min(252, len(c))
    hi52, lo52 = h.iloc[-win:].max(), l.iloc[-win:].min()
    vol20, vol50 = v.rolling(20).mean(), v.rolling(50).mean()
    last = c.iloc[-1]

    cross_window = (ma50 > ma200).iloc[-20:]
    golden = bool(cross_window.iloc[-1] and not cross_window.all()) if ma200.notna().iloc[-20] else False
    breakout = bool(last >= c.iloc[-21:-1].max() and v.iloc[-1] > 1.5 * vol20.iloc[-2])

    return {
        "gia": last,
        "thay_doi": last / c.iloc[-2] - 1,
        "gtgd_tb20": float((c * v).iloc[-20:].mean()),
        "so_phien": len(c),
        "ma20": ma20.iloc[-1], "ma50": ma50.iloc[-1], "ma200": ma200.iloc[-1],
        "ma200_tang": bool(ma200.iloc[-1] > ma200.iloc[-21]) if len(c) > 220 else False,
        "pct_ma50": last / ma50.iloc[-1] - 1,
        "pct_ma200": last / ma200.iloc[-1] - 1 if not math.isnan(ma200.iloc[-1]) else np.nan,
        "rsi14": r.iloc[-1],
        "macd": macd.iloc[-1], "macd_signal": signal.iloc[-1], "macd_hist": hist.iloc[-1],
        "macd_hist_tang": bool(hist.iloc[-1] > hist.iloc[-4]),
        "atr_pct": atr.iloc[-1] / last,
        "cach_dinh_52t": last / hi52 - 1,
        "tu_day_52t": last / lo52 - 1,
        "vol5_vs_vol20": v.iloc[-5:].mean() / vol20.iloc[-1] if vol20.iloc[-1] else np.nan,
        "vol20_vs_vol50": vol20.iloc[-1] / vol50.iloc[-1] if vol50.iloc[-1] else np.nan,
        "r1t": ret(c, 21), "r3t": ret(c, 63), "r6t": ret(c, 126), "r9t": ret(c, 189), "r12t": ret(c, 252),
        "golden_cross_20p": golden,
        "breakout_20p": breakout,
    }


# ----------------------------------------------------------------------------
# Chỉ số cơ bản
# ----------------------------------------------------------------------------
def _prev_q(y, q, k):
    idx = y * 4 + (q - 1) - k
    return idx // 4, idx % 4 + 1


def _growth(cur, base):
    if cur is None or base is None or base <= 0 or (isinstance(cur, float) and math.isnan(cur)):
        return np.nan
    return cur / base - 1


def fundamental(raw: dict, loai: str) -> dict:
    ratio = raw.get("ratio") or {}
    qmap = {(q["yearReport"], q["lengthReport"]): q for q in raw.get("quarters", [])}
    out = {"ky_bctc": None}
    if qmap:
        y, q = max(qmap)
        out["ky_bctc"] = f"Q{q}/{y}"
        rev_f = REVENUE_FIELD.get(loai, "isa3")

        def series(field, start, n):
            vals = []
            for k in range(start, start + n):
                row = qmap.get(_prev_q(y, q, k))
                if row is None or row.get(field) is None:
                    return None
                vals.append(row[field])
            return vals

        np8 = series(PROFIT_FIELD, 0, 8)
        rv8 = series(rev_f, 0, 8)
        np4 = series(PROFIT_FIELD, 0, 4)
        out["ln_ttm"] = sum(np4) if np4 else np.nan
        out["tt_ln_ttm"] = _growth(sum(np8[:4]), sum(np8[4:])) if np8 else np.nan
        out["tt_dt_ttm"] = _growth(sum(rv8[:4]), sum(rv8[4:])) if rv8 else np.nan
        last, yoy = qmap.get((y, q), {}), qmap.get(_prev_q(y, q, 4), {})
        out["tt_ln_quy"] = _growth(last.get(PROFIT_FIELD), yoy.get(PROFIT_FIELD))
        out["tt_dt_quy"] = _growth(last.get(rev_f), yoy.get(rev_f))
        out["lai_4_quy"] = bool(np4 and all(x > 0 for x in np4))
        out["chuyen_lai"] = bool((last.get(PROFIT_FIELD) or 0) > 0 >= (yoy.get(PROFIT_FIELD) or 0) and yoy)

    g = lambda k: ratio.get(k) if ratio.get(k) not in (None, 0, 0.0) else np.nan
    out.update({
        "roe": g("roe"), "roa": g("roa"),
        "no_vay_vcsh": ratio.get("debtPerEquity", np.nan) if loai != "NH" else np.nan,
        "thanh_toan_hh": g("currentRatio"),
        "bien_ln_gop": g("grossMargin"), "bien_ln_rong": g("afterTaxProfitMargin"),
        "npl": g("npl"), "nim": g("netInterestMargin"), "casa": g("casaRatio"),
        "pe_quy": g("pe"), "pb_quy": g("pb"),
    })
    return out


# ----------------------------------------------------------------------------
# Chấm điểm
# ----------------------------------------------------------------------------
def nz(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def step(x, rules):
    """rules: [(ngưỡng, điểm), ...] theo thứ tự giảm dần; trả về điểm của ngưỡng đầu tiên đạt."""
    if not nz(x):
        return 0
    for thr, pts in rules:
        if x >= thr:
            return pts
    return 0


def score_fundamental(r: dict) -> tuple[float, list, list, list]:
    good, warn = [], []
    parts = [
        ("ROE", step(r.get("roe"), [(0.20, 20), (0.15, 15), (0.10, 8)]), 20),
        ("Tăng trưởng LN 4 quý", step(r.get("tt_ln_ttm"), [(0.30, 20), (0.15, 15), (0.05, 8), (0.0001, 4)]), 20),
        ("Tăng trưởng LN quý", step(r.get("tt_ln_quy"), [(0.30, 15), (0.15, 10), (0.0001, 5)]), 15),
        ("Tăng trưởng doanh thu", step(r.get("tt_dt_ttm"), [(0.20, 10), (0.10, 7), (0.0001, 3)]), 10),
    ]

    pe, pe_ng, peg = r.get("pe"), r.get("pe_nganh"), r.get("peg")
    v = 0
    if nz(pe) and pe > 0 and nz(pe_ng):
        v = 10 if pe <= 0.8 * pe_ng else 6 if pe <= pe_ng else 3 if pe <= 1.2 * pe_ng else 0
    parts.append(("P/E so với ngành", v, 10))
    v = 0
    if nz(peg) and peg > 0:
        v = 10 if peg <= 0.7 else 7 if peg <= 1 else 3 if peg <= 1.5 else 0
    parts.append(("PEG", v, 10))

    loai = r.get("loai")
    h = 0
    if loai == "NH":
        h += 8 if nz(r.get("npl")) and r["npl"] <= 0.015 else 4 if nz(r.get("npl")) and r["npl"] <= 0.025 else 0
        h += 4 if nz(r.get("nim")) and r["nim"] >= 0.03 else 2 if nz(r.get("nim")) and r["nim"] >= 0.025 else 0
        h += 3 if r.get("lai_4_quy") else 0
    else:
        d = r.get("no_vay_vcsh")
        lim = (0.5, 1.0, 1.5) if loai == "CT" else (1.0, 1.5, 2.0)
        h += 8 if nz(d) and d <= lim[0] else 5 if nz(d) and d <= lim[1] else 2 if nz(d) and d <= lim[2] else 0
        h += 4 if loai != "CT" or (nz(r.get("thanh_toan_hh")) and r["thanh_toan_hh"] >= 1.2) else 0
        h += 3 if r.get("lai_4_quy") else 0
    parts.append(("Sức khoẻ tài chính", h, 15))
    s = sum(p[1] for p in parts)

    # Diễn giải
    if nz(r.get("roe")) and r["roe"] >= 0.15: good.append(f"ROE {r['roe']:.0%}")
    if nz(r.get("tt_ln_ttm")) and r["tt_ln_ttm"] >= 0.15: good.append(f"LN 4 quý +{r['tt_ln_ttm']:.0%}")
    if nz(r.get("tt_ln_quy")) and r["tt_ln_quy"] >= 0.25: good.append(f"LN quý gần nhất +{r['tt_ln_quy']:.0%}")
    if r.get("chuyen_lai"): good.append("Quý gần nhất chuyển lỗ thành lãi")
    if nz(peg) and 0 < peg <= 1: good.append(f"PEG {peg:.2f}")
    if nz(pe) and nz(pe_ng) and 0 < pe <= 0.8 * pe_ng: good.append(f"P/E {pe:.1f} thấp hơn ngành ({pe_ng:.1f})")
    if nz(r.get("tt_ln_ttm")) and r["tt_ln_ttm"] < 0: warn.append(f"LN 4 quý {r['tt_ln_ttm']:.0%}")
    if nz(r.get("tt_ln_quy")) and r["tt_ln_quy"] < -0.1: warn.append(f"LN quý {r['tt_ln_quy']:.0%}")
    if not nz(pe) or pe <= 0: warn.append("P/E âm/không có")
    elif nz(pe_ng) and pe > 1.5 * pe_ng: warn.append(f"P/E {pe:.1f} cao hơn ngành")
    if loai == "CT" and nz(r.get("no_vay_vcsh")) and r["no_vay_vcsh"] > 1.5: warn.append(f"Nợ vay/VCSH {r['no_vay_vcsh']:.1f}")
    if loai == "NH" and nz(r.get("npl")) and r["npl"] > 0.025: warn.append(f"Nợ xấu {r['npl']:.1%}")
    return s, good, warn, parts


def score_technical(r: dict) -> tuple[float, list, list, list]:
    good, warn = [], []
    c = r["gia"]
    trend = (5 if c > r["ma20"] else 0) + (10 if c > r["ma50"] else 0)         + (5 if nz(r["ma200"]) and c > r["ma200"] else 0)         + (10 if nz(r["ma200"]) and r["ma50"] > r["ma200"] else 0) + (5 if r["ma200_tang"] else 0)
    rs_ = r.get("rsi14")
    mom = (10 if nz(rs_) and 50 <= rs_ <= 70 else 5 if nz(rs_) and 70 < rs_ <= 80 else 3 if nz(rs_) and 40 <= rs_ < 50 else 0)         + (8 if r["macd"] > r["macd_signal"] else 0) + (7 if r["macd"] > 0 else 0)
    parts = [
        ("Xu hướng (MA)", trend, 35),
        ("Động lượng (RSI, MACD)", mom, 25),
        ("Sức mạnh giá RS", round(0.25 * (r.get("rs") or 0), 1), 25),
        ("Gần đỉnh 52 tuần", step(r["cach_dinh_52t"], [(-0.10, 10), (-0.20, 6), (-0.30, 3)]), 10),
        ("Dòng tiền (khối lượng)", 5 if nz(r["vol20_vs_vol50"]) and r["vol20_vs_vol50"] > 1 else 0, 5),
    ]
    s = sum(p[1] for p in parts)

    if nz(r["ma200"]) and c > r["ma50"] > r["ma200"] and r["ma200_tang"]: good.append("Xu hướng tăng (Giá>MA50>MA200)")
    if (r.get("rs") or 0) >= 80: good.append(f"Sức mạnh giá RS {r['rs']:.0f}")
    if r["cach_dinh_52t"] >= -0.05: good.append("Sát đỉnh 52 tuần")
    if r["breakout_20p"]: good.append("Vượt đỉnh 20 phiên kèm vol lớn")
    if r["golden_cross_20p"]: good.append("MA50 cắt lên MA200 (20 phiên)")
    if r["macd"] > r["macd_signal"] and r["macd_hist_tang"]: good.append("MACD cắt lên/hist tăng")
    if nz(rs_) and rs_ > 75: warn.append(f"RSI quá mua {rs_:.0f}")
    if nz(rs_) and rs_ < 35: warn.append(f"RSI yếu {rs_:.0f}")
    if c < r["ma50"]: warn.append("Giá dưới MA50")
    if nz(r["ma200"]) and c < r["ma200"]: warn.append("Giá dưới MA200")
    if r["pct_ma50"] > 0.15: warn.append(f"Giá cao hơn MA50 {r['pct_ma50']:.0%} (dễ điều chỉnh)")
    return s, good, warn, parts


def grade(x):
    return "A" if x >= 75 else "B" if x >= 60 else "C" if x >= 45 else "D"


# ----------------------------------------------------------------------------
# Quy trình chính
# ----------------------------------------------------------------------------
def run(cfg: dict, symbols: list[str] | None, refresh: bool):
    t0 = time.time()
    ttl = cfg["cache_gio"]
    workers = cfg.get("so_luong_tai_song_song", 8)
    today = now_vn().strftime("%Y-%m-%d")

    # Xoá giá của các ngày trước (giá được cache theo ngày)
    for d in (CACHE / "gia").glob("*"):
        if d.is_dir() and d.name != today:
            shutil.rmtree(d, ignore_errors=True)

    print("1) Tải danh sách công ty...")
    uni = cached("danh_sach", ttl["danh_sach"], fetch_universe, refresh)
    bao_loi_nguon("danh sách")
    if not uni:
        sys.exit("Không tải được danh sách công ty (Vietcap) và không có bản dự phòng danh_sach_ma.json.")
    snap = ROOT / "danh_sach_ma.json"
    if len(uni) > 1000 and (not snap.exists() or time.time() - snap.stat().st_mtime > 7 * 86400):
        snap.write_text(json.dumps(uni, ensure_ascii=False, indent=0), encoding="utf-8")
    info = pd.DataFrame(uni).drop_duplicates("ma").set_index("ma")
    m = info["san"].isin(cfg["san"]) & info["loai"].isin(cfg["loai_doanh_nghiep"]) & (info.index.str.len() == 3)
    tickers = sorted(info.index[m])
    if symbols:
        missing = [s for s in symbols if s not in info.index]
        if missing:
            print("   Không tìm thấy mã:", ", ".join(missing))
        symbols = [s for s in symbols if s in info.index]
        tickers = sorted(set(tickers) | set(symbols))
    print(f"   {len(tickers)} mã cần xét")

    print("2) Tải giá lịch sử...")
    px = parallel(lambda s: cached(f"gia/{today}/{s}", ttl["gia"], lambda: fetch_ohlc(s), refresh),
                  tickers, workers, "giá")
    vnindex = cached(f"gia/{today}/VNINDEX", ttl["gia"], lambda: fetch_ohlc("VNINDEX"), refresh)
    print(f"   {sum(1 for v in px.values() if v)}/{len(tickers)} mã có giá")
    bao_loi_nguon("giá")

    lk = cfg["loc_thanh_khoan"]
    tech, frames = {}, {}
    for s in tickers:
        if not px.get(s):
            continue
        df = to_frame(px[s])
        if len(df) < 60:
            continue
        frames[s] = df
        tech[s] = technical(df)
    # Mã chỉ định qua --ma luôn được giữ lại; các mã khác phải đạt thanh khoản (dùng làm mặt bằng so sánh RS, P/E ngành)
    tech = {s: t for s, t in tech.items()
            if (symbols and s in symbols)
            or (t["gtgd_tb20"] >= lk["gtgd_tb20_toi_thieu_ty"] * 1e9
                and t["gia"] >= lk["gia_toi_thieu"] and t["so_phien"] >= lk["so_phien_toi_thieu"])}
    print(f"   {len(tech)} mã đạt thanh khoản (GTGD TB20 ≥ {lk['gtgd_tb20_toi_thieu_ty']} tỷ, giá ≥ {lk['gia_toi_thieu']:,}đ)")
    if not tech:
        sys.exit("Không có mã nào đủ dữ liệu.")
    if not symbols and len(tech) < 30:
        sys.exit(f"Chỉ {len(tech)} mã có dữ liệu – nguồn giá có thể đang lỗi hoặc chặn truy cập; dừng để không ghi đè kết quả cũ.")

    # RS rating (kiểu IBD): 40% hiệu suất 3T + 20% 6T + 20% 9T + 20% 12T, xếp hạng phần trăm 1–99
    t = pd.DataFrame(tech).T
    perf = 0.4 * t["r3t"].astype(float).fillna(0) + 0.2 * t["r6t"].astype(float).fillna(0) \
        + 0.2 * t["r9t"].astype(float).fillna(0) + 0.2 * t["r12t"].astype(float).fillna(0)
    rs_rank = (perf.rank(pct=True) * 98 + 1).round()
    vni3m, vni = np.nan, None
    if vnindex:
        vni = to_frame(vnindex)["close"]
        vni3m = ret(vni, 63)

    print("3) Tải dữ liệu tài chính...")
    names = sorted(tech)
    targets = [s for s in symbols if s in tech] if symbols else names
    fund_raw = parallel(lambda s: cached(f"co_ban/{s}", ttl["co_ban"], lambda: fetch_fundamental(s), refresh),
                        targets, workers, "BCTC")
    key = hashlib.md5(",".join(names).encode()).hexdigest()[:10]
    vnd = cached(f"vnd/{today}_{key}", ttl["gia"], lambda: fetch_vnd_ratios(names), refresh) or {}
    print(f"   {sum(1 for v in fund_raw.values() if v)}/{len(targets)} mã có BCTC, {len(vnd)} mã có định giá VNDirect")
    bao_loi_nguon("tài chính")

    # P/E trung vị theo ngành tính trên toàn bộ mã đạt thanh khoản (cần ≥ 3 mã, nếu không dùng trung vị thị trường).
    # Nếu VNDirect lỗi thì dùng P/E cuối quý từ Vietcap của các mã đã có BCTC.
    def pe_of(s):
        v = vnd.get(s, {}).get("PRICE_TO_EARNINGS")
        if v is None and fund_raw.get(s):
            v = (fund_raw[s].get("ratio") or {}).get("pe")
        return v
    pe_all = pd.DataFrame([{"nganh": info.at[s, "nganh"], "pe": pe_of(s)} for s in names])
    pe_all["pe"] = pd.to_numeric(pe_all["pe"], errors="coerce")
    pe_all = pe_all[(pe_all["pe"] > 0) & (pe_all["pe"] < 100)]
    med_all = pe_all["pe"].median()
    med = pe_all.groupby("nganh")["pe"].agg(["median", "count"])

    rows = []
    for s in targets:
        loai = info.at[s, "loai"]
        r = {"ma": s, "ten": info.at[s, "ten"], "san": info.at[s, "san"], "nganh": info.at[s, "nganh"], "loai": loai}
        r.update(tech[s])
        r["rs"] = float(rs_rank.get(s, np.nan))
        r["vuot_vnindex_3t"] = r["r3t"] - vni3m if nz(r["r3t"]) and nz(vni3m) else np.nan
        r.update(fundamental(fund_raw.get(s) or {}, loai))
        v = vnd.get(s, {})
        r["pe"] = v.get("PRICE_TO_EARNINGS", r.get("pe_quy", np.nan))
        r["pb"] = v.get("PRICE_TO_BOOK", r.get("pb_quy", np.nan))
        r["co_tuc"] = v.get("DIVIDEND_YIELD", np.nan)
        r["von_hoa_ty"] = v.get("MARKETCAP", np.nan) / 1e9 if v.get("MARKETCAP") else np.nan
        r["beta"] = v.get("BETA", np.nan)
        rows.append(r)
    df = pd.DataFrame(rows)
    df["pe_nganh"] = df["nganh"].map(lambda n: med.at[n, "median"] if n in med.index and med.at[n, "count"] >= 3 else med_all)
    # Tăng trưởng dùng cho PEG giới hạn 50% để tránh PEG ảo do LN tăng đột biến từ nền thấp
    g = df["tt_ln_ttm"].clip(upper=0.5)
    df["peg"] = np.where((df["pe"] > 0) & (g > 0), df["pe"] / (g * 100), np.nan)

    w = cfg["trong_so"]
    out = []
    for r in df.to_dict("records"):
        f, fg, fw, fp = score_fundamental(r)
        k, kg, kw, kp = score_technical(r)
        r["phan_cb"], r["phan_kt"] = fp, kp
        r["diem_cb"], r["diem_kt"] = round(f, 1), round(k, 1)
        r["diem_tong"] = round(w["co_ban"] * f + w["ky_thuat"] * k, 1)
        r["xep_loai"] = grade(r["diem_tong"])
        r["diem_manh"] = "; ".join(fg + kg)
        r["luu_y"] = "; ".join(fw + kw)
        out.append(r)
    df = pd.DataFrame(out).sort_values("diem_tong", ascending=False).reset_index(drop=True)

    top_cfg = cfg["loc_top"]
    m = (df["diem_cb"] >= top_cfg["diem_co_ban_toi_thieu"]) & (df["diem_kt"] >= top_cfg["diem_ky_thuat_toi_thieu"])
    if top_cfg.get("yeu_cau_gia_tren_ma50"):
        m &= df["gia"] > df["ma50"]
    if top_cfg.get("yeu_cau_lai_ttm_duong"):
        m &= df["ln_ttm"].fillna(-1) > 0
    top = df[m].head(top_cfg["so_ma_hien_thi"])

    print(f"\nHoàn tất trong {time.time() - t0:.0f}s. VN-Index 3 tháng: {vni3m:+.1%}" if nz(vni3m) else "")

    # Chuỗi giá 200 phiên (căn theo ngày giao dịch của VN-Index) để vẽ biểu đồ trên trang web
    axis = vni.index[-200:] if vni is not None else frames[df["ma"].iloc[0]].index[-200:]
    closes = {s: [None if math.isnan(x) else round(x) for x in frames[s]["close"].reindex(axis).ffill()]
              for s in df["ma"]}
    ctx = {
        "vni3m": vni3m,
        "vnindex": [round(x, 2) for x in vni.reindex(axis).ffill()] if vni is not None else [],
        "ngay": [d.strftime("%Y-%m-%d") for d in axis],
        "gia_200": closes,
        "so_ma_xet": len(tickers), "so_ma_thanh_khoan": len(names), "so_ma_top": len(top),
        "cfg": cfg,
    }
    return df, top, ctx


# ----------------------------------------------------------------------------
# Xuất kết quả
# ----------------------------------------------------------------------------
COLUMNS = [
    ("ma", "Mã", None), ("ten", "Tên", None), ("san", "Sàn", None), ("nganh", "Ngành", None),
    ("gia", "Giá", "#,##0"), ("diem_tong", "Điểm tổng", "0.0"), ("xep_loai", "Hạng", None),
    ("diem_cb", "Điểm PTCB", "0.0"), ("diem_kt", "Điểm PTKT", "0.0"),
    ("gtgd_tb20_ty", "GTGD TB20 (tỷ)", "#,##0.0"), ("von_hoa_ty", "Vốn hoá (tỷ)", "#,##0"),
    ("pe", "P/E", "0.0"), ("pe_nganh", "P/E ngành", "0.0"), ("pb", "P/B", "0.00"), ("peg", "PEG", "0.00"),
    ("roe", "ROE", "0.0%"), ("roa", "ROA", "0.0%"),
    ("tt_ln_ttm", "TT LN 4Q", "0.0%"), ("tt_ln_quy", "TT LN quý", "0.0%"),
    ("tt_dt_ttm", "TT DT 4Q", "0.0%"), ("tt_dt_quy", "TT DT quý", "0.0%"),
    ("no_vay_vcsh", "Nợ vay/VCSH", "0.00"), ("npl", "Nợ xấu (NH)", "0.00%"), ("co_tuc", "Cổ tức", "0.0%"),
    ("rs", "RS (1-99)", "0"), ("rsi14", "RSI14", "0.0"), ("pct_ma50", "So MA50", "0.0%"),
    ("pct_ma200", "So MA200", "0.0%"), ("cach_dinh_52t", "Cách đỉnh 52T", "0.0%"),
    ("r1t", "Hiệu suất 1T", "0.0%"), ("r3t", "Hiệu suất 3T", "0.0%"), ("r6t", "Hiệu suất 6T", "0.0%"),
    ("vuot_vnindex_3t", "Vượt VNI 3T", "0.0%"), ("atr_pct", "ATR%", "0.0%"), ("beta", "Beta", "0.00"),
    ("diem_manh", "Điểm mạnh", None), ("luu_y", "Lưu ý / rủi ro", None), ("ky_bctc", "Kỳ BCTC", None),
]

GIAI_THICH = [
    ("PHÂN TÍCH CƠ BẢN (100 điểm)", ""),
    ("ROE (TTM) – 20đ", "≥20%: 20 | ≥15%: 15 | ≥10%: 8"),
    ("Tăng trưởng LNST cty mẹ 4 quý gần nhất so với 4 quý trước – 20đ", "≥30%: 20 | ≥15%: 15 | ≥5%: 8 | >0: 4"),
    ("Tăng trưởng LNST quý gần nhất so với cùng kỳ – 15đ", "≥30%: 15 | ≥15%: 10 | >0: 5  (tiêu chí C của CANSLIM)"),
    ("Tăng trưởng doanh thu 4 quý – 10đ", "≥20%: 10 | ≥10%: 7 | >0: 3  (NH: tổng thu nhập HĐ; BH: DT thuần KD bảo hiểm)"),
    ("P/E so với trung vị ngành (ICB cấp 2) – 10đ", "≤0.8x: 10 | ≤1x: 6 | ≤1.2x: 3"),
    ("PEG = P/E ÷ tăng trưởng LN 4 quý (%, tối đa 50%) – 10đ", "≤0.7: 10 | ≤1: 7 | ≤1.5: 3"),
    ("Sức khoẻ tài chính – 15đ", "DN thường: Nợ vay/VCSH ≤0.5: 8, ≤1: 5, ≤1.5: 2; Thanh toán hiện hành ≥1.2: 4; Lãi 4 quý liên tiếp: 3\n"
                                 "Ngân hàng: Nợ xấu ≤1.5%: 8, ≤2.5%: 4; NIM ≥3%: 4, ≥2.5%: 2; Lãi 4 quý: 3\n"
                                 "CK/Bảo hiểm: Nợ vay/VCSH ≤1: 8, ≤1.5: 5, ≤2: 2; +4; Lãi 4 quý: 3"),
    ("", ""),
    ("PHÂN TÍCH KỸ THUẬT (100 điểm)", ""),
    ("Xu hướng – 35đ", "Giá>MA20: 5 | Giá>MA50: 10 | Giá>MA200: 5 | MA50>MA200: 10 | MA200 đi lên (so với 20 phiên trước): 5"),
    ("Động lượng – 25đ", "RSI14 50–70: 10 (70–80: 5, 40–50: 3) | MACD>Signal: 8 | MACD>0: 7"),
    ("Sức mạnh giá tương đối RS – 25đ", "RS 1–99 = xếp hạng phần trăm của (40%·HS 3T + 20%·6T + 20%·9T + 20%·12T) trong các mã được lọc; điểm = RS × 0.25"),
    ("Vị trí so với đỉnh 52 tuần – 10đ", "Cách đỉnh ≤10%: 10 | ≤20%: 6 | ≤30%: 3"),
    ("Dòng tiền – 5đ", "KL TB 20 phiên > KL TB 50 phiên: 5"),
    ("", ""),
    ("ĐIỂM TỔNG", "= 50% PTCB + 50% PTKT (chỉnh trong cau_hinh.json). Hạng A ≥75, B ≥60, C ≥45, D <45"),
    ("DANH SÁCH TOP", "Điểm PTCB ≥55, PTKT ≥55, giá trên MA50, LN 4 quý dương (chỉnh trong cau_hinh.json)"),
    ("", ""),
    ("LƯU Ý", "Kết quả là bộ lọc định lượng dựa trên dữ liệu công khai, có thể chậm hoặc sai sót. "
              "Đây không phải khuyến nghị mua/bán. Hãy tự kiểm tra BCTC, tin tức, quản trị doanh nghiệp và quản lý rủi ro trước khi đầu tư."),
]


def export(df: pd.DataFrame, top: pd.DataFrame):
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    OUT.mkdir(exist_ok=True)
    stamp = now_vn().strftime("%Y-%m-%d_%H%M")
    path = OUT / f"loc_co_phieu_{stamp}.xlsx"

    def view(d):
        d = d.assign(gtgd_tb20_ty=d["gtgd_tb20"] / 1e9)
        return d[[c for c, _, _ in COLUMNS]].rename(columns={c: n for c, n, _ in COLUMNS})

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        view(top).to_excel(xw, sheet_name="Top co phieu", index=False)
        view(df).to_excel(xw, sheet_name="Xep hang day du", index=False)
        pd.DataFrame(GIAI_THICH, columns=["Tiêu chí", "Cách chấm"]).to_excel(xw, sheet_name="Giai thich", index=False)

        head = PatternFill("solid", fgColor="1F4E78")
        for name in ("Top co phieu", "Xep hang day du"):
            ws = xw.sheets[name]
            ws.freeze_panes = "C2"
            ws.auto_filter.ref = ws.dimensions
            for j, (_, label, fmt) in enumerate(COLUMNS, 1):
                col = get_column_letter(j)
                cell = ws[f"{col}1"]
                cell.fill, cell.font = head, Font(color="FFFFFF", bold=True)
                cell.alignment = Alignment(wrap_text=True, vertical="center")
                ws.column_dimensions[col].width = {"Tên": 22, "Ngành": 18, "Điểm mạnh": 60, "Lưu ý / rủi ro": 45}.get(label, 11)
                if fmt:
                    for c in ws[col][1:]:
                        c.number_format = fmt
                if label in ("Điểm tổng", "Điểm PTCB", "Điểm PTKT", "RS (1-99)"):
                    ws.conditional_formatting.add(f"{col}2:{col}{ws.max_row}", ColorScaleRule(
                        start_type="num", start_value=20, start_color="F8696B",
                        mid_type="num", mid_value=55, mid_color="FFEB84",
                        end_type="num", end_value=85, end_color="63BE7B"))
            ws.row_dimensions[1].height = 32
        ws = xw.sheets["Giai thich"]
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 60, 120
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")

    csv = OUT / f"loc_co_phieu_{stamp}.csv"
    view(df).to_csv(csv, index=False, encoding="utf-8-sig")
    return path


WEB_FIELDS = [
    "ma", "ten", "san", "nganh", "loai", "gia", "thay_doi", "diem_tong", "diem_cb", "diem_kt", "xep_loai",
    "gtgd_tb20", "von_hoa_ty", "pe", "pe_nganh", "pb", "peg", "roe", "roa", "tt_ln_ttm", "tt_ln_quy",
    "tt_dt_ttm", "tt_dt_quy", "no_vay_vcsh", "npl", "nim", "co_tuc", "rs", "rsi14", "pct_ma50", "pct_ma200",
    "cach_dinh_52t", "r1t", "r3t", "r6t", "vuot_vnindex_3t", "atr_pct", "beta", "diem_manh", "luu_y",
    "ky_bctc", "phan_cb", "phan_kt", "breakout_20p", "golden_cross_20p",
]


def web_data(df: pd.DataFrame, top: pd.DataFrame, ctx: dict) -> dict:
    """Gói dữ liệu cho trang web (NaN → null)."""
    def clean(v):
        if isinstance(v, (float, np.floating)):
            return None if math.isnan(v) else round(float(v), 4)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return v

    top_set = set(top["ma"])
    rows = []
    for r in df[WEB_FIELDS].to_dict("records"):
        r = {k: clean(v) for k, v in r.items()}
        r["top"] = r["ma"] in top_set
        rows.append(r)
    cfg = ctx["cfg"]
    now = now_vn()
    return {
        "tao_luc": now.strftime("%Y-%m-%d %H:%M"),
        "trong_phien": now.weekday() < 5 and "09:00" <= now.strftime("%H:%M") < "15:00" and ctx["ngay"][-1] == now.strftime("%Y-%m-%d"),
        "ngay": ctx["ngay"], "vnindex": ctx["vnindex"], "vni3m": clean(ctx["vni3m"]),
        "gia_200": ctx["gia_200"],
        "dem": {"xet": ctx["so_ma_xet"], "thanh_khoan": ctx["so_ma_thanh_khoan"], "top": ctx["so_ma_top"]},
        "san": cfg["san"], "loc": cfg["loc_thanh_khoan"], "loc_top": cfg["loc_top"], "trong_so": cfg["trong_so"],
        "giai_thich": GIAI_THICH,
        "rows": rows,
    }


def render_page(data: dict) -> str:
    """Trả về nội dung trang (không có <!doctype>/<head>), dùng chung cho file cục bộ và bản đăng web."""
    tpl = (ROOT / "mau_trang_web.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return tpl.replace("/*__DU_LIEU__*/null", payload)


def export_html(df: pd.DataFrame, top: pd.DataFrame, ctx: dict, web_dir: Path | None = None) -> Path:
    """Ghi ket_qua/bao_cao.html; nếu có web_dir thì ghi thêm index.html + du_lieu.json để đăng lên web
    (trang đang mở sẽ tự tải lại du_lieu.json theo chu kỳ)."""
    data = web_data(df, top, ctx)
    page = ('<!doctype html>\n<html lang="vi">\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + render_page(data))
    OUT.mkdir(exist_ok=True)
    path = OUT / "bao_cao.html"
    path.write_text(page, encoding="utf-8")
    if web_dir:
        web_dir.mkdir(parents=True, exist_ok=True)
        (web_dir / "index.html").write_text(page, encoding="utf-8")
        (web_dir / "du_lieu.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (web_dir / ".nojekyll").write_text("", encoding="utf-8")
        return web_dir / "index.html"
    return path


def print_table(d: pd.DataFrame, n=30):
    if d.empty:
        print("  (không có mã nào thoả toàn bộ điều kiện)")
        return
    f = lambda x, p="{:.0%}": p.format(x) if nz(x) else "-"
    print(f"{'#':>3} {'Mã':<4} {'Giá':>8} {'Tổng':>5} {'CB':>5} {'KT':>5} {'P/E':>5} {'ROE':>5} {'LN4Q':>6} {'LNQ':>6} {'RS':>3} {'RSI':>4}  Điểm mạnh")
    for i, r in enumerate(d.head(n).itertuples(), 1):
        print(f"{i:>3} {r.ma:<4} {r.gia:>8,.0f} {r.diem_tong:>5.1f} {r.diem_cb:>5.0f} {r.diem_kt:>5.0f} "
              f"{f(r.pe, '{:.1f}'):>5} {f(r.roe):>5} {f(r.tt_ln_ttm, '{:+.0%}'):>6} {f(r.tt_ln_quy, '{:+.0%}'):>6} "
              f"{r.rs:>3.0f} {r.rsi14:>4.0f}  {r.diem_manh[:70]}")


def print_detail(r: dict):
    p = lambda x, fmt="{:.1%}": fmt.format(x) if nz(x) else "-"
    print(f"\n=== {r['ma']} – {r['ten']} ({r['san']}, {r['nganh']}) | Kỳ BCTC: {r.get('ky_bctc')} ===")
    print(f"Giá {r['gia']:,.0f} | Điểm tổng {r['diem_tong']} (hạng {r['xep_loai']}) | PTCB {r['diem_cb']} | PTKT {r['diem_kt']}")
    print(f"P/E {p(r['pe'], '{:.1f}')} (ngành {p(r['pe_nganh'], '{:.1f}')}) | P/B {p(r['pb'], '{:.2f}')} | PEG {p(r['peg'], '{:.2f}')} "
          f"| ROE {p(r['roe'])} | ROA {p(r['roa'])} | Cổ tức {p(r['co_tuc'])}")
    print(f"Tăng trưởng LN: 4 quý {p(r.get('tt_ln_ttm'))}, quý {p(r.get('tt_ln_quy'))} | DT: 4 quý {p(r.get('tt_dt_ttm'))}, quý {p(r.get('tt_dt_quy'))}")
    print(f"MA20 {r['ma20']:,.0f} | MA50 {r['ma50']:,.0f} | MA200 {p(r['ma200'], '{:,.0f}')} | RSI {r['rsi14']:.0f} | MACD hist {r['macd_hist']:,.0f} "
          f"| RS {r['rs']:.0f} | Cách đỉnh 52T {p(r['cach_dinh_52t'])} | HS 3T {p(r['r3t'])} | ATR {p(r['atr_pct'])}")
    print(f"Điểm mạnh: {r['diem_manh'] or '-'}")
    print(f"Lưu ý:     {r['luu_y'] or '-'}")


def main():
    ap = argparse.ArgumentParser(description="Lọc cổ phiếu VN theo PTCB + PTKT")
    ap.add_argument("--ma", help="Danh sách mã cần phân tích, cách nhau bằng dấu phẩy")
    ap.add_argument("--san", help="Sàn: HOSE,HNX,UPCOM (ghi đè cau_hinh.json)")
    ap.add_argument("--gtgd", type=float, help="GTGD trung bình 20 phiên tối thiểu (tỷ đồng)")
    ap.add_argument("--top", type=int, help="Số mã hiển thị trong danh sách top")
    ap.add_argument("--lam-moi", action="store_true", help="Bỏ qua cache, tải lại dữ liệu")
    ap.add_argument("--web", help="Thư mục xuất trang web để đăng trực tuyến (index.html + du_lieu.json)")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "cau_hinh.json").read_text(encoding="utf-8"))
    if a.san:
        cfg["san"] = [s.strip().upper() for s in a.san.split(",")]
    if a.gtgd is not None:
        cfg["loc_thanh_khoan"]["gtgd_tb20_toi_thieu_ty"] = a.gtgd
    if a.top:
        cfg["loc_top"]["so_ma_hien_thi"] = a.top
    symbols = [s.strip().upper() for s in a.ma.split(",")] if a.ma else None

    df, top, ctx = run(cfg, symbols, a.lam_moi)
    if symbols:
        for r in df.to_dict("records"):
            print_detail(r)
        return
    print(f"\n===== TOP {len(top)} MÃ THOẢ ĐIỀU KIỆN (PTCB + PTKT) =====")
    print_table(top, len(top))
    path = export(df, top)
    web = export_html(df, top, ctx, Path(a.web).resolve() if a.web else None)
    print(f"\nĐã lưu: {path}")
    print(f"Trang web: {web}")
    print("Lưu ý: đây là kết quả lọc định lượng, không phải khuyến nghị đầu tư.")


if __name__ == "__main__":
    main()
