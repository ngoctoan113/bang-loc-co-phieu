"""
Kiểm định quá khứ bộ tiêu chí "Cơ hội hôm nay".

Với mỗi phiên trong ~3 năm gần nhất và mỗi mã HOSE/HNX đủ thanh khoản TẠI PHIÊN ĐÓ, khi xuất hiện điểm mua kỹ thuật
(vượt đỉnh 20 phiên / về MA20 / nền chặt), dựng lại toàn bộ tiêu chí chỉ bằng dữ liệu đã có tới phiên đó
(BCTC tính từ ngày công bố), rồi mô phỏng giao dịch theo đúng quy tắc trên trang: mua giá mở cửa phiên kế tiếp,
chờ T+2, giữ tối đa `so_phien_giu` phiên, thoát sớm nếu chạm cắt lỗ khẩn cấp; trừ phí ~0,4%.

Kết quả (nhat_ky/kiem_dinh.json):
  - hiệu quả của quy tắc hiện tại so với mọi điểm mua kỹ thuật và VN-Index
  - từng tiêu chí: kết quả khi đạt / không đạt, và khi BỎ tiêu chí đó khỏi quy tắc
  - một số tiêu chí đề xuất: kết quả khi THÊM vào quy tắc
  - mọi so sánh tách hai nửa thời gian để xem có ổn định không

Cách dùng:  python kiem_dinh.py
"""
from __future__ import annotations

import bisect
import json
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import loc_co_phieu as L

sys.stdout.reconfigure(encoding="utf-8")
OUT = L.ROOT / "nhat_ky" / "kiem_dinh.json"
SO_PHIEN = 1000
KHONG_AP_DUNG = ("Nợ vay/VCSH", "Dòng tiền kinh doanh", "Nợ xấu")  # tiêu chí chỉ áp cho một loại doanh nghiệp


# ----------------------------------------------------------------------------
# Dữ liệu
# ----------------------------------------------------------------------------
def tai_du_lieu(cfg: dict):
    ttl = cfg["cache_gio"]
    uni = L.cached("danh_sach", ttl["danh_sach"], L.fetch_universe)
    info = pd.DataFrame(uni).drop_duplicates("ma").set_index("ma")
    m = info["san"].isin(cfg["san"]) & info["loai"].isin(cfg["loai_doanh_nghiep"]) & (info.index.str.len() == 3)
    tickers = sorted(info.index[m])
    tuan = L.now_vn().strftime("%G-W%V")
    print(f"1) Giá {SO_PHIEN} phiên của {len(tickers)} mã...")
    px = L.parallel(lambda s: L.cached(f"gia_dai/{tuan}/{s}", 24 * 7, lambda: L.fetch_ohlc(s, SO_PHIEN)),
                    tickers, cfg.get("so_luong_tai_song_song", 8), "giá")
    vni = L.cached(f"gia_dai/{tuan}/VNINDEX", 24 * 7, lambda: L.fetch_ohlc("VNINDEX", SO_PHIEN))
    L.bao_loi_nguon("giá")
    # Xoá dữ liệu giá dài của các tuần trước
    for d in (L.CACHE / "gia_dai").glob("*"):
        if d.is_dir() and d.name != tuan:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    frames = {s: L.to_frame(px[s]) for s in tickers if px.get(s) and len(px[s]["c"]) >= 260}
    return info, frames, L.to_frame(vni)


def chuoi_thi_truong(vni: pd.DataFrame, gia: pd.DataFrame, ma50: pd.DataFrame) -> pd.Series:
    """Điểm bối cảnh thị trường theo từng phiên – như thi_truong() nhưng không có yếu tố thế giới."""
    c, v = vni["close"], vni["volume"]
    ma20, m50, ma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    diem = 50 + np.where(c > m50, 15, -15) + np.where(c > ma200, 10, -10) + np.where(m50 > ma200, 5, -5) \
        + np.where(c > ma20, 5, -5)
    dist = ((c.pct_change() <= -0.002) & (v > v.shift())).astype(int).rolling(25).sum()
    diem = diem + np.where(dist >= 6, -10, np.where(dist <= 3, 5, 0))
    tren = (gia > ma50).where(ma50.notna()).mean(axis=1).reindex(c.index)
    diem = diem + np.where(tren >= 0.6, 10, np.where(tren <= 0.4, -10, 0))
    return pd.Series(np.clip(diem, 0, 100), index=c.index)


# ----------------------------------------------------------------------------
# Dựng sự kiện (mỗi điểm mua kỹ thuật của một mã đủ thanh khoản)
# ----------------------------------------------------------------------------
def dung_su_kien(cfg: dict, info: pd.DataFrame, frames: dict, vni: pd.DataFrame):
    dates = vni.index
    print("2) Tính chỉ báo kỹ thuật theo từng phiên...")
    T = {s: L.technical_series(df).reindex(dates) for s, df in frames.items()}
    M = lambda k: pd.DataFrame({s: t[k] for s, t in T.items()})
    gia, gtgd, so_phien, ma50 = M("gia"), M("gtgd_tb20"), M("so_phien"), M("ma50")
    lk = cfg["loc_thanh_khoan"]
    thanh_khoan = (gtgd >= lk["gtgd_tb20_toi_thieu_ty"] * 1e9) & (gia >= lk["gia_toi_thieu"]) & (so_phien >= lk["so_phien_toi_thieu"])
    perf = 0.4 * M("r3t").fillna(0) + 0.2 * M("r6t").fillna(0) + 0.2 * M("r9t").fillna(0) + 0.2 * M("r12t").fillna(0)
    rs = (perf.where(thanh_khoan).rank(axis=1, pct=True) * 98 + 1).round()
    thi_truong = chuoi_thi_truong(vni, gia, ma50)

    bat_dau = dates[252]
    ever = [s for s in gia.columns if thanh_khoan.loc[bat_dau:, s].any()]
    print(f"3) BCTC của {len(ever)} mã từng đủ thanh khoản...")
    fund_raw = L.parallel(lambda s: L.cached(f"co_ban_v3/{s}", cfg["cache_gio"]["co_ban"], lambda: L.fetch_fundamental(s)),
                          ever, cfg.get("so_luong_tai_song_song", 8), "BCTC")
    L.bao_loi_nguon("tài chính")

    # Dòng thời gian chỉ số cơ bản của mỗi mã: thay đổi tại mỗi ngày công bố BCTC
    moc, eps = {}, {}
    for s in ever:
        raw = fund_raw.get(s) or {}
        qk = {(q["yearReport"], q["lengthReport"]): q for q in raw.get("quarters", [])}
        ngay = sorted({L.ngay_cong_bo(*k, qk) for k in qk})
        tl = [(d, L.fundamental(raw, info.at[s, "loai"], as_of=d)) for d in ngay]
        moc[s] = ([d for d, _ in tl], [f for _, f in tl])
        e = pd.Series({pd.Timestamp(d): (f["ln_ttm"] / f["so_cp"] if L.nz(f.get("ln_ttm")) and f.get("so_cp") else np.nan)
                       for d, f in tl}, dtype=float)
        eps[s] = e.reindex(dates.union(e.index)).ffill().reindex(dates) if len(e) else pd.Series(np.nan, index=dates)
    EPS = pd.DataFrame(eps).reindex(columns=gia.columns)
    PE = (gia / EPS).where(EPS > 0)
    pe_hop_le = PE.where(thanh_khoan & (PE > 0) & (PE < 100))
    med_tt = pe_hop_le.median(axis=1)
    PE_NGANH = pd.DataFrame(index=dates, columns=gia.columns, dtype=float)
    for nganh, cols in pd.Series(gia.columns, index=gia.columns).groupby(info.reindex(gia.columns)["nganh"]):
        cols = list(cols)
        med, cnt = pe_hop_le[cols].median(axis=1), pe_hop_le[cols].count(axis=1)
        v = med.where(cnt >= 3, med_tt)
        for c_ in cols:
            PE_NGANH[c_] = v

    print("4) Dựng sự kiện điểm mua và mô phỏng giao dịch...")
    ch = cfg["co_hoi"]
    w = cfg["trong_so"]
    vc = vni["close"]
    su_kien = []
    ngay_str = dates.strftime("%Y-%m-%d")
    i0 = dates.get_loc(bat_dau)
    for s in ever:
        t = T[s]
        co_diem_mua = (t["breakout_20p"] == True) | (t["pullback_ma20"] == True) | (t["nen_chat"] == True)  # noqa: E712
        idx = np.where(co_diem_mua.to_numpy() & thanh_khoan[s].to_numpy())[0]
        idx = idx[(idx >= i0) & (idx < len(dates) - 1)]
        if not len(idx):
            continue
        df = frames[s].reindex(dates)
        cf = df["close"].ffill().to_numpy()
        recs = t.to_dict("records")
        moc_ngay, moc_f = moc[s]
        loai, san = info.at[s, "loai"], info.at[s, "san"]
        for i in idx:
            d = ngay_str[i]
            k = bisect.bisect_right(moc_ngay, d) - 1
            if k < 0:
                continue
            r = {**recs[i], **moc_f[k], "ma": s, "san": san, "loai": loai, "nganh": info.at[s, "nganh"]}
            for b in L.TECH_BOOL:
                r[b] = bool(r[b]) if r[b] == r[b] else False
            r["rs"] = float(rs.iat[i, rs.columns.get_loc(s)])
            r["pe"] = PE.iat[i, PE.columns.get_loc(s)]
            r["pe_nganh"] = PE_NGANH.iat[i, PE_NGANH.columns.get_loc(s)]
            g = min(r["tt_ln_ttm"], 0.5) if L.nz(r.get("tt_ln_ttm")) else np.nan
            r["peg"] = r["pe"] / (g * 100) if L.nz(r["pe"]) and r["pe"] > 0 and L.nz(g) and g > 0 else np.nan
            r["von_hoa_ty"] = r["gia"] * r["so_cp"] / 1e9 if r.get("so_cp") else np.nan
            f_, *_ = L.score_fundamental(r)
            k_, *_ = L.score_technical(r)
            r["diem_cb"], r["diem_kt"] = round(f_, 1), round(k_, 1)
            r["diem_tong"] = round(w["co_ban"] * f_ + w["ky_thuat"] * k_, 1)
            r["thi_truong"] = float(thi_truong.iat[i])
            # Mô phỏng: mua giá mở cửa phiên kế tiếp
            gia_vao = df["open"].iat[i + 1]
            if not (gia_vao > 0):
                continue
            kq = L.mo_phong(df, i + 1, gia_vao, L.muc_gia_quy_tac(gia_vao, cfg), float("inf"), toi_da=ch["so_phien_giu"])
            kq["ln_vni"] = float(vc.iat[kq["i_ra"]] / vc.iat[i] - 1)
            # Lợi nhuận giữ cố định h phiên (không phụ thuộc cách thoát lệnh) và phần vượt VN-Index cùng kỳ
            for h in (5, 10, 20, 40):
                j = i + 1 + h
                if j < len(dates) and cf[j] == cf[j]:
                    kq[f"f{h}"] = float(cf[j] / gia_vao - 1 - L.PHI_KHU_HOI)
                    kq[f"v{h}"] = kq[f"f{h}"] - float(vc.iat[j] / vc.iat[i] - 1)
            su_kien.append({"row": r, "i": int(i), "ngay": d, "ma": s, "gia_vao": float(gia_vao), **kq})
    print(f"   {len(su_kien)} sự kiện điểm mua từ {ngay_str[i0]} đến {ngay_str[-1]}")
    return su_kien, ngay_str[i0], ngay_str[-1], vc


# ----------------------------------------------------------------------------
# Đánh giá tiêu chí
# ----------------------------------------------------------------------------
TIEU_CHI_THEM = {
    # Tiêu chí đã bỏ khỏi quy tắc sau kiểm định 10/2026 – tiếp tục theo dõi xem có nên đưa lại không
    "ROE ≥ 15%": lambda r: L.nz(r.get("roe")) and r["roe"] >= 0.15,
    "LN 4 quý tăng ≥ 10%": lambda r: L.nz(r.get("tt_ln_ttm")) and r["tt_ln_ttm"] >= 0.10,
    "Nợ vay/VCSH ≤ 1 (DN thường)": lambda r: r["loai"] != "CT" or (L.nz(r.get("no_vay_vcsh")) and r["no_vay_vcsh"] <= 1),
    "RSI ≤ 72": lambda r: L.nz(r.get("rsi14")) and r["rsi14"] <= 72,
    "Không mua đuổi: giá ≤ MA20 + 6%": lambda r: r["pct_ma20"] <= 0.06,
    # Ứng viên khác
    "Cách đỉnh 52 tuần ≤ 15%": lambda r: r["cach_dinh_52t"] >= -0.15,
    "P/E ≤ trung vị ngành": lambda r: L.nz(r.get("pe")) and L.nz(r.get("pe_nganh")) and 0 < r["pe"] <= r["pe_nganh"],
    "RS ≥ 70": lambda r: r["rs"] >= 70,
    "Thị trường không ở mức Rủi ro cao (≥ 45 điểm)": lambda r: r["thi_truong"] >= 45,
    "KL 20 phiên > KL 50 phiên": lambda r: L.nz(r.get("vol20_vs_vol50")) and r["vol20_vs_vol50"] > 1,
    "Chỉ lấy điểm mua vượt đỉnh 20 phiên": lambda r: r["breakout_20p"],
    "Bỏ điểm mua về MA20": lambda r: r["breakout_20p"] or r["nen_chat"],
    "Biến động rất thấp: ATR ≤ 2,5%": lambda r: r["atr_pct"] <= 0.025,
}


def gan_co(su_kien: list, cfg: dict):
    """Gắn kết quả từng tiêu chí (theo cấu hình hiện tại) cho mỗi sự kiện."""
    ten_tc = []
    for e in su_kien:
        cl, kt, _ = L.kiem_tra_co_hoi(e["row"], cfg["co_hoi"])
        co = {}
        for ten, ok, _ in cl + kt:
            if ten.startswith("Có điểm mua"):
                continue  # mọi sự kiện đều có điểm mua
            co[ten] = bool(ok)
            if ten not in ten_tc:
                ten_tc.append(ten)
        e["co"] = co
        e["them"] = {k: bool(f(e["row"])) for k, f in TIEU_CHI_THEM.items()}
    for e in su_kien:  # tiêu chí không áp dụng cho loại doanh nghiệp này coi như đạt
        for ten in ten_tc:
            e["co"].setdefault(ten, True)
    return ten_tc


def khong_chong_lan(lst: list) -> list:
    """Mỗi mã chỉ giữ một vị thế: bỏ tín hiệu mới khi vị thế trước của cùng mã chưa đóng."""
    out, mo = [], {}
    for e in sorted(lst, key=lambda x: x["i"]):
        if e["i"] > mo.get(e["ma"], -1):
            out.append(e)
            mo[e["ma"]] = e["i"] + 1 + e["so_phien"]
    return out


def tk(lst: list, giua: str) -> dict:
    lst = khong_chong_lan(lst)
    return {**L.thong_ke(lst), "nua_dau": L.thong_ke([e for e in lst if e["ngay"] < giua]),
            "nua_sau": L.thong_ke([e for e in lst if e["ngay"] >= giua])}


def theo_trang(lst: list, cfg: dict) -> list:
    """Như trên trang web: mỗi phiên chỉ lấy tối đa N mã theo bối cảnh thị trường (ưu tiên vượt đỉnh, điểm cao)."""
    toi_da = cfg["co_hoi"]["so_ma_toi_da"]
    by_day = {}
    for e in lst:
        by_day.setdefault(e["i"], []).append(e)
    out = []
    for i, es in by_day.items():
        diem = es[0]["row"]["thi_truong"]
        n = toi_da["thuan_loi" if diem >= 65 else "trung_tinh" if diem >= 45 else "rui_ro"]
        es.sort(key=lambda e: (e["row"]["breakout_20p"], e["row"]["diem_tong"]), reverse=True)
        out += es[:n]
    return out


def nhan_xet_bo(qt: dict, bo: dict) -> str:
    """So sánh quy tắc hiện tại với quy tắc khi bỏ một tiêu chí (dựa trên lợi nhuận TB mỗi giao dịch)."""
    if qt.get("n", 0) < 15 or bo["nua_dau"].get("n", 0) < 5 or bo["nua_sau"].get("n", 0) < 5:
        return "Chưa đủ mẫu"
    d = bo["ln_tb"] - qt["ln_tb"]
    d1 = bo["nua_dau"]["ln_tb"] - qt["nua_dau"].get("ln_tb", 0) if qt["nua_dau"].get("n") else d
    d2 = bo["nua_sau"]["ln_tb"] - qt["nua_sau"].get("ln_tb", 0) if qt["nua_sau"].get("n") else d
    if d > 0.002 and d1 > 0 and d2 > 0:
        return "Cân nhắc bỏ: bỏ đi kết quả tốt hơn ở cả hai giai đoạn"
    if d < -0.002 and d1 < 0 and d2 < 0:
        return "Hữu ích: bỏ đi kết quả kém hơn ở cả hai giai đoạn"
    return "Tác động nhỏ hoặc không ổn định"


def nhan_xet_them(qt: dict, them: dict) -> str:
    if them.get("n", 0) < 10 or them["nua_dau"].get("n", 0) < 4 or them["nua_sau"].get("n", 0) < 4:
        return "Chưa đủ mẫu"
    d = them["ln_tb"] - qt["ln_tb"]
    d1 = them["nua_dau"]["ln_tb"] - qt["nua_dau"].get("ln_tb", 0)
    d2 = them["nua_sau"]["ln_tb"] - qt["nua_sau"].get("ln_tb", 0)
    if d > 0.002 and d1 > 0 and d2 > 0:
        return "Nên thêm: tốt hơn ở cả hai giai đoạn"
    if d < -0.002 and d1 < 0 and d2 < 0:
        return "Không nên thêm: kém hơn ở cả hai giai đoạn"
    return "Tác động nhỏ hoặc không ổn định"


def phan_tich(su_kien: list, cfg: dict, tu: str, den: str, vc: pd.Series) -> dict:
    ten_tc = gan_co(su_kien, cfg)
    giua = sorted(e["ngay"] for e in su_kien)[len(su_kien) // 2] if su_kien else den
    dat_het = lambda e, bo=None: all(v for k, v in e["co"].items() if k != bo)
    quy_tac = [e for e in su_kien if dat_het(e)]
    qt = tk(quy_tac, giua)

    tieu_chi = []
    for ten in ten_tc:
        bo = tk([e for e in su_kien if dat_het(e, ten)], giua)
        tieu_chi.append({
            "ten": ten,
            "dat": tk([e for e in su_kien if e["co"][ten]], giua),
            "khong_dat": tk([e for e in su_kien if not e["co"][ten]], giua),
            "bo": bo, "nhan_xet": nhan_xet_bo(qt, bo),
        })
    them = []
    for ten in TIEU_CHI_THEM:
        t_ = tk([e for e in quy_tac if e["them"][ten]], giua)
        them.append({
            "ten": ten,
            "dat": tk([e for e in su_kien if e["them"][ten]], giua),
            "khong_dat": tk([e for e in su_kien if not e["them"][ten]], giua),
            "them": t_, "nhan_xet": nhan_xet_them(qt, t_),
        })

    trang = khong_chong_lan(theo_trang(quy_tac, cfg))
    gd = [{"ngay": e["ngay"], "ma": e["ma"], "loai_mua": "Vượt đỉnh" if e["row"]["breakout_20p"] else "Về MA20" if e["row"]["pullback_ma20"] else "Nền chặt",
           "gia_vao": round(e["gia_vao"]), "gia_ra": round(e["gia_ra"]), "trang_thai": e["trang_thai"], "ln": round(e["ln"], 4),
           "so_phien": e["so_phien"], "ln_vni": round(e["ln_vni"], 4), "diem_tong": e["row"]["diem_tong"]} for e in trang]
    # VN-Index giữ 20 phiên bất kỳ trong giai đoạn (mốc so sánh)
    v = vc.loc[tu:den]
    vni20 = float((v.shift(-20) / v - 1).dropna().mean())
    return {
        "tao_luc": L.now_vn().strftime("%Y-%m-%d %H:%M"), "tu": tu, "den": den, "giua": giua,
        "so_su_kien": len(su_kien), "so_ma": len({e["ma"] for e in su_kien}),
        "tong_quan": [
            {"ten": "Mọi điểm mua kỹ thuật (mã đủ thanh khoản)", **tk(su_kien, giua)},
            {"ten": "Quy tắc hiện tại (đạt toàn bộ tiêu chí)", **qt},
            {"ten": "Quy tắc hiện tại + giới hạn số mã theo thị trường (như trên trang)", **tk(trang, giua)},
        ],
        "vni_20_phien": vni20,
        "tieu_chi": tieu_chi, "de_xuat_them": them, "giao_dich": gd,
        "cau_hinh": {"co_hoi": cfg["co_hoi"], "danh_muc": cfg["danh_muc"]},
        "gia_dinh": ["Mua giá mở cửa phiên sau tín hiệu; chỉ bán được từ T+2",
                     f"Giữ {cfg['co_hoi']['so_phien_giu']} phiên, thoát sớm nếu chạm cắt lỗ khẩn cấp −{cfg['co_hoi']['cat_lo_pct']:.0%}; trừ phí và thuế ~0,4%",
                     "BCTC chỉ được dùng từ ngày công bố; thiếu ngày công bố thì giả định 50 ngày sau khi hết quý",
                     "P/E tính từ LNST 4 quý / số cổ phiếu; bối cảnh thị trường không gồm yếu tố thế giới",
                     "Chỉ gồm mã còn giao dịch đến nay (mã đã huỷ niêm yết không có trong dữ liệu)"],
    }


def sach(o):
    """NaN/inf → null để JSON hợp lệ cho trình duyệt."""
    if isinstance(o, dict):
        return {k: sach(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sach(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 5)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def main():
    t0 = time.time()
    cfg = json.loads((L.ROOT / "cau_hinh.json").read_text(encoding="utf-8"))
    info, frames, vni = tai_du_lieu(cfg)
    su_kien, tu, den, vc = dung_su_kien(cfg, info, frames, vni)
    kq = phan_tich(su_kien, cfg, tu, den, vc)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(sach(kq), ensure_ascii=False, allow_nan=False), encoding="utf-8")
    f = lambda s: (f"n={s['n']:>4}  thắng {s['thang']:.0%}  LN TB {s['ln_tb']:+.2%}  R TB {s['r_tb']:+.2f}  vượt VNI {s['vuot_vni']:+.2%}"
                   if s.get("n") else "n=0")
    print(f"\nGiai đoạn {tu} → {den}, {kq['so_su_kien']} sự kiện / {kq['so_ma']} mã. VN-Index giữ 20 phiên TB: {kq['vni_20_phien']:+.2%}")
    for x in kq["tong_quan"]:
        print(f"  {x['ten'][:62]:<62} {f(x)}")
    print("\nTiêu chí hiện tại (bỏ tiêu chí → kết quả quy tắc):")
    for x in kq["tieu_chi"]:
        print(f"  {x['ten'][:45]:<45} đạt: {f(x['dat'])[:44]:<44} | bỏ: {f(x['bo'])[:44]:<44} → {x['nhan_xet']}")
    print("\nTiêu chí đề xuất (thêm vào quy tắc):")
    for x in kq["de_xuat_them"]:
        print(f"  {x['ten'][:45]:<45} thêm: {f(x['them'])[:44]:<44} → {x['nhan_xet']}")
    print(f"\nĐã lưu {OUT} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
