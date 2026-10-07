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
def chuan_bi(cfg: dict, info: pd.DataFrame, frames: dict, vni: pd.DataFrame) -> dict:
    """Ma trận (phiên × mã) dùng chung cho kiểm định quy tắc và nghiên cứu điểm số: chỉ báo, thanh khoản,
    chỉ số cơ bản tại từng thời điểm, yếu tố và điểm theo nhóm (cùng định nghĩa với loc_co_phieu)."""
    dates = vni.index
    print("2) Tính chỉ báo kỹ thuật theo từng phiên...")
    T = {s: L.technical_series(df).reindex(dates) for s, df in frames.items()}
    M = lambda k: pd.DataFrame({s: t[k] for s, t in T.items()})
    gia, gtgd, gtgd5, so_phien, ma50 = M("gia"), M("gtgd_tb20"), M("gtgd_tb5"), M("so_phien"), M("ma50")
    lk = cfg["loc_thanh_khoan"]
    # Cùng quy tắc thanh khoản với bảng lọc hằng ngày (loc_co_phieu.run)
    thanh_khoan = (gia >= lk["gia_toi_thieu"]) & (so_phien >= 60) & (
        ((gtgd >= lk["gtgd_tb20_toi_thieu_ty"] * 1e9) & (so_phien >= lk["so_phien_toi_thieu"]))
        | ((gtgd5 >= lk["gtgd_tb5_toi_thieu_ty"] * 1e9) & (so_phien >= lk["so_phien_toi_thieu_tb5"])))
    perf = 0.4 * M("r3t").fillna(0) + 0.2 * M("r6t").fillna(0) + 0.2 * M("r9t").fillna(0) + 0.2 * M("r12t").fillna(0)
    rs = (perf.where(thanh_khoan).rank(axis=1, pct=True) * 98 + 1).round()
    hose_hnx = [c for c in gia.columns if info.at[c, "san"] in ("HOSE", "HNX")]
    thi_truong = chuoi_thi_truong(vni, gia[hose_hnx], ma50[hose_hnx])

    bat_dau = dates[252]
    ever = [s for s in gia.columns if thanh_khoan.loc[bat_dau:, s].any()]
    print(f"3) BCTC của {len(ever)} mã từng đủ thanh khoản...")
    fund_raw = L.parallel(lambda s: L.cached(f"co_ban_v3/{s}", cfg["cache_gio"]["co_ban"], lambda: L.fetch_fundamental(s)),
                          ever, cfg.get("so_luong_tai_song_song", 8), "BCTC")
    L.bao_loi_nguon("tài chính")

    # Dòng thời gian chỉ số cơ bản của mỗi mã: thay đổi tại mỗi ngày công bố BCTC
    moc, buoc = {}, {k: {} for k in ("eps", "roe", "roe_min_4q", "so_quy_tang_truong", "so_cp")}
    for s in ever:
        raw = fund_raw.get(s) or {}
        qk = {(q["yearReport"], q["lengthReport"]): q for q in raw.get("quarters", [])}
        ngay = sorted({L.ngay_cong_bo(*k, qk) for k in qk})
        tl = [(d, L.fundamental(raw, info.at[s, "loai"], as_of=d)) for d in ngay]
        moc[s] = ([d for d, _ in tl], [f for _, f in tl])
        for k in buoc:
            if k == "eps":
                val = lambda f: f["ln_ttm"] / f["so_cp"] if L.nz(f.get("ln_ttm")) and f.get("so_cp") else np.nan
            else:
                val = lambda f, k=k: f.get(k) if L.nz(f.get(k)) else np.nan
            e = pd.Series({pd.Timestamp(d): val(f) for d, f in tl}, dtype=float)
            buoc[k][s] = e.reindex(dates.union(e.index)).ffill().reindex(dates) if len(e) else pd.Series(np.nan, index=dates)
    B = {k: pd.DataFrame(v).reindex(columns=gia.columns) for k, v in buoc.items()}
    PE = (gia / B["eps"]).where(B["eps"] > 0)
    pe_hop_le = PE.where(thanh_khoan & (PE > 0) & (PE < 100))
    med_tt = pe_hop_le.median(axis=1)
    PE_NGANH = pd.DataFrame(index=dates, columns=gia.columns, dtype=float)
    for nganh, cols in pd.Series(gia.columns, index=gia.columns).groupby(info.reindex(gia.columns)["nganh"]):
        cols = list(cols)
        med, cnt = pe_hop_le[cols].median(axis=1), pe_hop_le[cols].count(axis=1)
        v = med.where(cnt >= 3, med_tt)
        for c_ in cols:
            PE_NGANH[c_] = v

    # Điểm theo yếu tố cho mọi mã đủ thanh khoản ở mọi phiên – cùng định nghĩa với L.NHOM_DIEM / L.cham_diem
    print("4) Chấm điểm theo yếu tố cho từng phiên...")
    YT = {
        "atr_pct": M("atr_pct"), "bien_do_10p": M("bien_do_10p"), "cach_dinh_52t": M("cach_dinh_52t"),
        "ep": (1 / PE).fillna(0.0).where(gia.notna()),
        "pe_rel": (PE / PE_NGANH).where((PE > 0) & (PE_NGANH > 0)).fillna(5.0).where(gia.notna()),
        "xu_huong_tang": ((gia > ma50) & (ma50 > M("ma200"))).astype(float).where(gia.notna()),
        "roe": B["roe"], "roe_min_4q": B["roe_min_4q"], "so_quy_tang_truong": B["so_quy_tang_truong"],
        "von_hoa_ty": gia * B["so_cp"] / 1e9,
    }
    DIEM = {}
    for key, _, yeu_to in L.NHOM_DIEM:
        parts = [(YT[col] * chieu).where(thanh_khoan).rank(axis=1, pct=True).where(thanh_khoan).fillna(0.5) for col, chieu in yeu_to]
        DIEM[key] = 100 * sum(parts) / len(parts)
    DIEM_TONG = sum(DIEM.values()) / len(DIEM)
    return {"dates": dates, "T": T, "M": M, "gia": gia, "thanh_khoan": thanh_khoan, "rs": rs, "thi_truong": thi_truong,
            "bat_dau": bat_dau, "ever": ever, "moc": moc, "PE": PE, "PE_NGANH": PE_NGANH, "YT": YT, "DIEM": DIEM,
            "DIEM_TONG": DIEM_TONG}


def dung_su_kien(cfg: dict, info: pd.DataFrame, frames: dict, vni: pd.DataFrame, Q: dict | None = None):
    Q = Q or chuan_bi(cfg, info, frames, vni)
    dates, T, gia, thanh_khoan, rs, thi_truong = Q["dates"], Q["T"], Q["gia"], Q["thanh_khoan"], Q["rs"], Q["thi_truong"]
    bat_dau, ever, moc, PE, PE_NGANH, DIEM, DIEM_TONG = (Q[k] for k in ("bat_dau", "ever", "moc", "PE", "PE_NGANH", "DIEM", "DIEM_TONG"))
    print("5) Dựng sự kiện điểm mua và mô phỏng giao dịch...")
    ch = cfg["co_hoi"]
    vc = vni["close"]
    su_kien = []
    ngay_str = dates.strftime("%Y-%m-%d")
    i0 = dates.get_loc(bat_dau)
    for s in ever:
        t = T[s]
        j_s = gia.columns.get_loc(s)
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
            for b_ in L.TECH_BOOL:
                r[b_] = bool(r[b_]) if r[b_] == r[b_] else False
            r["rs"] = float(rs.iat[i, j_s])
            r["pe"] = PE.iat[i, j_s]
            r["pe_nganh"] = PE_NGANH.iat[i, j_s]
            g = min(r["tt_ln_ttm"], 0.5) if L.nz(r.get("tt_ln_ttm")) else np.nan
            r["peg"] = r["pe"] / (g * 100) if L.nz(r["pe"]) and r["pe"] > 0 and L.nz(g) and g > 0 else np.nan
            r["von_hoa_ty"] = r["gia"] * r["so_cp"] / 1e9 if r.get("so_cp") else np.nan
            for key in DIEM:
                r["d_" + key] = float(DIEM[key].iat[i, j_s])
            r["diem_tong"] = round(float(DIEM_TONG.iat[i, j_s]), 1)
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


# ----------------------------------------------------------------------------
# Sức dự báo của điểm số (chạy lại hằng tuần để phát hiện khi điểm số mất tác dụng)
# ----------------------------------------------------------------------------
TEN_YEU_TO = {
    "atr_pct": "Biến động ATR thấp", "bien_do_10p": "Biên độ 10 phiên hẹp", "ep": "Lợi suất lợi nhuận (1/PE) cao",
    "pe_rel": "P/E thấp so với ngành", "roe_min_4q": "ROE thấp nhất 4 quý cao", "roe": "ROE cao",
    "so_quy_tang_truong": "Nhiều quý LN tăng", "von_hoa_ty": "Vốn hoá lớn", "cach_dinh_52t": "Gần đỉnh 52 tuần",
    "xu_huong_tang": "Giá > MA50 > MA200",
}


def _ic_theo_phien(X: pd.DataFrame, Y: pd.DataFrame, mask: pd.DataFrame) -> pd.Series:
    """Tương quan thứ hạng (Spearman) giữa X và Y trong từng phiên, chỉ trên các ô mask; bỏ phiên có < 30 mã."""
    m = mask & X.notna() & Y.notna()
    rx, ry = X.where(m).rank(axis=1), Y.where(m).rank(axis=1)
    rx, ry = rx.sub(rx.mean(axis=1), axis=0), ry.sub(ry.mean(axis=1), axis=0)
    ic = (rx * ry).sum(axis=1) / np.sqrt((rx ** 2).sum(axis=1) * (ry ** 2).sum(axis=1))
    return ic[m.sum(axis=1) >= 30].dropna()


def nghien_cuu_diem(Q: dict, frames: dict, cfg: dict, buoc: int = 5) -> dict:
    """Mỗi `buoc` phiên: tương quan thứ hạng giữa từng yếu tố/nhóm/điểm tổng và lợi nhuận 20 phiên sau đó
    (mua giá mở cửa phiên kế tiếp) vượt trung bình các mã đủ thanh khoản."""
    dates, gia, lk_ = Q["dates"], Q["gia"], Q["thanh_khoan"]
    mo = pd.DataFrame({s: frames[s]["open"] for s in gia.columns}).reindex(dates)
    f20 = gia.ffill().shift(-20) / mo.shift(-1) - 1
    x20 = f20.sub(f20.where(lk_).mean(axis=1), axis=0)
    chon = dates[252:len(dates) - 21:buoc]
    giua = chon[len(chon) // 2]
    X, Y, K = (lambda A: A.loc[chon]), x20.loc[chon], lk_.loc[chon]

    def do(ten, A, nhom):
        ic = _ic_theo_phien(X(A), Y, K)
        a, b = ic[ic.index < giua], ic[ic.index >= giua]
        return {"ten": ten, "nhom": nhom, "ic": float(ic.mean()), "t": float(ic.mean() / (ic.std() / np.sqrt(len(ic)))),
                "ic_nua_dau": float(a.mean()), "ic_nua_sau": float(b.mean()), "so_phien": int(len(ic))}

    ket = [do("Điểm tổng", Q["DIEM_TONG"], "Điểm")]
    ket += [do(ten, Q["DIEM"][k], "Nhóm") for k, ten, _ in L.NHOM_DIEM]
    for _, _, yeu_to in L.NHOM_DIEM:
        ket += [do(TEN_YEU_TO[c], Q["YT"][c] * chieu, "Yếu tố") for c, chieu in yeu_to]
    # Tham khảo: các yếu tố KHÔNG dùng trong điểm
    M = Q["M"]
    ket += [do("Tăng mạnh 5 phiên gần nhất", M("r1w"), "Tham khảo"), do("Hiệu suất 3 tháng (RS)", M("r3t"), "Tham khảo"),
            do("RSI14 cao", M("rsi14"), "Tham khảo")]

    # Kết quả thực tế: 20 mã điểm cao nhất và Danh sách Top mỗi phiên, so với TB thị trường
    diem = Q["DIEM_TONG"].where(lk_).loc[chon]
    top20 = [Y.loc[d][diem.loc[d].nlargest(20).index].mean() for d in chon if diem.loc[d].notna().sum() >= 30]
    lt = cfg["loc_top"]
    top_m = (diem >= lt["diem_toi_thieu"]) & ((Q["YT"]["xu_huong_tang"].loc[chon] == 1) if lt.get("yeu_cau_xu_huong_tang") else True)
    ds_top = Y.where(top_m).mean(axis=1).dropna()
    return {"giua": giua.strftime("%Y-%m-%d"), "so_phien": len(chon), "yeu_to": ket,
            "top20_vuot_tb": float(np.nanmean(top20)),
            "danh_sach_top_vuot_tb": float(ds_top.mean()), "danh_sach_top_ty_le_thang": float((ds_top > 0).mean()),
            "danh_sach_top_so_ma_tb": float(top_m.sum(axis=1).mean())}


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
    Q = chuan_bi(cfg, info, frames, vni)
    su_kien, tu, den, vc = dung_su_kien(cfg, info, frames, vni, Q)
    kq = phan_tich(su_kien, cfg, tu, den, vc)
    print("6) Sức dự báo của điểm số...")
    kq["nghien_cuu_diem"] = nghien_cuu_diem(Q, frames, cfg)
    for x in kq["nghien_cuu_diem"]["yeu_to"]:
        print(f"  {x['nhom']:<9} {x['ten']:<34} IC {x['ic']:+.3f} (t {x['t']:+.1f})  nửa đầu {x['ic_nua_dau']:+.3f}  nửa sau {x['ic_nua_sau']:+.3f}")
    nc = kq["nghien_cuu_diem"]
    print(f"  Top 20 điểm cao nhất vượt TB {nc['top20_vuot_tb']:+.2%}/20 phiên; Danh sách Top vượt TB {nc['danh_sach_top_vuot_tb']:+.2%}"
          f" (~{nc['danh_sach_top_so_ma_tb']:.0f} mã/phiên, {nc['danh_sach_top_ty_le_thang']:.0%} số phiên thắng)")
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
