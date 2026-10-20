# TV4 review PR #9 — C2.01–C2.03 (20/10/2026)

Base dev f9238b8, head gốc db2cabd, nhánh feat/tv2-c2-01-03. Đối chiếu GPM1_CRYPTO.xlsx, C2.01/C2.02/C2.03 và DATA_CONTRACT D1. Không sửa main hoặc cập nhật trạng thái Excel.

## Kết quả bàn giao dữ liệu

D1 Ny cung cấp trên Drive có SHA-256:

```text
FEA20184F835471EBDD48D43F7D697D36195BAB74F1B7DF3B790D20F9B23E09F
```

Đã tải và đối chiếu hash khớp, kiểm D1 hợp lệ. Có 3.006.714 dòng, sáu venue nghiên cứu, mỗi venue 501.119 dòng; ts_available từ 2025-10-18T00:01:00Z đến 2026-09-30T23:59:00Z. Tái lập D1 từ sáu raw hiện tại trên cùng Drive cho kết quả khớp toàn bộ hàng/cột/giá trị/kiểu dữ liệu. Không cần byte hash của file tái sinh giống file gốc vì metadata/index/cách ghi Parquet có thể khác; file D1 nhận được giữ nguyên bytes theo hash trên.

Sáu file Drive hiện tại có ID và checksum khác bộ raw nhóm gửi trước đó. Bản Binance spot mới đã cắt mẫu và dùng schema ts_open_utc; năm raw CCXT mới có volume_quote. Bản cũ giữ nguyên, kể cả hash đã xác nhận; không gán hash cũ cho file mới. Năm raw CCXT cũ không có quote volume quan sát được, nên khi dùng bản cũ volume_quote phải giữ NaN. Không thay bằng close × volume_base, không sửa hoặc điền dữ liệu gốc.

Tái lập T0 từ bộ raw hiện tại khớp cả sáu dòng outputs/tables/T0_data_quality.csv: không thiếu/trùng phút, không timestamp lệch phút, giá thiếu/không dương, high/low sai hoặc volume âm trong khoảng quan sát. Hai jump >300bps ở binance_perp (395,41) và bybit_perp (306,22) được giữ nguyên và đánh dấu. Ngưỡng 300bps là cấu hình kiểm chất lượng, không phải lý do loại quan sát. Đây là nghiệm thu đầu vào D1, chưa phải D3/D4/M2 hay kết quả mô hình.

## Findings đã xử lý

1. Bộ gộp chỉ nhận raw schema mới, không đọc được file Binance spot cũ đã bàn giao. Dùng chung normalize_raw_ohlcv của TV4 để nhận cả open_time_utc/volume/quote_volume và ts_open_utc/volume_base/volume_quote; availability luôn bằng giờ mở + một phút.
2. validate_long từ chối mọi NaN, bao gồm volume_quote không quan sát ở raw CCXT cũ. Dùng validator D1 chung, giữ quote volume thiếu; volume_base vẫn phải có. Không ước lượng quote volume từ giá đóng cửa.
3. Bộ gộp có thể nhận timestamp không timezone, timestamp lệch phút, giá infinity, venue sai hoặc metadata synthetic; có thể trả bảng thiếu một venue trong mẫu. Kiểm định danh/provenance đã cung cấp, UTC aware, lưới phút, finite/schema, danh sách venue duy nhất và mỗi venue phải có dữ liệu trong sample. Trùng khóa báo lỗi, không tự xóa. D1 được ghi atomically bằng tiện ích chung.
4. Reader T0 rename vô điều kiện các alias, tạo tên cột trùng nếu raw có cả tên mới và cũ. Giữ chọn alias theo thứ tự ưu tiên hiện có thay vì rename cả bảng.
5. Khi sample.start đúng đầu tháng lúc 00:00, nến available tại mốc này mở ở tháng trước. Bộ tải tháng phải tính từ sample.start - một phút; regression offline kiểm tháng trước được tải và nến đầu được giữ. Cắt ra bảng rỗng báo lỗi trước khi thay file raw cũ.

## Kiểm chứng

- Test riêng gốc về D1/T0/collector: 26 passed. Bộ regression mới trước sửa: 14 failed, 1 passed, tái hiện các findings trên.
- Sau sửa: nhóm liên quan 41 passed; toàn repo với --require-tv5 **390 passed, 1 skipped** (Windows không tạo được directory symlink).
- Demo nền multi và spot_perp: mỗi bộ 720 dòng, final test chưa dùng. Các lệnh không khởi chạy collector live hay tải lại toàn bộ lịch sử Binance Vision.
- Nguồn D1, raw mới, biên nhận và bằng chứng so sánh giữ local ngoài Git. D1 được bàn giao tại data/interim/ohlcv_1m_long.parquet cùng receipt; bộ raw mới dùng cho tái lập được lưu riêng data/raw/ohlcv/_handoffs/pr9-db2cabd/. Bộ raw cũ ở thư mục gốc không đổi.

Đầu vào D1 cho C4.08/C4.09 đã nhận; TV4 còn phải dựng D3/D4 và kiểm missing/fingerprint/biên mẫu. Config vẫn có TODO nhóm xác nhận khoảng mẫu; việc kiểm/tái lập ở đây dùng đúng khoảng hiện khai báo, không tự thay quyết định nghiên cứu. Pipeline đầy đủ vẫn chờ các stage khác của nhóm.
