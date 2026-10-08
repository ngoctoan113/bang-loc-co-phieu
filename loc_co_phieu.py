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
    rows = sorted(stats.get("data") or [], key=lambda r: (r.get("yearReport") or 0, r.get("quarter") or 0))
    quarters = [
        {k: v for k, v in q.items() if k in ("yearReport", "lengthReport", "publicDate", "isa3", "isb38", "isi64", "isa22")}
        for q in (inc.get("data") or {}).get("quarters", [])
        if q.get("lengthReport") in (1, 2, 3, 4)
    ]
    # Giữ toàn bộ lịch sử chỉ số theo quý để kiểm định quá khứ dựng lại được dữ liệu tại từng thời điểm
    return {"ratios": [{k: r.get(k) for k in RATIO_FIELDS} for r in rows], "quarters": quarters}


RATIO_FIELDS = ("yearReport", "quarter", "roe", "roa", "pe", "pb", "debtPerEquity", "currentRatio", "grossMargin",
                "afterTaxProfitMargin", "npl", "netInterestMargin", "casaRatio", "priceToCashFlow", "numberOfSharesMktCap")


def ngay_cong_bo(y: int, q: int, quarters_by_key: dict) -> str:
    """Ngày BCTC quý (y, q) được công bố; nếu thiếu thì giả định 50 ngày sau khi kết thúc quý (thận trọng)."""
    pub = (quarters_by_key.get((y, q)) or {}).get("publicDate")
    if pub:
        return pub[:10]
    end = pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)
    return (end + pd.Timedelta(days=50)).strftime("%Y-%m-%d")


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


TECH_BOOL = ("ma200_tang", "macd_hist_tang", "golden_cross_20p", "breakout_20p", "pullback_ma20", "nen_chat", "ma50_tang")


def technical_series(df: pd.DataFrame) -> pd.DataFrame:
    """Toàn bộ chỉ báo kỹ thuật cho MỌI phiên (mỗi dòng chỉ dùng dữ liệu đến phiên đó).
    Bảng lọc hằng ngày lấy dòng cuối; kiểm định quá khứ dùng cả chuỗi – hai nơi chung một công thức."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    ma20, ma50, ma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    hi52, lo52 = h.rolling(252, min_periods=1).max(), l.rolling(252, min_periods=1).min()
    vol20, vol50 = v.rolling(20).mean(), v.rolling(50).mean()
    pos = pd.Series(np.arange(len(c)), index=c.index)
    tren = ma50 > ma200
    nen_10p = h.rolling(10).max() / l.rolling(10).min() - 1
    out = pd.DataFrame({
        "gia": c,
        "thay_doi": c / c.shift() - 1,
        "gtgd_tb20": (c * v).rolling(20).mean(),
        "gtgd_tb5": (c * v).rolling(5).mean(),
        "r1w": c / c.shift(5) - 1,
        "so_phien": pos + 1,
        "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "ma200_tang": (ma200 > ma200.shift(20)) & (pos >= 220),
        "pct_ma50": c / ma50 - 1,
        "pct_ma200": c / ma200 - 1,
        "rsi14": rsi(c),
        "macd": macd, "macd_signal": signal, "macd_hist": hist,
        "macd_hist_tang": hist > hist.shift(3),
        "atr_pct": atr / c,
        "cach_dinh_52t": c / hi52 - 1,
        "tu_day_52t": c / lo52 - 1,
        "vol5_vs_vol20": v.rolling(5).mean() / vol20.replace(0, np.nan),
        "vol20_vs_vol50": vol20 / vol50.replace(0, np.nan),
        "r1t": c / c.shift(21) - 1, "r3t": c / c.shift(63) - 1, "r6t": c / c.shift(126) - 1,
        "r9t": c / c.shift(189) - 1, "r12t": c / c.shift(252) - 1,
        # MA50 vừa cắt lên MA200 trong 20 phiên gần nhất
        "golden_cross_20p": tren & (tren.astype(float).rolling(20).min() == 0) & ma200.shift(19).notna(),
        # Vượt đỉnh đóng cửa 20 phiên trước với khối lượng > 1,5 lần trung bình 20 phiên trước đó
        "breakout_20p": (c >= c.shift().rolling(20).max()) & (v > 1.5 * vol20.shift()),
        # Về MA20: 3 phiên gần nhất có giá thấp nhất chạm vùng MA20 (±1,5%) và đóng cửa vẫn trên MA20
        "pullback_ma20": ((l <= ma20 * 1.015).astype(float).rolling(3).max() == 1) & (c > ma20),
        # Nền chặt: biên độ 10 phiên ≤ 8% và giá cách đỉnh 52 tuần không quá 10%
        "nen_chat": (nen_10p <= 0.08) & (c / hi52 - 1 >= -0.10),
        "bien_do_10p": nen_10p,
        "pct_ma20": c / ma20 - 1,
        "ma50_tang": ma50 > ma50.shift(5),
        "atr": atr,
        "dinh_20p": h.shift().rolling(20).max(),
        "dinh_dong_cua_20p": c.rolling(20).max(),
        "day_10p": l.rolling(10).min(),
        "vol_hom_nay": v / vol20.shift().replace(0, np.nan),
    })
    return out


def technical(df: pd.DataFrame) -> dict:
    r = technical_series(df).iloc[-1].to_dict()
    for k in TECH_BOOL:
        r[k] = bool(r[k])
    r["so_phien"] = int(r["so_phien"])
    return r


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


def fundamental(raw: dict, loai: str, as_of: str | None = None) -> dict:
    """Chỉ số cơ bản từ BCTC. as_of='YYYY-MM-DD': chỉ dùng các BCTC đã công bố tới ngày đó (kiểm định quá khứ)."""
    qall = {(q["yearReport"], q["lengthReport"]): q for q in raw.get("quarters", [])}
    ratios = raw.get("ratios") or []
    if as_of:
        qmap = {k: v for k, v in qall.items() if ngay_cong_bo(*k, qall) <= as_of}
        ratios = [r for r in ratios if (r.get("yearReport"), r.get("quarter")) in qmap]
    else:
        qmap = qall
    ratio = dict(ratios[-1]) if ratios else {}
    ratio["roe_lich_su"] = [r.get("roe") for r in ratios[-8:]]
    out = {"ky_bctc": None, "so_cp": ratio.get("numberOfSharesMktCap")}
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
        # Số quý (trong 4 quý gần nhất) có LNST tăng so với cùng kỳ
        n_up = 0
        for k in range(4):
            a, b = qmap.get(_prev_q(y, q, k), {}), qmap.get(_prev_q(y, q, k + 4), {})
            if a.get(PROFIT_FIELD) is not None and b.get(PROFIT_FIELD) is not None and a[PROFIT_FIELD] > b[PROFIT_FIELD]:
                n_up += 1
        out["so_quy_tang_truong"] = n_up

    g = lambda k: ratio.get(k) if ratio.get(k) not in (None, 0, 0.0) else np.nan
    roe_hist = [x for x in (ratio.get("roe_lich_su") or []) if x is not None]
    pcf = ratio.get("priceToCashFlow")
    out.update({
        "roe_min_4q": min(roe_hist[-4:]) if len(roe_hist) >= 4 else np.nan,
        # P/CF dương ⇔ dòng tiền HĐKD 12 tháng dương (không áp dụng cho ngân hàng, CK, bảo hiểm)
        "dong_tien_duong": (pcf is not None and pcf > 0) if loai == "CT" else None,
        "roe": g("roe"), "roa": g("roa"),
        "no_vay_vcsh": ratio.get("debtPerEquity", np.nan) if loai != "NH" else np.nan,
        "thanh_toan_hh": g("currentRatio"),
        "bien_ln_gop": g("grossMargin"), "bien_ln_rong": g("afterTaxProfitMargin"),
        "npl": g("npl"), "nim": g("netInterestMargin"), "casa": g("casaRatio"),
        "pe_quy": g("pe"), "pb_quy": g("pb"),
    })
    return out


# ----------------------------------------------------------------------------
# Chấm điểm theo yếu tố
# ----------------------------------------------------------------------------
# Nghiên cứu 10/2026 (146 thời điểm × ~200 mã, 10/2023–10/2026): chỉ giữ các yếu tố có tương quan thứ hạng (IC) dương
# và ổn định ở cả hai nửa giai đoạn với lợi nhuận 20 phiên vượt trung bình thị trường. Điểm kỹ thuật/tăng trưởng
# cũ (MA, MACD, RS, tăng trưởng LN) không có sức dự báo nên đã bỏ khỏi điểm số (vẫn hiển thị để tham khảo).
# Mỗi nhóm = trung bình thứ hạng phần trăm của các yếu tố trong nhóm, so với các mã đủ thanh khoản cùng phiên.
NHOM_DIEM = [
    ("on_dinh", "Ổn định giá", [("atr_pct", -1), ("bien_do_10p", -1)]),
    ("gia_tri", "Định giá", [("ep", 1), ("pe_rel", -1)]),
    ("chat_luong", "Chất lượng", [("roe_min_4q", 1), ("roe", 1), ("so_quy_tang_truong", 1)]),
    ("quy_mo", "Quy mô", [("von_hoa_ty", 1)]),
    ("xu_huong", "Xu hướng giá", [("cach_dinh_52t", 1), ("xu_huong_tang", 1)]),
]


def nz(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def them_yeu_to(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm các cột yếu tố suy ra: lợi suất lợi nhuận, P/E so với ngành, xu hướng tăng."""
    pe = pd.to_numeric(df["pe"], errors="coerce")
    pen = pd.to_numeric(df["pe_nganh"], errors="coerce")
    df["ep"] = np.where(pe > 0, 1 / pe, 0.0)                              # lỗ / không có P/E → 0 (kém nhất)
    df["pe_rel"] = np.where((pe > 0) & (pen > 0), pe / pen, 5.0)          # lỗ → coi như rất đắt
    df["xu_huong_tang"] = ((df["gia"] > df["ma50"]) & (df["ma50"] > df["ma200"])).astype(float)
    return df


def _pct_trong(x: pd.Series, ref: pd.Series) -> pd.Series:
    """Thứ hạng phần trăm của từng giá trị x trong phân phối ref (0..1), kiểu xếp hạng trung bình."""
    r = np.sort(ref.dropna().to_numpy(dtype=float))
    if not len(r):
        return pd.Series(np.nan, index=x.index)
    v = x.to_numpy(dtype=float)
    lo, hi = np.searchsorted(r, v, "left"), np.searchsorted(r, v, "right")
    out = (lo + (hi - lo + 1) / 2) / len(r)
    return pd.Series(np.where(np.isnan(v), np.nan, np.clip(out, 0, 1)), index=x.index)


def cham_diem(df: pd.DataFrame, ref: pd.Series | None = None) -> pd.DataFrame:
    """Điểm 0–100 cho từng nhóm và điểm tổng (trung bình 5 nhóm). ref: các mã làm mặt bằng so sánh."""
    ref = pd.Series(True, index=df.index) if ref is None else ref
    for key, _, yeu_to in NHOM_DIEM:
        parts = []
        for col, chieu in yeu_to:
            x = pd.to_numeric(df[col], errors="coerce").astype(float) * chieu
            parts.append(_pct_trong(x, x[ref]).fillna(0.5))           # thiếu dữ liệu → mức trung bình
        df["d_" + key] = 100 * sum(parts) / len(parts)
    df["diem_tong"] = df[["d_" + k for k, _, _ in NHOM_DIEM]].mean(axis=1).round(1)
    for k, _, _ in NHOM_DIEM:
        df["d_" + k] = df["d_" + k].round(0)
    return df


def grade(x):
    return "A" if x >= 70 else "B" if x >= 60 else "C" if x >= 45 else "D"


def dien_giai(r: dict) -> tuple[list, list]:
    """Điểm mạnh / lưu ý bằng lời cho một mã (dựa trên các yếu tố có ý nghĩa trong nghiên cứu)."""
    good, warn = [], []
    g = lambda k: r.get(k) if nz(r.get(k)) else None
    atr, pe, pen, roe_min, sq = g("atr_pct"), g("pe"), g("pe_nganh"), g("roe_min_4q"), g("so_quy_tang_truong")
    if atr is not None and atr <= 0.02: good.append(f"Biến động thấp (ATR {atr:.1%})")
    if r.get("nen_chat"): good.append("Nền giá chặt sát đỉnh")
    if pe and pe > 0 and pen and pe <= 0.8 * pen: good.append(f"P/E {pe:.1f} thấp hơn ngành ({pen:.1f})")
    if roe_min is not None and roe_min >= 0.15: good.append(f"ROE ổn định (thấp nhất 4 quý {roe_min:.0%})")
    if sq is not None and sq >= 3: good.append(f"LN tăng {int(sq)}/4 quý gần nhất")
    if g("cach_dinh_52t") is not None and r["cach_dinh_52t"] >= -0.05: good.append("Sát đỉnh 52 tuần")
    if r.get("xu_huong_tang"): good.append("Xu hướng tăng (Giá > MA50 > MA200)")
    if g("rsi14") is not None and r["rsi14"] > 70: good.append(f"Động lượng mạnh (RSI {r['rsi14']:.0f})")
    if g("von_hoa_ty") is not None and r["von_hoa_ty"] >= 10000: good.append(f"Vốn hoá lớn ({r['von_hoa_ty']:,.0f} tỷ)")
    if atr is not None and atr >= 0.04: warn.append(f"Biến động mạnh (ATR {atr:.1%})")
    if not pe or pe <= 0: warn.append("Đang lỗ / P/E âm")
    elif pe > 100: warn.append("P/E > 100 (lợi nhuận rất mỏng)")
    elif pen and pe > 1.5 * pen: warn.append(f"P/E {pe:.1f} cao hơn ngành ({pen:.1f})")
    if g("tt_ln_quy") is not None and r["tt_ln_quy"] < -0.2: warn.append(f"LN quý {r['tt_ln_quy']:.0%}")
    if g("tt_ln_ttm") is not None and r["tt_ln_ttm"] < 0: warn.append(f"LN 4 quý {r['tt_ln_ttm']:.0%}")
    if g("ma200") is not None and r["gia"] < r["ma200"]: warn.append("Giá dưới MA200")
    if g("rsi14") is not None and r["rsi14"] < 40: warn.append(f"Động lượng yếu (RSI {r['rsi14']:.0f})")
    if g("r1w") is not None and r["r1w"] > 0.08 and r.get("diem_tong", 50) < 45:
        warn.append(f"Vừa tăng {r['r1w']:.0%}/5 phiên nhưng nền tảng yếu")
    if r.get("loai") == "NH" and g("npl") is not None and r["npl"] > 0.025: warn.append(f"Nợ xấu {r['npl']:.1%}")
    if r.get("loai") == "CT" and g("no_vay_vcsh") is not None and r["no_vay_vcsh"] > 2: warn.append(f"Nợ vay/VCSH {r['no_vay_vcsh']:.1f}")
    return good, warn


# ----------------------------------------------------------------------------
# Thị trường Việt Nam + thế giới
# ----------------------------------------------------------------------------
# (mã Yahoo, tên, nhóm, chiều ảnh hưởng tới TTCK VN khi chỉ số tăng: +1 tích cực, -1 tiêu cực, 0 không rõ ràng)
THE_GIOI = [
    ("^GSPC", "S&P 500", "Chứng khoán Mỹ", 1),
    ("^IXIC", "Nasdaq", "Chứng khoán Mỹ", 1),
    ("000001.SS", "Shanghai Composite", "Chứng khoán Trung Quốc", 1),
    ("EEM", "ETF thị trường mới nổi (EEM)", "Dòng vốn ngoại", 1),
    ("DX-Y.NYB", "Chỉ số USD (DXY)", "Tỷ giá", -1),
    ("VND=X", "Tỷ giá USD/VND", "Tỷ giá", -1),
    ("^TNX", "Lợi suất TPCP Mỹ 10 năm", "Lãi suất", -1),
    ("BZ=F", "Dầu Brent", "Hàng hoá", 0),
    ("GC=F", "Vàng", "Hàng hoá", 0),
]


def fetch_yahoo(sym: str):
    d = http("GET", f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}?range=1y&interval=1d",
             headers={"Referer": None, "Origin": None,
                      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"})
    try:
        res = d["chart"]["result"][0]
        pts = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
        return {"t": [p[0] for p in pts], "c": [p[1] for p in pts]} if len(pts) > 60 else None
    except (TypeError, KeyError, IndexError):
        return None


def the_gioi(today: str, ttl: float, refresh: bool) -> list[dict]:
    out = []
    raw = parallel(lambda s: cached(f"the_gioi/{today}/{s.replace('^', '_')}", ttl, lambda: fetch_yahoo(s), refresh),
                   [x[0] for x in THE_GIOI], 4, "thế giới")
    for sym, ten, nhom, chieu in THE_GIOI:
        d = raw.get(sym)
        if not d:
            continue
        c = pd.Series(d["c"], dtype=float)
        ma50 = c.rolling(50).mean().iloc[-1]
        last = c.iloc[-1]
        xu_huong = 1 if last > ma50 else -1
        # ^TNX là lợi suất (%), đo biến động theo điểm phần trăm thay vì %
        chg1m = (last - c.iloc[-22]) if sym == "^TNX" else ret(c, 21)
        out.append({
            "ma": sym, "ten": ten, "nhom": nhom, "chieu": chieu, "gia": last,
            "thay_doi": (last - c.iloc[-2]) if sym == "^TNX" else ret(c, 1), "thay_doi_1t": chg1m,
            "tren_ma50": last > ma50, "don_vi": "điểm %" if sym == "^TNX" else "%",
            "anh_huong": chieu * xu_huong,  # +1 thuận lợi, -1 bất lợi, 0 trung tính
            "ngay": datetime.fromtimestamp(d["t"][-1], VN_TZ).strftime("%Y-%m-%d"),
            "spark": [round(x, 4) for x in c.iloc[-60:]],
        })
    return out


def thi_truong(vni_df: pd.DataFrame | None, tech_all: dict, world: list[dict]) -> dict:
    """Chấm điểm bối cảnh thị trường 0–100 từ xu hướng VN-Index, độ rộng thị trường, ngày phân phối và thế giới."""
    yeu_to = []  # (mô tả, điểm cộng/trừ)
    out = {}
    if vni_df is not None and len(vni_df) > 200:
        c, v = vni_df["close"], vni_df["volume"]
        ma20, ma50, ma200 = c.rolling(20).mean().iloc[-1], c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
        last = c.iloc[-1]
        out.update({"vni": last, "vni_thay_doi": ret(c, 1), "vni_1t": ret(c, 21), "vni_3t": ret(c, 63),
                    "vni_ma20": ma20, "vni_ma50": ma50, "vni_ma200": ma200, "vni_rsi": rsi(c).iloc[-1]})
        yeu_to.append(("VN-Index trên MA50" if last > ma50 else "VN-Index dưới MA50", 15 if last > ma50 else -15))
        yeu_to.append(("VN-Index trên MA200" if last > ma200 else "VN-Index dưới MA200", 10 if last > ma200 else -10))
        yeu_to.append(("MA50 trên MA200 (xu hướng trung hạn tăng)" if ma50 > ma200 else "MA50 dưới MA200 (xu hướng trung hạn giảm)",
                       5 if ma50 > ma200 else -5))
        yeu_to.append(("VN-Index trên MA20" if last > ma20 else "VN-Index dưới MA20", 5 if last > ma20 else -5))
        # Ngày phân phối (O'Neil): giảm ≥ 0,2% với khối lượng cao hơn phiên trước, trong 25 phiên gần nhất
        if v.iloc[-25:].sum() > 0:
            chg = c.pct_change()
            dist = int(((chg <= -0.002) & (v > v.shift())).iloc[-25:].sum())
            out["ngay_phan_phoi"] = dist
            if dist >= 6:
                yeu_to.append((f"{dist} ngày phân phối trong 25 phiên (áp lực bán lớn)", -10))
            elif dist <= 3:
                yeu_to.append((f"Chỉ {dist} ngày phân phối trong 25 phiên", 5))
            else:
                yeu_to.append((f"{dist} ngày phân phối trong 25 phiên", 0))

    t = [x for x in tech_all.values() if nz(x.get("ma50"))]
    if t:
        tren50 = sum(x["gia"] > x["ma50"] for x in t) / len(t)
        t200 = [x for x in t if nz(x.get("ma200"))]
        tren200 = sum(x["gia"] > x["ma200"] for x in t200) / len(t200) if t200 else np.nan
        tang = sum(x["thay_doi"] > 0 for x in t)
        giam = sum(x["thay_doi"] < 0 for x in t)
        out.update({"do_rong_ma50": tren50, "do_rong_ma200": tren200, "so_ma_tang": tang, "so_ma_giam": giam,
                    "so_ma_dung": len(t) - tang - giam})
        yeu_to.append((f"{tren50:.0%} cổ phiếu trên MA50", 10 if tren50 >= 0.6 else -10 if tren50 <= 0.4 else 0))

    w = {x["ma"]: x for x in world}
    if "^GSPC" in w:
        yeu_to.append(("S&P 500 trên MA50" if w["^GSPC"]["tren_ma50"] else "S&P 500 dưới MA50", 5 if w["^GSPC"]["tren_ma50"] else -5))
    if "DX-Y.NYB" in w and nz(w["DX-Y.NYB"]["thay_doi_1t"]) and w["DX-Y.NYB"]["thay_doi_1t"] > 0.02:
        yeu_to.append((f"USD mạnh lên {w['DX-Y.NYB']['thay_doi_1t']:+.1%} trong 1 tháng (bất lợi dòng vốn ngoại)", -5))
    if "VND=X" in w and nz(w["VND=X"]["thay_doi_1t"]) and w["VND=X"]["thay_doi_1t"] > 0.01:
        yeu_to.append((f"Tỷ giá USD/VND tăng {w['VND=X']['thay_doi_1t']:+.1%} trong 1 tháng", -5))
    if "^TNX" in w and nz(w["^TNX"]["thay_doi_1t"]) and w["^TNX"]["thay_doi_1t"] > 0.3:
        yeu_to.append((f"Lợi suất TPCP Mỹ tăng {w['^TNX']['thay_doi_1t']:+.2f} điểm % trong 1 tháng", -5))
    if "EEM" in w:
        yeu_to.append(("Thị trường mới nổi (EEM) trên MA50" if w["EEM"]["tren_ma50"] else "Thị trường mới nổi (EEM) dưới MA50",
                       3 if w["EEM"]["tren_ma50"] else -3))

    diem = max(0, min(100, 50 + sum(p for _, p in yeu_to)))
    out["diem"] = diem
    out["trang_thai"] = "thuan_loi" if diem >= 65 else "trung_tinh" if diem >= 45 else "rui_ro"
    out["nhan"] = {"thuan_loi": "Thuận lợi", "trung_tinh": "Trung tính", "rui_ro": "Rủi ro cao"}[out["trang_thai"]]
    out["yeu_to"] = yeu_to
    return out


# ----------------------------------------------------------------------------
# Cơ hội trong ngày: tiêu chí chất lượng + điểm mua kỹ thuật
# ----------------------------------------------------------------------------
def buoc_gia(p: float, san: str) -> int:
    if san == "HOSE":
        return 10 if p < 10000 else 50 if p < 50000 else 100
    return 100


def lam_tron_xuong(p: float, san: str) -> float:
    b = buoc_gia(p, san)
    return math.floor(p / b) * b


def lam_tron_len(p: float, san: str) -> float:
    b = buoc_gia(p, san)
    return math.ceil(p / b) * b


def kiem_tra_co_hoi(r: dict, ch: dict) -> tuple[list, list, str | None]:
    """Trả về (tiêu chí chất lượng, tiêu chí điểm mua, loại điểm mua). Mỗi tiêu chí: [tên, đạt?, giá trị].
    Tiêu chí có ngưỡng = null trong cau_hinh.json được bỏ qua (đã bỏ sau kiểm định quá khứ)."""
    f = lambda x, p="{:.0%}": p.format(x) if nz(x) else "–"
    on = lambda k: ch.get(k) is not None
    loai = r["loai"]
    cl = [[f"Điểm tổng ≥ {ch['diem_tong']}", r["diem_tong"] >= ch["diem_tong"], f"{r['diem_tong']:.0f}"]]
    if on("roe"):
        cl.append([f"ROE ≥ {ch['roe']:.0%}", nz(r.get("roe")) and r["roe"] >= ch["roe"], f(r.get("roe"))])
    if on("roe_min_4q"):
        cl.append([f"ROE thấp nhất 4 quý ≥ {ch['roe_min_4q']:.0%} (ổn định)", nz(r.get("roe_min_4q")) and r["roe_min_4q"] >= ch["roe_min_4q"], f(r.get("roe_min_4q"))])
    if on("tt_ln_ttm"):
        cl.append([f"LN 4 quý tăng ≥ {ch['tt_ln_ttm']:.0%}", nz(r.get("tt_ln_ttm")) and r["tt_ln_ttm"] >= ch["tt_ln_ttm"], f(r.get("tt_ln_ttm"), "{:+.0%}")])
    cl += [
        ["LN quý gần nhất tăng so với cùng kỳ", nz(r.get("tt_ln_quy")) and r["tt_ln_quy"] > 0, f(r.get("tt_ln_quy"), "{:+.0%}")],
        ["≥ 3/4 quý gần nhất LN tăng so với cùng kỳ", (r.get("so_quy_tang_truong") or 0) >= 3, f"{r.get('so_quy_tang_truong') or 0}/4"],
        ["Có lãi 4 quý liên tiếp", bool(r.get("lai_4_quy")), "Có" if r.get("lai_4_quy") else "Không"],
    ]
    if on("peg"):
        cl.append([f"PEG ≤ {ch['peg']}", nz(r.get("peg")) and r["peg"] <= ch["peg"], f(r.get("peg"), "{:.2f}")])
    cl += [
        [f"GTGD TB20 ≥ {ch['gtgd_ty']} tỷ", r["gtgd_tb20"] >= ch["gtgd_ty"] * 1e9, f"{r['gtgd_tb20'] / 1e9:.1f} tỷ"],
        [f"Vốn hoá ≥ {ch['von_hoa_ty']:,} tỷ", nz(r.get("von_hoa_ty")) and r["von_hoa_ty"] >= ch["von_hoa_ty"], f(r.get("von_hoa_ty"), "{:,.0f} tỷ")],
    ]
    if loai == "CT":
        if on("no_vay_vcsh"):
            cl.append([f"Nợ vay/VCSH ≤ {ch['no_vay_vcsh']}", nz(r.get("no_vay_vcsh")) and r["no_vay_vcsh"] <= ch["no_vay_vcsh"], f(r.get("no_vay_vcsh"), "{:.2f}")])
        cl.append(["Dòng tiền kinh doanh 12 tháng dương", bool(r.get("dong_tien_duong")), "Có" if r.get("dong_tien_duong") else "Không"])
    if loai == "NH":
        cl.append(["Nợ xấu ≤ 2%", nz(r.get("npl")) and r["npl"] <= 0.02, f(r.get("npl"), "{:.2%}")])

    lim = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}.get(r["san"], 0.07)
    loai_mua = ("Vượt đỉnh 20 phiên kèm khối lượng" if r["breakout_20p"]
                else "Điều chỉnh về MA20" if r["pullback_ma20"]
                else "Nền giá chặt sát đỉnh" if r["nen_chat"] else None)
    rsi_ok = nz(r.get("rsi14")) and r["rsi14"] >= ch["rsi_min"] and (not on("rsi_max") or r["rsi14"] <= ch["rsi_max"])
    kt = [
        ["Giá trên MA20 và MA50", r["gia"] > r["ma20"] and r["gia"] > r["ma50"], f(r.get("pct_ma50"), "{:+.1%}") + " so MA50"],
        ["MA50 trên MA200 và đang đi lên", (not nz(r.get("ma200")) or r["ma50"] > r["ma200"]) and r["ma50_tang"],
         "MA50 dưới MA200" if nz(r.get("ma200")) and r["ma50"] <= r["ma200"] else "Đạt" if r["ma50_tang"] else "MA50 đi xuống"],
        [f"RSI trong {ch['rsi_min']}–{ch['rsi_max']}" if on("rsi_max") else f"RSI ≥ {ch['rsi_min']}", rsi_ok, f(r.get("rsi14"), "{:.0f}")],
    ]
    if on("atr_pct_toi_da"):
        kt.append([f"Biến động thấp: ATR ≤ {ch['atr_pct_toi_da']:.0%} giá", nz(r.get("atr_pct")) and r["atr_pct"] <= ch["atr_pct_toi_da"], f(r.get("atr_pct"), "{:.1%}")])
    if on("cach_ma20_toi_da"):
        kt.append([f"Không mua đuổi: giá ≤ MA20 + {ch['cach_ma20_toi_da']:.0%}", r["pct_ma20"] <= ch["cach_ma20_toi_da"], f(r.get("pct_ma20"), "{:+.1%}") + " so MA20"])
    kt += [
        ["Chưa tăng kịch trần hôm nay", r["thay_doi"] < lim - 0.002, f(r.get("thay_doi"), "{:+.1%}")],
        ["Có điểm mua: vượt đỉnh / về MA20 / nền chặt", loai_mua is not None, loai_mua or "Chưa có"],
    ]
    return cl, kt, loai_mua


def co_hoi(df: pd.DataFrame, tt: dict, cfg: dict) -> tuple[list, list]:
    ch = cfg["co_hoi"]
    toi_da = ch["so_ma_toi_da"][tt["trang_thai"]]
    dat, gan_dat = [], []
    for r in df.to_dict("records"):
        cl, kt, loai_mua = kiem_tra_co_hoi(r, ch)
        so_truot = sum(not c[1] for c in cl + kt)
        gia, san = r["gia"], r["san"]
        cat_lo = lam_tron_xuong(muc_gia_quy_tac(gia, cfg), san)
        tran_mua = gia * 1.02 if not ch.get("cach_ma20_toi_da") else min(gia * 1.02, r["ma20"] * (1 + ch["cach_ma20_toi_da"]))
        item = {
            "ma": r["ma"], "ten": r["ten"], "san": san, "nganh": r["nganh"], "gia": gia, "thay_doi": r["thay_doi"],
            "diem_tong": r["diem_tong"], "xep_loai": grade(r["diem_tong"]), "phan_nhom": r.get("phan_nhom"),
            "loai_mua": loai_mua, "chat_luong": cl, "diem_mua": kt, "so_truot": so_truot,
            "vung_mua": [gia, lam_tron_len(max(gia, tran_mua), san)],
            "cat_lo": cat_lo, "rui_ro_pct": (gia - cat_lo) / gia, "so_phien_giu": ch["so_phien_giu"],
        }
        if so_truot == 0:
            dat.append(item)
        elif so_truot <= 2:
            gan_dat.append(item)
    dat.sort(key=lambda x: (x["loai_mua"] == "Vượt đỉnh 20 phiên kèm khối lượng", x["diem_tong"]), reverse=True)
    gan_dat.sort(key=lambda x: (x["so_truot"], -x["diem_tong"]))
    for i, x in enumerate(dat):
        x["trong_gioi_han"] = i < toi_da
    return dat[:5], gan_dat[:8]


# ----------------------------------------------------------------------------
# Mô phỏng giao dịch theo quy tắc (dùng chung cho nhật ký tín hiệu và kiểm định quá khứ)
# ----------------------------------------------------------------------------
PHI_KHU_HOI = 0.004  # phí mua + bán + thuế bán ≈ 0,4% giá trị


def muc_gia_quy_tac(gia_vao: float, cfg: dict) -> float:
    """Cắt lỗ khẩn cấp của tín hiệu "Cơ hội hôm nay". Kiểm định quá khứ cho thấy cắt lỗ chặt (7%, 2×ATR) kèm
    chốt lời 2R làm kết quả kém hơn nhiều so với giữ cố định ~20 phiên, nên chỉ dùng cắt lỗ rộng cho biến cố."""
    return gia_vao * (1 - cfg["co_hoi"]["cat_lo_pct"])


def mo_phong(df: pd.DataFrame, i_vao: int, gia_vao: float, cat_lo: float, muc_tieu: float,
             toi_da: int = 20, cho: int = 2) -> dict:
    """Theo dõi vị thế mua tại phiên i_vao. Chỉ được bán từ phiên i_vao + cho (T+2).
    Mỗi phiên: giá thấp nhất chạm cắt lỗ → thoát ở cắt lỗ (hoặc giá mở cửa nếu mở cửa đã thấp hơn);
    giá cao nhất chạm mục tiêu → thoát ở mục tiêu; cùng phiên chạm cả hai thì tính cắt lỗ (thận trọng).
    Sau `toi_da` phiên mà chưa chạm thì thoát theo giá đóng cửa."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    n = len(c)
    ket = None
    for j in range(i_vao + cho, min(n, i_vao + toi_da + 1)):
        if np.isnan(l[j]):  # mã không giao dịch phiên này
            continue
        if l[j] <= cat_lo:
            ket = ("Chạm cắt lỗ", min(o[j], cat_lo) if o[j] > 0 else cat_lo, j)
            break
        if h[j] >= muc_tieu:
            ket = ("Chạm mục tiêu", max(o[j], muc_tieu), j)
            break
    if ket is None:
        j = min(n - 1, i_vao + toi_da)
        while j > i_vao and np.isnan(c[j]):  # lùi về phiên gần nhất có giá
            j -= 1
        ket = ("Hết thời gian", c[j], j) if i_vao + toi_da <= n - 1 else ("Đang mở", c[j], j)
    trang_thai, gia_ra, j = ket
    ln = gia_ra / gia_vao - 1 - (PHI_KHU_HOI if trang_thai != "Đang mở" else 0)
    r = gia_vao - cat_lo
    return {"trang_thai": trang_thai, "gia_ra": float(gia_ra), "i_ra": int(j), "so_phien": int(j - i_vao),
            "ln": float(ln), "r": float((gia_ra - gia_vao) / r) if r > 0 else np.nan}


def thong_ke(lst: list[dict]) -> dict:
    """Tổng hợp kết quả các giao dịch đã đóng."""
    xs = [x for x in lst if x["trang_thai"] != "Đang mở"]
    if not xs:
        return {"n": 0}
    ln = np.array([x["ln"] for x in xs])
    lai, lo = ln[ln > 0].sum(), -ln[ln < 0].sum()
    vni = [x.get("ln_vni") for x in xs if x.get("ln_vni") is not None]
    v20 = [x["v20"] for x in xs if x.get("v20") is not None]
    them = {"f20_tb": float(np.mean([x["f20"] for x in xs if x.get("f20") is not None])),
            "v20_tb": float(np.mean(v20)), "v20_thang": float(np.mean(np.array(v20) > 0))} if v20 else {}
    return {
        **them,
        "n": len(xs), "thang": float((ln > 0).mean()), "ln_tb": float(ln.mean()), "ln_trung_vi": float(np.median(ln)),
        "r_tb": float(np.nanmean([x["r"] for x in xs])),
        "he_so_lai": float(lai / lo) if lo > 0 else None,
        "muc_tieu": float(np.mean([x["trang_thai"] == "Chạm mục tiêu" for x in xs])),
        "cat_lo": float(np.mean([x["trang_thai"] == "Chạm cắt lỗ" for x in xs])),
        "phien_tb": float(np.mean([x["so_phien"] for x in xs])),
        "vni_tb": float(np.mean(vni)) if vni else None,
        "vuot_vni": float(np.mean([x["ln"] - x["ln_vni"] for x in xs if x.get("ln_vni") is not None])) if vni else None,
    }


# ----------------------------------------------------------------------------
# Nhật ký tín hiệu thực tế: ghi lại mã đưa ra ở "Cơ hội hôm nay" và theo dõi kết quả
# ----------------------------------------------------------------------------
NHAT_KY = ROOT / "nhat_ky" / "tin_hieu.csv"
KIEM_DINH = ROOT / "nhat_ky" / "kiem_dinh.json"
NHAT_KY_COT = ["ngay", "gio", "ma", "san", "loai_mua", "gia", "cat_lo", "so_phien_giu", "diem_tong",
               "thi_truong", "diem_thi_truong", "trong_gioi_han", "ngay_truoc", "dong_cua_truoc"]


def ghi_nhat_ky(dat: list[dict], tt: dict, ngay_du_lieu: str, frames: dict) -> int:
    """Thêm tín hiệu mới (mỗi mã tối đa 1 dòng mỗi phiên, giữ lần xuất hiện đầu tiên).
    Lưu kèm giá đóng cửa phiên liền trước làm mốc: khi nguồn dữ liệu điều chỉnh lùi lịch sử giá sau chia cổ tức /
    thưởng cổ phiếu, so mốc này với chuỗi giá mới sẽ ra đúng hệ số điều chỉnh."""
    cu = pd.read_csv(NHAT_KY, dtype=str) if NHAT_KY.exists() else pd.DataFrame(columns=NHAT_KY_COT)
    da_co = set(zip(cu["ngay"], cu["ma"]))
    moi = [{
        "ngay": ngay_du_lieu, "gio": now_vn().strftime("%H:%M"), "ma": x["ma"], "san": x["san"],
        "loai_mua": x["loai_mua"], "gia": x["gia"], "cat_lo": x["cat_lo"], "so_phien_giu": x["so_phien_giu"],
        "diem_tong": x["diem_tong"],
        "thi_truong": tt["nhan"], "diem_thi_truong": tt["diem"], "trong_gioi_han": x["trong_gioi_han"],
        "ngay_truoc": frames[x["ma"]].index[-2].strftime("%Y-%m-%d"), "dong_cua_truoc": float(frames[x["ma"]]["close"].iloc[-2]),
    } for x in dat if (ngay_du_lieu, x["ma"]) not in da_co]
    if moi:
        NHAT_KY.parent.mkdir(exist_ok=True)
        pd.concat([cu, pd.DataFrame(moi)], ignore_index=True).to_csv(NHAT_KY, index=False, encoding="utf-8")
    return len(moi)


def danh_gia_nhat_ky(frames: dict, vni_df: pd.DataFrame | None) -> dict:
    """Theo dõi từng tín hiệu đã ghi: vào lệnh tại giá ghi nhận, thoát khi chạm cắt lỗ khẩn cấp hoặc hết số phiên giữ."""
    if not NHAT_KY.exists():
        return {"tin_hieu": [], "tong_ket": {"n": 0}}
    out = []
    for r in pd.read_csv(NHAT_KY, dtype=str).to_dict("records"):
        df = frames.get(r["ma"])
        x = {**r, **{k: float(r[k]) for k in ("gia", "cat_lo", "diem_tong")}, "so_phien_giu": int(float(r["so_phien_giu"]))}
        if df is None:
            out.append({**x, "trang_thai": "Chưa có giá"})
            continue
        ngay = df.index.strftime("%Y-%m-%d")
        if r["ngay"] not in ngay:
            out.append({**x, "trang_thai": "Chưa có giá"})
            continue
        i = int(np.where(ngay == r["ngay"])[0][0])
        # Quy đổi giá ghi nhận theo điều chỉnh lịch sử giá (chia cổ tức bằng cổ phiếu, thưởng, phát hành quyền…)
        k = 1.0
        try:
            if r.get("ngay_truoc") in ngay and float(r.get("dong_cua_truoc") or 0) > 0:
                k = float(df["close"].iloc[int(np.where(ngay == r["ngay_truoc"])[0][0])]) / float(r["dong_cua_truoc"])
            elif abs(df["close"].iat[i] / x["gia"] - 1) > 0.08:  # dòng cũ chưa có mốc: lệch quá biên độ ngày → do sự kiện quyền
                k = float(df["close"].iat[i]) / x["gia"]
        except (TypeError, ValueError):
            k = 1.0
        if abs(k - 1) > 0.005:
            x["dieu_chinh"] = round(k, 4)
            x["gia_goc"] = x["gia"]
            x["gia"], x["cat_lo"] = x["gia"] * k, x["cat_lo"] * k
        kq = mo_phong(df, i, x["gia"], x["cat_lo"], float("inf"), toi_da=x["so_phien_giu"])
        x.update(kq)
        x["ngay_ra"] = ngay[kq["i_ra"]]
        if vni_df is not None and r["ngay"] in vni_df.index.strftime("%Y-%m-%d"):
            vc = vni_df["close"]
            a = vc.loc[r["ngay"]].iloc[-1] if isinstance(vc.loc[r["ngay"]], pd.Series) else vc.loc[r["ngay"]]
            x["ln_vni"] = float(vc.asof(pd.Timestamp(x["ngay_ra"])) / a - 1)
        x.pop("i_ra", None)
        out.append(x)
    out.sort(key=lambda x: x["ngay"], reverse=True)
    return {"tin_hieu": out, "tong_ket": thong_ke([x for x in out if "ln" in x])}


# ----------------------------------------------------------------------------
# Quy trình chính
# ----------------------------------------------------------------------------
def run(cfg: dict, symbols: list[str] | None, refresh: bool, ghi_nk: bool = False):
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
    co_phieu = info["loai"].isin(cfg["loai_doanh_nghiep"]) & (info.index.str.len() == 3)
    tickers = sorted(info.index[co_phieu & info["san"].isin(cfg["san"])])
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
    print(f"   {sum(1 for s in tickers if px.get(s))}/{len(tickers)} mã có giá")
    bao_loi_nguon("giá")

    lk = cfg["loc_thanh_khoan"]
    tech_all, frames = {}, {}  # mọi mã có giá (≥ 30 phiên): dùng cho danh mục, độ rộng thị trường, mã đang tăng
    for s in tickers:
        if not px.get(s):
            continue
        df = to_frame(px[s])
        if len(df) < 30:
            continue
        frames[s] = df
        tech_all[s] = technical(df)

    def du_thanh_khoan(t: dict) -> bool:
        """GTGD TB 20 phiên đủ lớn, HOẶC dòng tiền 5 phiên gần nhất đủ lớn (bắt mã vừa có dòng tiền vào)."""
        if t["gia"] < lk["gia_toi_thieu"]:
            return False
        if t["gtgd_tb20"] >= lk["gtgd_tb20_toi_thieu_ty"] * 1e9 and t["so_phien"] >= lk["so_phien_toi_thieu"]:
            return True
        return (nz(t.get("gtgd_tb5")) and t["gtgd_tb5"] >= lk["gtgd_tb5_toi_thieu_ty"] * 1e9
                and t["so_phien"] >= lk["so_phien_toi_thieu_tb5"])

    lien_quan = set(symbols or [])
    L_ = {s for s, t in tech_all.items() if du_thanh_khoan(t) and t["so_phien"] >= 60}
    dt = cfg["dang_tang"]
    tang = {s for s, t in tech_all.items() if t["so_phien"] >= 60 and nz(t.get("r1w")) and t["r1w"] >= dt["tang_5_phien"]
            and nz(t.get("gtgd_tb5")) and t["gtgd_tb5"] >= dt["gtgd_tb5_ty"] * 1e9}
    print(f"   {len(L_)} mã đủ thanh khoản (GTGD TB20 ≥ {lk['gtgd_tb20_toi_thieu_ty']} tỷ hoặc TB5 ≥ {lk['gtgd_tb5_toi_thieu_ty']} tỷ, "
          f"giá ≥ {lk['gia_toi_thieu']:,}đ), {len(tang)} mã tăng ≥ {dt['tang_5_phien']:.0%} trong 5 phiên")
    if len(L_) < 30:
        sys.exit(f"Chỉ {len(L_)} mã có dữ liệu – nguồn giá có thể đang lỗi hoặc chặn truy cập; dừng để không ghi đè kết quả cũ.")
    names = sorted(L_ | tang | {s for s in lien_quan if s in tech_all})

    # RS rating (kiểu IBD): 40% hiệu suất 3T + 20% 6T + 20% 9T + 20% 12T, xếp hạng phần trăm 1–99 trong các mã đủ thanh khoản
    t = pd.DataFrame({s: tech_all[s] for s in names}).T
    perf = (0.4 * t["r3t"].astype(float).fillna(0) + 0.2 * t["r6t"].astype(float).fillna(0)
            + 0.2 * t["r9t"].astype(float).fillna(0) + 0.2 * t["r12t"].astype(float).fillna(0))
    rs_rank = (_pct_trong(perf, perf[perf.index.isin(L_)]) * 98 + 1).round()
    vni3m, vni = np.nan, None
    if vnindex:
        vni = to_frame(vnindex)["close"]
        vni3m = ret(vni, 63)

    print("3) Tải dữ liệu tài chính...")
    fund_raw = parallel(lambda s: cached(f"co_ban_v3/{s}", ttl["co_ban"], lambda: fetch_fundamental(s), refresh),
                        names, workers, "BCTC")
    key = hashlib.md5(",".join(names).encode()).hexdigest()[:10]
    vnd = cached(f"vnd/{today}_{key}", ttl["gia"], lambda: fetch_vnd_ratios(names), refresh) or {}
    print(f"   {sum(1 for v in fund_raw.values() if v)}/{len(names)} mã có BCTC, {len(vnd)} mã có định giá VNDirect")
    bao_loi_nguon("tài chính")

    rows = []
    for s in names:
        loai = info.at[s, "loai"]
        r = {"ma": s, "ten": info.at[s, "ten"], "san": info.at[s, "san"], "nganh": info.at[s, "nganh"], "loai": loai,
             "du_thanh_khoan": s in L_}
        r.update(tech_all[s])
        r["rs"] = float(rs_rank.get(s, np.nan))
        r["vuot_vnindex_3t"] = r["r3t"] - vni3m if nz(r["r3t"]) and nz(vni3m) else np.nan
        r.update(fundamental(fund_raw.get(s) or {}, loai))
        v = vnd.get(s, {})
        r["pe"] = v.get("PRICE_TO_EARNINGS", r.get("pe_quy", np.nan))
        r["pb"] = v.get("PRICE_TO_BOOK", r.get("pb_quy", np.nan))
        r["co_tuc"] = v.get("DIVIDEND_YIELD", np.nan)
        r["von_hoa_ty"] = (v["MARKETCAP"] / 1e9 if v.get("MARKETCAP")
                           else r["gia"] * r["so_cp"] / 1e9 if r.get("so_cp") else np.nan)
        r["beta"] = v.get("BETA", np.nan)
        rows.append(r)
    df = pd.DataFrame(rows)
    # P/E trung vị theo ngành trên các mã đủ thanh khoản (cần ≥ 3 mã, nếu không dùng trung vị thị trường)
    pe = pd.to_numeric(df["pe"], errors="coerce")
    hop_le = df["du_thanh_khoan"] & (pe > 0) & (pe < 100)
    med_all = pe[hop_le].median()
    med = pe[hop_le].groupby(df.loc[hop_le, "nganh"]).agg(["median", "count"])
    df["pe_nganh"] = df["nganh"].map(lambda n: med.at[n, "median"] if n in med.index and med.at[n, "count"] >= 3 else med_all)
    # Tăng trưởng dùng cho PEG giới hạn 50% để tránh PEG ảo do LN tăng đột biến từ nền thấp
    g = df["tt_ln_ttm"].clip(upper=0.5)
    df["peg"] = np.where((df["pe"] > 0) & (g > 0), df["pe"] / (g * 100), np.nan)

    # Chấm điểm: mặt bằng so sánh là các mã đủ thanh khoản
    df = cham_diem(them_yeu_to(df), ref=df["du_thanh_khoan"])
    out = []
    for r in df.to_dict("records"):
        r["xep_loai"] = grade(r["diem_tong"])
        r["phan_nhom"] = [[ten, r["d_" + k], 100] for k, ten, _ in NHOM_DIEM]
        tot, xau = dien_giai(r)
        r["diem_manh"], r["luu_y"] = "; ".join(tot), "; ".join(xau)
        out.append(r)
    df_all = pd.DataFrame(out).sort_values("diem_tong", ascending=False).reset_index(drop=True)
    df = df_all[df_all["du_thanh_khoan"] | df_all["ma"].isin(lien_quan)].reset_index(drop=True)

    top_cfg = cfg["loc_top"]
    m = df["du_thanh_khoan"] & (df["diem_tong"] >= top_cfg["diem_toi_thieu"])
    if top_cfg.get("yeu_cau_xu_huong_tang"):
        m &= df["xu_huong_tang"] == 1
    top = df[m].head(top_cfg["so_ma_hien_thi"])

    # Mã đang tăng mạnh 5 phiên, phân loại theo điểm nền tảng
    # (nghiên cứu 10/2026: trong nhóm vừa tăng mạnh, điểm ≥ 60 tiếp tục vượt TB thị trường, điểm < 45 thường đảo chiều)
    dang_tang = []
    for r in df_all[df_all["ma"].isin(tang)].sort_values("r1w", ascending=False).to_dict("records"):
        dang_tang.append({
            "ma": r["ma"], "ten": r["ten"], "san": r["san"], "nganh": r["nganh"], "gia": r["gia"], "thay_doi": r["thay_doi"],
            "r1w": r["r1w"], "gtgd_tb5": r["gtgd_tb5"], "gtgd_tb20": r["gtgd_tb20"], "diem_tong": r["diem_tong"],
            "xep_loai": r["xep_loai"], "phan_nhom": r["phan_nhom"], "du_thanh_khoan": bool(r["du_thanh_khoan"]),
            "rsi14": r["rsi14"], "diem_manh": r["diem_manh"], "luu_y": r["luu_y"],
            "nhom": ("nen_tang" if r["diem_tong"] >= dt["diem_nen_tang"]
                     else "dau_co" if r["diem_tong"] < dt["diem_dau_co"] else "trung_binh"),
        })

    print("4) Bối cảnh thị trường Việt Nam và thế giới...")
    world = the_gioi(today, ttl["gia"], refresh)
    bao_loi_nguon("thế giới")
    # Độ rộng thị trường chỉ tính trên các sàn được lọc (HOSE, HNX) để khớp với VN-Index
    tt = thi_truong(to_frame(vnindex) if vnindex else None,
                    {s: t for s, t in tech_all.items() if info.at[s, "san"] in ("HOSE", "HNX") and t["so_phien"] >= 60}, world)
    dat, gan_dat = co_hoi(df, tt, cfg)
    print(f"   Thị trường: {tt['nhan']} ({tt['diem']}/100). Cơ hội đạt đủ tiêu chí: "
          f"{', '.join(x['ma'] for x in dat) or 'không có'}")
    vni_df = to_frame(vnindex) if vnindex else None
    if ghi_nk and vni_df is not None and not symbols:
        n_moi = ghi_nhat_ky(dat, tt, vni_df.index[-1].strftime("%Y-%m-%d"), frames)
        print(f"   Nhật ký tín hiệu: thêm {n_moi} tín hiệu mới")
    nhat_ky = danh_gia_nhat_ky(frames, vni_df)

    # Dữ liệu kỹ thuật gọn cho mọi mã có giá (để đánh giá danh mục kể cả mã thanh khoản thấp)
    tat_ca = {
        s: {"ten": info.at[s, "ten"], "san": info.at[s, "san"], "nganh": info.at[s, "nganh"],
            **{k: (None if not nz(x.get(k)) else round(float(x[k])) if abs(x[k]) >= 1000 else round(float(x[k]), 4))
               for k in ("gia", "thay_doi", "ma20", "ma50", "ma200", "rsi14", "atr", "dinh_dong_cua_20p",
                         "day_10p", "pct_ma50", "cach_dinh_52t", "gtgd_tb20")},
            "ma50_tang": x["ma50_tang"]}
        for s, x in tech_all.items()
    }

    print(f"\nHoàn tất trong {time.time() - t0:.0f}s. VN-Index 3 tháng: {vni3m:+.1%}" if nz(vni3m) else "")

    # Chuỗi giá 200 phiên (căn theo ngày giao dịch của VN-Index) để vẽ biểu đồ trên trang web
    axis = vni.index[-200:] if vni is not None else frames[df["ma"].iloc[0]].index[-200:]
    closes = {s: [None if math.isnan(x) else round(x) for x in frames[s]["close"].reindex(axis).ffill()]
              for s in df_all["ma"]}
    ctx = {
        "vni3m": vni3m,
        "vnindex": [round(x, 2) for x in vni.reindex(axis).ffill()] if vni is not None else [],
        # VN-Index dài hơn (đủ để vẽ MA200 trên 250 phiên) cho tab Thị trường
        "vnindex_dai": {"ngay": [d.strftime("%Y-%m-%d") for d in vni.index[-450:]],
                        "c": [round(x, 2) for x in vni.iloc[-450:]]} if vni is not None else None,
        "ngay": [d.strftime("%Y-%m-%d") for d in axis],
        "gia_200": closes,
        "so_ma_xet": len(tickers), "so_ma_thanh_khoan": len(L_), "so_ma_top": len(top), "dang_tang": dang_tang,
        "cfg": cfg,
        "thi_truong": tt, "the_gioi": world, "co_hoi": dat, "gan_dat": gan_dat, "tat_ca": tat_ca,
        "nhat_ky": nhat_ky,
        "df_all": df_all,  # gồm cả mã đang tăng mạnh chưa đủ thanh khoản (cho trang web)
    }
    return df, top, ctx


# ----------------------------------------------------------------------------
# Xuất kết quả
# ----------------------------------------------------------------------------
COLUMNS = [
    ("ma", "Mã", None), ("ten", "Tên", None), ("san", "Sàn", None), ("nganh", "Ngành", None),
    ("gia", "Giá", "#,##0"), ("diem_tong", "Điểm tổng", "0.0"), ("xep_loai", "Hạng", None),
    ("d_on_dinh", "Ổn định giá", "0"), ("d_gia_tri", "Định giá", "0"), ("d_chat_luong", "Chất lượng", "0"),
    ("d_quy_mo", "Quy mô", "0"), ("d_xu_huong", "Xu hướng giá", "0"),
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
    ("CÁCH TÍNH ĐIỂM (0–100)", ""),
    ("Nguyên tắc", "Mỗi yếu tố được xếp hạng phần trăm so với tất cả mã đủ thanh khoản trong cùng phiên (100 = tốt nhất). "
                   "Điểm nhóm = trung bình các yếu tố trong nhóm; Điểm tổng = trung bình 5 nhóm. Thiếu dữ liệu → tính mức trung bình (50)."),
    ("Ổn định giá", "ATR14 (% giá) càng thấp càng tốt; biên độ dao động 10 phiên càng hẹp càng tốt"),
    ("Định giá", "Lợi suất lợi nhuận 1/PE càng cao càng tốt (doanh nghiệp lỗ = kém nhất); P/E so với trung vị ngành càng thấp càng tốt"),
    ("Chất lượng", "ROE thấp nhất trong 4 quý gần nhất (độ ổn định); ROE hiện tại; số quý trong 4 quý gần nhất có LN tăng so với cùng kỳ"),
    ("Quy mô", "Vốn hoá càng lớn càng tốt"),
    ("Xu hướng giá", "Khoảng cách tới đỉnh 52 tuần càng gần càng tốt; giá > MA50 > MA200"),
    ("Hạng", "A ≥ 70 · B ≥ 60 · C ≥ 45 · D < 45"),
    ("", ""),
    ("VÌ SAO CHỌN CÁC YẾU TỐ NÀY", ""),
    ("Nghiên cứu 10/2026", "146 thời điểm × ~200 mã đủ thanh khoản (10/2023–10/2026), đo tương quan thứ hạng (IC) giữa từng yếu tố và "
                           "lợi nhuận 20 phiên sau đó so với trung bình thị trường; chỉ giữ yếu tố có IC dương ở cả hai nửa giai đoạn."),
    ("Kết quả", "Điểm mới: IC +0,11 (nửa đầu +0,107, nửa sau +0,113); 20 mã điểm cao nhất vượt TB thị trường +1,2%/20 phiên.\n"
                "Điểm cũ (PTCB 50% + PTKT 50%): IC +0,03, nửa sau −0,01 (mất tác dụng)."),
    ("Đã bỏ khỏi điểm", "Tăng trưởng LN/doanh thu, MACD, RS 3–12 tháng, RSI, các mốc MA chi tiết: không có sức dự báo ổn định "
                        "(vẫn hiển thị để tham khảo)."),
    ("", ""),
    ("DANH SÁCH TOP", "Đủ thanh khoản, điểm tổng ≥ 65 và giá > MA50 > MA200 (chỉnh trong cau_hinh.json, mục loc_top). "
                      "Trong nghiên cứu nhóm này vượt TB thị trường +1,4%/20 phiên, ổn định cả hai nửa giai đoạn."),
    ("ĐỦ THANH KHOẢN", "GTGD TB 20 phiên ≥ 5 tỷ (≥ 200 phiên dữ liệu) HOẶC GTGD TB 5 phiên ≥ 10 tỷ (≥ 120 phiên); giá ≥ 5.000đ; "
                       "gồm HOSE, HNX, UPCOM."),
    ("ĐANG TĂNG MẠNH", "Tăng ≥ 8% trong 5 phiên, GTGD TB 5 phiên ≥ 1 tỷ. Có nền tảng: điểm ≥ 60 (trong nghiên cứu tiếp tục vượt TB "
                       "+2,2%/20 phiên); đầu cơ: điểm < 45 (thường kém TB −2,3%/20 phiên)."),
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
                if label in ("Điểm tổng", "Ổn định giá", "Định giá", "Chất lượng", "Quy mô", "Xu hướng giá", "RS (1-99)"):
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
    "ma", "ten", "san", "nganh", "loai", "gia", "thay_doi", "diem_tong", "d_on_dinh", "d_gia_tri", "d_chat_luong",
    "d_quy_mo", "d_xu_huong", "xep_loai", "r1w", "gtgd_tb5", "xu_huong_tang", "du_thanh_khoan",
    "gtgd_tb20", "von_hoa_ty", "pe", "pe_nganh", "pb", "peg", "roe", "roa", "tt_ln_ttm", "tt_ln_quy",
    "tt_dt_ttm", "tt_dt_quy", "no_vay_vcsh", "npl", "nim", "co_tuc", "rs", "rsi14", "pct_ma50", "pct_ma200",
    "cach_dinh_52t", "r1t", "r3t", "r6t", "vuot_vnindex_3t", "atr_pct", "beta", "diem_manh", "luu_y",
    "ky_bctc", "phan_nhom", "breakout_20p", "golden_cross_20p", "nen_chat", "roe_min_4q", "so_quy_tang_truong",
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
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v

    top_set = set(top["ma"])
    rows = []
    for r in ctx.get("df_all", df)[WEB_FIELDS].to_dict("records"):
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
        "san": cfg["san"], "loc": cfg["loc_thanh_khoan"], "loc_top": cfg["loc_top"], "cfg_dang_tang": cfg["dang_tang"],
        "nhom_diem": [[k, ten] for k, ten, _ in NHOM_DIEM], "dang_tang": clean(ctx["dang_tang"]),
        "giai_thich": GIAI_THICH,
        "rows": rows,
        "thi_truong": clean(ctx["thi_truong"]), "the_gioi": clean(ctx["the_gioi"]), "vnindex_dai": ctx["vnindex_dai"],
        "co_hoi": clean(ctx["co_hoi"]), "gan_dat": clean(ctx["gan_dat"]), "tat_ca": clean(ctx["tat_ca"]),
        "cfg_co_hoi": cfg["co_hoi"], "cfg_danh_muc": cfg["danh_muc"],
        "nhat_ky": clean(ctx["nhat_ky"]),
        # Kết quả kiểm định quá khứ do kiem_dinh.py tạo (chạy hằng tuần)
        "kiem_dinh": json.loads(KIEM_DINH.read_text(encoding="utf-8")) if KIEM_DINH.exists() else None,
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
        # Bản web không nhúng dữ liệu: trang tự tải du_lieu.json (tránh tải dữ liệu hai lần)
        (web_dir / "index.html").write_text(
            page.split("<title>")[0] + (ROOT / "mau_trang_web.html").read_text(encoding="utf-8"), encoding="utf-8")
        (web_dir / "du_lieu.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (web_dir / ".nojekyll").write_text("", encoding="utf-8")
        return web_dir / "index.html"
    return path


def print_table(d: pd.DataFrame, n=30):
    if d.empty:
        print("  (không có mã nào thoả toàn bộ điều kiện)")
        return
    f = lambda x, p="{:.0%}": p.format(x) if nz(x) else "-"
    print(f"{'#':>3} {'Mã':<4} {'Sàn':<5} {'Giá':>8} {'Tổng':>5} {'ÔĐ':>4} {'GT':>4} {'CL':>4} {'QM':>4} {'XH':>4} {'P/E':>5} {'ROE':>5} {'5P':>6}  Điểm mạnh")
    for i, r in enumerate(d.head(n).itertuples(), 1):
        print(f"{i:>3} {r.ma:<4} {r.san:<5} {r.gia:>8,.0f} {r.diem_tong:>5.1f} {r.d_on_dinh:>4.0f} {r.d_gia_tri:>4.0f} "
              f"{r.d_chat_luong:>4.0f} {r.d_quy_mo:>4.0f} {r.d_xu_huong:>4.0f} {f(r.pe, '{:.1f}'):>5} {f(r.roe):>5} "
              f"{f(r.r1w, '{:+.1%}'):>6}  {r.diem_manh[:70]}")


def print_detail(r: dict):
    p = lambda x, fmt="{:.1%}": fmt.format(x) if nz(x) else "-"
    print(f"\n=== {r['ma']} – {r['ten']} ({r['san']}, {r['nganh']}) | Kỳ BCTC: {r.get('ky_bctc')} ===")
    print(f"Giá {r['gia']:,.0f} | Điểm tổng {r['diem_tong']} (hạng {r['xep_loai']}) | "
          + " | ".join(f"{ten} {r['d_' + k]:.0f}" for k, ten, _ in NHOM_DIEM))
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
    ap.add_argument("--ghi-nhat-ky", action="store_true", help="Ghi tín hiệu mới vào nhat_ky/tin_hieu.csv (máy chủ GitHub bật cờ này)")
    a = ap.parse_args()

    cfg = json.loads((ROOT / "cau_hinh.json").read_text(encoding="utf-8"))
    if a.san:
        cfg["san"] = [s.strip().upper() for s in a.san.split(",")]
    if a.gtgd is not None:
        cfg["loc_thanh_khoan"]["gtgd_tb20_toi_thieu_ty"] = a.gtgd
    if a.top:
        cfg["loc_top"]["so_ma_hien_thi"] = a.top
    symbols = [s.strip().upper() for s in a.ma.split(",")] if a.ma else None

    df, top, ctx = run(cfg, symbols, a.lam_moi, a.ghi_nhat_ky)
    if symbols:
        for r in df[df["ma"].isin(symbols)].to_dict("records"):
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
