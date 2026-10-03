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

## Tiêu chí chấm điểm

**Phân tích cơ bản (100đ)**: ROE · tăng trưởng LNST 4 quý · tăng trưởng LNST quý gần nhất so với cùng kỳ ·
tăng trưởng doanh thu · P/E so với trung vị ngành · PEG · sức khoẻ tài chính (nợ vay/VCSH, thanh toán;
ngân hàng dùng nợ xấu, NIM).

**Phân tích kỹ thuật (100đ)**: xu hướng (giá so với MA20/50/200, MA200 đi lên) · động lượng (RSI14, MACD) ·
sức mạnh giá tương đối RS 1–99 (kiểu IBD) · khoảng cách tới đỉnh 52 tuần · dòng tiền (khối lượng).

**Điểm tổng** = 50% PTCB + 50% PTKT. Thang điểm chi tiết xem sheet *Giai thich* trong file Excel.

## Tuỳ chỉnh (`cau_hinh.json`)
| Khoá | Ý nghĩa |
|---|---|
| `san` | Sàn cần lọc: `HOSE`, `HNX`, `UPCOM` |
| `loai_doanh_nghiep` | `CT` doanh nghiệp thường, `NH` ngân hàng, `CK` chứng khoán, `BH` bảo hiểm |
| `loc_thanh_khoan` | GTGD TB 20 phiên tối thiểu (tỷ đồng), giá tối thiểu, số phiên tối thiểu |
| `trong_so` | Tỷ trọng PTCB / PTKT trong điểm tổng (VD: thiên về đầu tư giá trị → `0.7 / 0.3`) |
| `loc_top` | Điều kiện để vào danh sách Top |

## Hạn chế cần biết
- Dữ liệu từ API công khai, có thể chậm cập nhật hoặc sai lệch; BCTC chỉ có sau khi doanh nghiệp công bố.
- Tăng trưởng tính từ LNST cổ đông công ty mẹ, chưa loại trừ lợi nhuận bất thường (thanh lý tài sản, hoàn nhập dự phòng...).
- Điểm số chỉ là bộ lọc sàng lọc ban đầu, **không phải khuyến nghị mua/bán**. Cần đọc BCTC, tin tức,
  kế hoạch kinh doanh và có kế hoạch quản lý rủi ro (điểm cắt lỗ, tỷ trọng) trước khi ra quyết định.
