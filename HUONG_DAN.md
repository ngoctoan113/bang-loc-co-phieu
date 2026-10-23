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
  Bốn tab con: *Danh sách Top* (điểm ≥ 65 và giá > MA50 > MA200) · *Tất cả* · *Đang tăng mạnh* (tăng ≥ 8% trong 5 phiên,
  gắn nhãn "Tăng có nền tảng" / "Trung bình" / "Tăng đầu cơ" theo điểm, kèm cột mẫu hình 2 đáy tuần) ·
  *2 đáy tuần* (mã vừa xác nhận hoặc đang hình thành mẫu hình 2 đáy trên nến tuần, chấm 4 tiêu chí chất lượng; chỉ để theo dõi,
  xem mục "Mẫu hình 2 đáy tuần"). Bấm một mã để xem chi tiết; mã có mẫu hình được vẽ thêm đỉnh giữa và đáy trên biểu đồ.
  Khi thị trường Rủi ro cao, tab hiện cảnh báo: kiểm định 3 năm cho thấy điểm số gần như không dự báo được trong bối cảnh đó.
- **Danh mục của tôi**: nhập mã, giá vốn, khối lượng, ngày mua. Trang áp **cùng quy tắc đã kiểm định với Cơ hội hôm nay**:
  Cắt lỗ khẩn cấp (giá vốn −10%) / Hết thời hạn giữ: bán (đủ 20 phiên kể từ ngày mua, trừ khi mã đang có lại tín hiệu) / Giữ.
  Dừng lãi theo ATR, giá dưới MA50/MA200, chốt lời khi giá nóng, tỷ trọng quá lớn… chỉ hiện là **lưu ý**, không phải
  lệnh bán (kiểm định cho thấy thoát lệnh theo các tín hiệu kiểu này cho kết quả kém hơn). Danh mục chỉ lưu trên trình duyệt;
  dùng nút "Tạo link" để mở cùng danh mục trên máy khác (dữ liệu nằm trong link, không gửi lên máy chủ).
- **Thị trường**: điểm bối cảnh 0–100 từ xu hướng VN-Index (MA20/50/200), độ rộng thị trường, ngày phân phối,
  và thế giới (S&P 500, Nasdaq, Shanghai, EEM, DXY, USD/VND, lợi suất TPCP Mỹ 10 năm, dầu Brent, vàng – nguồn Yahoo Finance).

- **Kiểm định**: các cách kiểm tra công cụ có thực sự hiệu quả không.
  - *Nhật ký tín hiệu thực tế* (`nhat_ky/tin_hieu.csv`): mã đạt đủ tiêu chí ở "Cơ hội hôm nay" **khi phiên đã đóng cửa**
    được ghi lại và theo dõi đúng như kiểm định quá khứ: mua giá mở cửa phiên sau, T+2, giữ 20 phiên, cắt lỗ khẩn cấp,
    so với VN-Index cùng kỳ. Mỗi mã chỉ tính một vị thế (tín hiệu lặp lại khi đang giữ không tính thêm); thống kê tách
    "theo trang" (trong giới hạn số mã) và "mọi tín hiệu". Máy chủ GitHub tự ghi và lưu vào kho.
  - *Nhật ký bảng điểm* (`nhat_ky/diem/YYYY-MM.csv`): bảng điểm các mã đủ thanh khoản sau mỗi phiên. Sau 5/10/20 phiên,
    trang đối chiếu điểm với diễn biến thực tế (IC) và so Hạng A, Hạng D, Danh sách Top với VN-Index — phép thử ngoài
    mẫu cho Bảng xếp hạng. Dữ liệu bắt đầu từ 07/10/2026 (bảng điểm 07/10 chạy trên máy cá nhân, 21/10 lấy từ trang web).
  - *Kiểm định quá khứ* (`python kiem_dinh.py` → `nhat_ky/kiem_dinh.json`, máy chủ chạy sáng thứ Bảy hằng tuần):
    dựng lại tiêu chí trên ~3 năm dữ liệu (BCTC tính từ ngày công bố, thanh khoản tại từng thời điểm),
    so sánh khi đạt / không đạt / khi bỏ từng tiêu chí và khi thêm tiêu chí đề xuất, tách hai nửa giai đoạn;
    kèm sức dự báo của điểm số, theo bối cảnh thị trường và **so với VN-Index**, và mục *Mẫu hình 2 đáy tuần có hiệu quả không?*
    (mọi lần xác nhận 2 đáy tuần, so với VN-Index và với một phiên vượt đỉnh bất kỳ, theo từng tiêu chí chất lượng).

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
  (cắt lỗ chặt làm bị "rũ" khỏi mã tốt). Từ 22/10/2026 tab Danh mục cũng dùng quy tắc này.
- Các tiêu chí đã bỏ vẫn được kiểm định hằng tuần trong nhóm "đề xuất" để xem có nên đưa lại.
- Giới hạn: chỉ có mã còn niêm yết (thiên lệch sống sót), mẫu ~140 GD nên kết quả còn dao động; nhật ký thực tế là phép thử quyết định.

## Bản trực tuyến tự cập nhật (GitHub Pages)

Kho này có sẵn workflow `.github/workflows/cap_nhat_web.yml`. Máy chủ GitHub tự chạy bộ lọc theo lịch
(thứ 2–6, giờ VN): mỗi 20 phút trong phiên (9:07–14:47), sau giờ đóng cửa lúc 15:13, 16:43, 19:17 và 7:37 sáng hôm sau,
rồi đăng kết quả lên địa chỉ `https://<tài-khoản>.github.io/<tên-kho>/`. Mở được trên mọi máy và điện thoại,
không cần bật máy tính này. Trang đang mở tự kiểm tra dữ liệu mới mỗi 3 phút (nhãn **Tự cập nhật** màu xanh).

**Thực tế GitHub bỏ phần lớn các lần chạy theo lịch** (10/2026: chỉ 2–3 lần mỗi ngày, khoảng 13:40, 19:30, 20:40,
buổi sáng không có lần nào). Vì vậy nhật ký chỉ ghi khi phiên đã đóng cửa, và có nhiều mốc dự phòng sau giờ đóng cửa:
chỉ cần một mốc chạy được là đủ. Dòng chữ dưới tiêu đề trang cho biết dữ liệu đến phiên nào, cập nhật lúc mấy giờ.
Muốn cập nhật đúng giờ trong phiên thì cần một dịch vụ hẹn giờ bên ngoài (ví dụ cron-job.org) gọi
`POST https://api.github.com/repos/<tài-khoản>/<tên-kho>/actions/workflows/cap_nhat_web.yml/dispatches`
với nội dung `{"ref":"main"}` và một token GitHub chỉ có quyền Actions (đọc/ghi) trên kho này.

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
- GitHub tự tắt lịch chạy nếu kho không có commit nào trong 60 ngày; khi đó bấm bật lại trong tab Actions.
- Muốn tính lại kiểm định quá khứ ngay: tab Actions → Run workflow → tick "Chạy lại kiểm định quá khứ".
- Muốn đổi tiêu chí lọc cho bản trực tuyến: sửa `cau_hinh.json`, commit và push. Workflow chạy lại ngay khi có push.

## Nguồn dữ liệu
- **Vietcap (VCI)**: danh sách công ty, ngành ICB, giá lịch sử ngày, chỉ số tài chính TTM, báo cáo KQKD theo quý.
- **VNDirect**: P/E, P/B, tỷ suất cổ tức, vốn hoá, beta theo giá hiện tại.
- **DNSE**: nguồn giá dự phòng.

Dữ liệu được lưu cache trong `cache/` (giá: 1 giờ, BCTC: 24 giờ để kịp số liệu mới trong mùa báo cáo).

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

**Rà soát 21/10/2026 – so với VN-Index** (mốc trên là so với cổ phiếu trung bình, mà cổ phiếu trung bình kém VN-Index
khoảng 1%/20 phiên trong 3 năm qua). Mua cả nhóm, giữ 20 phiên, 37 kỳ không chồng lấn, chưa trừ phí:

| Nhóm | Hơn VN-Index /20 phiên | Độ tin cậy (t) |
|---|---|---|
| Hạng A | +0,17% | 0,4 (ngang VN-Index) |
| Danh sách Top | +0,47% | 0,9 |
| Hạng D | −2,06% | −3,1 (kém rõ rệt) |

- Điểm số tách mã yếu (Hạng D) rất tốt nhưng chưa chứng minh được việc chọn mã thắng VN-Index.
- Sức dự báo phụ thuộc bối cảnh: IC +0,15 khi thị trường Thuận lợi, chỉ +0,03 khi Rủi ro cao.
- Hai tuần đầu ngoài mẫu (07/10 → 21/10) IC −0,27: nhóm trụ giảm, mã bị bán mạnh hồi lên (điểm cũ cũng âm). Chưa đủ
  để kết luận; nhật ký bảng điểm sẽ cho câu trả lời sau vài tháng. Không chỉnh tiêu chí trên cùng dữ liệu 3 năm này nữa.

## Mẫu hình 2 đáy tuần (rà soát 23/10/2026)

Câu hỏi: nhiều mã tăng mạnh cuối tháng 10/2026 có chung đặc điểm tạo 2 đáy trên nến tuần (đáy cuối tháng 7 và đầu/giữa tháng 10,
cùng nhịp với VN-Index: 1.651 cuối tháng 7, ~1.680 ngày 20/10, đỉnh giữa ~1.874 tháng 9) – có nên thêm thành tiêu chí?
Kiểm tra phiên 23/10: trong 21 mã đủ thanh khoản tăng ≥ 8%/5 phiên, 8 mã (38%) vừa xác nhận 2 đáy tuần trong 40 phiên,
so với 19% ở các mã tăng dưới 10%/10 phiên – có liên hệ, nhưng phần lớn là trùng thời điểm: phiên xác nhận chính là phiên giá vượt lên.

**Định nghĩa** (Bulkowski – *Encyclopedia of Chart Patterns*, phần double bottoms; `tim_hai_day` trong `loc_co_phieu.py`):
có nhịp giảm dẫn vào mẫu hình; hai đáy nến tuần lệch nhau ≤ 6%, cách nhau 4–40 tuần; đỉnh giữa cao hơn đáy thấp hơn ≥ 10%;
**xác nhận** khi giá đóng cửa vượt đỉnh giữa (Bulkowski: chưa xác nhận thì gần một nửa trường hợp giá giảm tiếp).
Mục tiêu tham chiếu = đỉnh giữa + 65% chiều cao mẫu hình (quy tắc đo của Bulkowski).

**Kiểm định** 06/2023–09/2026, mã đủ thanh khoản tại thời điểm đó, mua giá mở cửa phiên sau, giữ 20 phiên, trừ phí 0,4%,
so với VN-Index (t theo cụm tháng vì nhiều mã xác nhận cùng lúc):

| Nhóm | Số lần | Hơn VN-Index /20 phiên | Nửa đầu | Nửa sau | t |
|---|---|---|---|---|---|
| Mọi phiên tăng đóng cửa cao nhất 15 phiên (mốc) | 19.415 | −1,19% | −0,65% | −1,80% | −2,1 |
| Xác nhận 2 đáy tuần | 585 | −1,19% | −1,17% | −1,21% | −1,7 |
| 2 đáy tuần đạt ≥ 3/4 tiêu chí chất lượng | 215 | +0,79% | −0,30% | +2,20% | +1,0 |
| 2 đáy tuần đạt cả 4 tiêu chí | 51 | +0,64% | +1,36% | −0,25% | +0,7 |

- Bản thân mẫu hình **không tốt hơn một phiên vượt đỉnh bất kỳ** và kém VN-Index; giữ lâu hơn còn kém hơn (40 phiên −2,4%,
  60 phiên −4,2%). Nới biên lệch đáy lên 10% (kiểu NKG, HDC, CII tháng 10/2026) cũng vậy (−1,2%). Mua sớm ở đáy 2 trước
  khi xác nhận: −1,1%.
- Tiêu chí chất lượng **tách được mẫu tốt/xấu ở cả hai nửa giai đoạn** (đạt / không đạt): KL tuần đáy 2 thấp hơn đáy 1
  (Bulkowski: lực bán cạn) −0,49% / −2,07% · đáy 2 không thấp hơn đáy 1 −0,81% / −1,72% · ATR ≤ 3% −0,39% / −2,52% ·
  điểm tổng ≥ 60 +0,21% / −1,81%. Bốn tiêu chí này hiện kèm mỗi mẫu hình trên trang.
- Gợi ý trong sách **không có tác dụng** trên dữ liệu Việt Nam: KL bùng nổ ≥ 1,5 lần khi xác nhận (O'Neil), đáy 2 thủng
  đáy 1 để "rũ bỏ" (O'Neil, thực tế kém hơn), RSI tuần phân kỳ dương (kém hơn), vị trí trong biên độ 52 tuần, độ rộng ≥ 7 tuần,
  bối cảnh thị trường.
- Kết luận: **chưa đưa thành điểm mua** của "Cơ hội hôm nay" (đạt ≥ 3/4 tiêu chí có dương nhưng chỉ ở nửa sau, t ≈ 1).
  Mẫu hình hiện ở tab *2 đáy tuần* để theo dõi; nhật ký bảng điểm ghi thêm cột `hai_day` (2 = vừa xác nhận, 1 = đang hình
  thành) từ cuối 10/2026, tab Kiểm định so nhóm vừa xác nhận với VN-Index ngoài mẫu. Kiểm định quá khứ chạy lại mỗi thứ Bảy.
  Sau 2–3 tháng, nếu nhóm đạt ≥ 3/4 tiêu chí vẫn thắng VN-Index thì mới cân nhắc thêm làm điểm mua.

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
| `co_hoi` | Tiêu chí tab Cơ hội hôm nay; cắt lỗ khẩn cấp và thời hạn giữ dùng chung cho cả tab Danh mục |
| `hai_day_tuan` | Định nghĩa mẫu hình 2 đáy tuần (lệch đáy, khoảng cách tuần, chiều cao, số phiên còn hiện sau xác nhận) và ngưỡng ATR / điểm của tiêu chí chất lượng |
| `danh_muc` | Ngưỡng các lưu ý trong tab Danh mục (dừng lãi ATR, chốt lời khi giá nóng, tỷ trọng tối đa) |

## Hạn chế cần biết
- Dữ liệu từ API công khai, có thể chậm cập nhật hoặc sai lệch; BCTC chỉ có sau khi doanh nghiệp công bố.
- Tăng trưởng tính từ LNST cổ đông công ty mẹ, chưa loại trừ lợi nhuận bất thường (thanh lý tài sản, hoàn nhập dự phòng...).
- Điểm số chỉ là bộ lọc sàng lọc ban đầu, **không phải khuyến nghị mua/bán**. Cần đọc BCTC, tin tức,
  kế hoạch kinh doanh và có kế hoạch quản lý rủi ro (điểm cắt lỗ, tỷ trọng) trước khi ra quyết định.
