# Bộ lọc cổ phiếu Việt Nam – PTCB + PTKT

Công cụ tự động tải dữ liệu toàn bộ cổ phiếu sàn HOSE/HNX (có thể thêm UPCOM), lọc theo thanh khoản,
chấm điểm theo các tiêu chí phân tích cơ bản và phân tích kỹ thuật, rồi xuất danh sách xếp hạng ra Excel.

## Cách chạy

Nhấp đúp `chay.bat`, hoặc mở terminal tại thư mục này:

```
python loc_co_phieu.py                    # lọc HOSE + HNX theo cau_hinh.json
python loc_co_phieu.py --san HOSE,HNX,UPCOM
python loc_co_phieu.py --gtgd 10 --top 20 # GTGD TB20 ≥ 10 tỷ, hiện 20 mã
python loc_co_phieu.py --ma FPT,HPG,VCB   # xem chi tiết từng mã
python loc_co_phieu.py --lam-moi          # bỏ cache, tải lại dữ liệu
```

Kết quả nằm trong thư mục `ket_qua/`:
- `bao_cao.html` – **trang web xem kết quả** (mở bằng trình duyệt): lọc theo ngành/sàn/hạng, sắp xếp theo cột,
  bấm vào một mã để xem biểu đồ giá 200 phiên (MA20, MA50), điểm từng tiêu chí, chỉ số và điểm mạnh/lưu ý.
  File này được ghi đè ở mỗi lần chạy. Giao diện nằm trong `mau_trang_web.html`.
- File Excel gồm 3 sheet: **Top co phieu**, **Xep hang day du**, **Giai thich**; kèm file CSV.

Chỉ cần Python 3.10+ với `pandas`, `numpy`, `requests`, `openpyxl`.

## Các tab trên trang web

- **Cơ hội hôm nay**: tối đa 5 mã qua **toàn bộ** tiêu chí chất lượng (ROE cao và ổn định, lợi nhuận tăng đều,
  dòng tiền dương, nợ thấp, thanh khoản tốt) **và** đang có điểm mua kỹ thuật (vượt đỉnh 20 phiên kèm khối lượng /
  điều chỉnh về MA20 / nền giá chặt), chưa tăng quá xa. Kèm vùng mua, cắt lỗ, mục tiêu tham chiếu tính theo quy tắc.
  Số mã tối đa phụ thuộc bối cảnh thị trường (Thuận lợi 5 · Trung tính 3 · Rủi ro cao 1).
  Bên dưới là danh sách theo dõi các mã chỉ thiếu 1–2 tiêu chí.
- **Bảng xếp hạng**: mọi mã HOSE/HNX/UPCOM đủ thanh khoản, chấm điểm theo 5 nhóm yếu tố (xem "Cách chấm điểm").
  Ba tab con: *Danh sách Top* (điểm ≥ 65 và giá > MA50 > MA200) · *Tất cả* · *Đang tăng mạnh* (tăng ≥ 8% trong 5 phiên,
  gắn nhãn "Tăng có nền tảng" / "Trung bình" / "Tăng đầu cơ" theo điểm).
- **Danh mục của tôi**: nhập mã, giá vốn, khối lượng, ngày mua. Trang áp quy tắc để báo Cắt lỗ / Bán (chạm điểm dừng lãi)
  / Giảm tỷ trọng / Chốt lời một phần / Thận trọng / Giữ, kèm ngưỡng dừng và lý do. Danh mục chỉ lưu trên trình duyệt;
  dùng nút "Tạo link" để mở cùng danh mục trên máy khác (dữ liệu nằm trong link, không gửi lên máy chủ).
- **Thị trường**: điểm bối cảnh 0–100 từ xu hướng VN-Index (MA20/50/200), độ rộng thị trường, ngày phân phối,
  và thế giới (S&P 500, Nasdaq, Shanghai, EEM, DXY, USD/VND, lợi suất TPCP Mỹ 10 năm, dầu Brent, vàng – nguồn Yahoo Finance).

- **Kiểm định**: hai cách kiểm tra bộ tiêu chí có thực sự hiệu quả không.
  - *Nhật ký tín hiệu thực tế* (`nhat_ky/tin_hieu.csv`): mỗi mã xuất hiện ở "Cơ hội hôm nay" được ghi lại và theo dõi
    theo đúng quy tắc (T+2, giữ 20 phiên, cắt lỗ khẩn cấp), so với VN-Index cùng kỳ. Máy chủ GitHub tự ghi và lưu vào kho.
  - *Kiểm định quá khứ* (`python kiem_dinh.py` → `nhat_ky/kiem_dinh.json`, máy chủ chạy sáng thứ Bảy hằng tuần):
    dựng lại tiêu chí trên ~3 năm dữ liệu (BCTC tính từ ngày công bố, thanh khoản tại từng thời điểm),
    so sánh khi đạt / không đạt / khi bỏ từng tiêu chí và khi thêm tiêu chí đề xuất, tách hai nửa giai đoạn.

Các ngưỡng nằm trong `cau_hinh.json` (mục `co_hoi`, `danh_muc`). Ngưỡng `null` = tiêu chí đang tắt.

### Kết quả kiểm định 10/2026 và thay đổi đã áp dụng
Giai đoạn 10/2023–10/2026, 50.838 điểm mua kỹ thuật của 300 mã:

| Bộ quy tắc | Số GD | Thắng | LN TB/GD | Vượt VN-Index |
|---|---|---|---|---|
| Mọi điểm mua kỹ thuật | 5.127 | 42% | −0,55% | −1,79% |
| Quy tắc cũ (cắt lỗ 7%/2ATR, chốt lời 2R) | 208 | 37% | −0,90% | −1,25% |
| **Quy tắc mới, như trên trang** | 137 | 55% | **+1,66%** | **+0,81%** |

- Thêm **biến động thấp: ATR ≤ 3%** (tiêu chí có tác dụng rõ và ổn định nhất).
- Bỏ ROE ≥ 15%, LN 4 quý ≥ 10%, nợ vay/VCSH ≤ 1, trần RSI 72 (không giúp hoặc làm kém đi); nới "không mua đuổi" lên MA20 + 15%.
- Đổi cách thoát lệnh: **giữ 20 phiên, cắt lỗ khẩn cấp 10%** thay cho cắt lỗ 7%/2ATR + chốt lời 2R
  (cắt lỗ chặt làm bị "rũ" khỏi mã tốt). Quy tắc danh mục (`danh_muc`) chưa thay đổi.
- Các tiêu chí đã bỏ vẫn được kiểm định hằng tuần trong nhóm "đề xuất" để xem có nên đưa lại.
- Giới hạn: chỉ có mã còn niêm yết (thiên lệch sống sót), mẫu ~140 GD nên kết quả còn dao động; nhật ký thực tế là phép thử quyết định.

## Bản trực tuyến tự cập nhật (GitHub Pages)

Kho này có sẵn workflow `.github/workflows/cap_nhat_web.yml`. Máy chủ GitHub tự chạy bộ lọc
**mỗi 20 phút từ 9:00 đến 14:40 và lúc 15:20 (thứ 2–6, giờ VN)**, rồi đăng kết quả lên địa chỉ
`https://<tài-khoản>.github.io/<tên-kho>/`. Mở được trên mọi máy và điện thoại, không cần bật máy tính này.
Trang đang mở tự kiểm tra dữ liệu mới mỗi 3 phút (nhãn **Tự cập nhật** màu xanh).

Thiết lập một lần:
1. Tạo kho **Public** trên GitHub (GitHub Pages miễn phí chỉ áp dụng cho kho công khai), không tick tạo README.
2. Đẩy mã lên:
   ```
   git remote add origin https://github.com/<tài-khoản>/<tên-kho>.git
   git push -u origin main
   ```
3. Trên GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. Tab **Actions → Cập nhật bảng lọc cổ phiếu → Run workflow** để chạy lần đầu.

Lưu ý:
- Lịch chạy của GitHub có thể trễ 5–15 phút vào giờ cao điểm.
- GitHub tự tắt lịch chạy nếu kho không có commit nào trong 60 ngày; khi đó bấm bật lại trong tab Actions.
- Muốn đổi tiêu chí lọc cho bản trực tuyến: sửa `cau_hinh.json`, commit và push. Workflow chạy lại ngay khi có push.

## Nguồn dữ liệu
- **Vietcap (VCI)**: danh sách công ty, ngành ICB, giá lịch sử ngày, chỉ số tài chính TTM, báo cáo KQKD theo quý.
- **VNDirect**: P/E, P/B, tỷ suất cổ tức, vốn hoá, beta theo giá hiện tại.
- **DNSE**: nguồn giá dự phòng.

Dữ liệu được lưu cache trong `cache/` (giá: 1 giờ, BCTC: 72 giờ).

## Cách chấm điểm (từ 10/2026)

Mỗi yếu tố được **xếp hạng phần trăm so với mọi mã đủ thanh khoản trong cùng phiên** (100 = tốt nhất);
điểm nhóm = trung bình các yếu tố trong nhóm; **Điểm tổng = trung bình 5 nhóm**. Hạng A ≥ 70 · B ≥ 60 · C ≥ 45 · D < 45.

| Nhóm | Yếu tố |
|---|---|
| Ổn định giá | ATR14 (% giá) thấp · biên độ 10 phiên hẹp |
| Định giá | lợi suất lợi nhuận 1/PE cao · P/E thấp so với trung vị ngành |
| Chất lượng | ROE thấp nhất 4 quý · ROE · số quý LN tăng so với cùng kỳ |
| Quy mô | vốn hoá |
| Xu hướng giá | gần đỉnh 52 tuần · giá > MA50 > MA200 |

**Vì sao đổi** (nghiên cứu 10/2026: 146 thời điểm × ~200 mã, 10/2023–10/2026, đo tương quan thứ hạng IC giữa yếu tố và
lợi nhuận 20 phiên sau đó so với trung bình thị trường):

| | IC | Nửa đầu | Nửa sau | 20 mã điểm cao nhất vượt TB |
|---|---|---|---|---|
| Điểm cũ (50% PTCB + 50% PTKT) | +0,030 | +0,072 | −0,013 | +0,68%/20 phiên |
| **Điểm mới** | **+0,110** | +0,108 | +0,112 | **+1,29%/20 phiên** |

- Bỏ khỏi điểm: tăng trưởng LN/doanh thu, MACD, RS/động lượng 3–12 tháng, RSI, các mốc MA chi tiết (không dự báo ổn định).
- Mã vừa tăng mạnh 5 phiên nói chung **không** tiếp tục vượt thị trường (−0,3%); trong đó mã điểm ≥ 60 vượt +2,2%,
  mã điểm < 45 kém −2,3% → tab "Đang tăng mạnh" gắn nhãn theo điểm.
- Danh sách Top mới vượt TB +1,4%/20 phiên ở cả hai nửa giai đoạn (cũ: +0,7%, nửa sau chỉ +0,3%).
- Nghiên cứu được chạy lại mỗi sáng thứ Bảy (tab Kiểm định → "Điểm số có dự báo được không?").

**Đủ thanh khoản**: GTGD TB 20 phiên ≥ 5 tỷ (≥ 200 phiên dữ liệu) **hoặc** GTGD TB 5 phiên ≥ 10 tỷ (≥ 120 phiên),
giá ≥ 5.000đ — để không bỏ sót mã vừa có dòng tiền vào.

**Sự kiện quyền** (chia cổ tức bằng cổ phiếu, thưởng…): nguồn giá điều chỉnh lùi lịch sử. Nhật ký tín hiệu tự quy đổi
giá ghi nhận; tab Danh mục báo "Cần cập nhật giá vốn" khi giá vốn cao hơn mọi mức giá (đã điều chỉnh) kể từ ngày mua.

## Tuỳ chỉnh (`cau_hinh.json`)
| Khoá | Ý nghĩa |
|---|---|
| `san` | Sàn đưa vào bảng: `HOSE`, `HNX`, `UPCOM` |
| `loai_doanh_nghiep` | `CT` doanh nghiệp thường, `NH` ngân hàng, `CK` chứng khoán, `BH` bảo hiểm |
| `loc_thanh_khoan` | GTGD TB 20 phiên / 5 phiên tối thiểu (tỷ đồng), giá tối thiểu, số phiên tối thiểu |
| `loc_top` | Điều kiện vào danh sách Top (điểm tối thiểu, yêu cầu xu hướng tăng) |
| `dang_tang` | Ngưỡng tăng 5 phiên, GTGD tối thiểu, ngưỡng điểm "có nền tảng" / "đầu cơ" |
| `co_hoi`, `danh_muc` | Quy tắc tab Cơ hội hôm nay và Danh mục |

## Hạn chế cần biết
- Dữ liệu từ API công khai, có thể chậm cập nhật hoặc sai lệch; BCTC chỉ có sau khi doanh nghiệp công bố.
- Tăng trưởng tính từ LNST cổ đông công ty mẹ, chưa loại trừ lợi nhuận bất thường (thanh lý tài sản, hoàn nhập dự phòng...).
- Điểm số chỉ là bộ lọc sàng lọc ban đầu, **không phải khuyến nghị mua/bán**. Cần đọc BCTC, tin tức,
  kế hoạch kinh doanh và có kế hoạch quản lý rủi ro (điểm cắt lỗ, tỷ trọng) trước khi ra quyết định.
