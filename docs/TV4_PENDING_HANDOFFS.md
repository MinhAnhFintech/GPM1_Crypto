# TV4 còn chờ bàn giao gì? — 19/10/2026

Đối chiếu GPM1_CRYPTO.xlsx (C4.01–C4.13, R4.01/R4.02, Mốc và Phụ thuộc) và PDF mục 2, 3.3, outline báo cáo. Trạng thái dưới đây mô tả phần TV4 đã nhận trong repo và các nhánh GitHub được kiểm tra ngày 19/10. Không cập nhật trạng thái Excel từ tài liệu này.

## Ưu tiên trước M2

| TV4 cần làm | Chờ ai / mã việc | Cần nhận cụ thể | Mốc gốc |
| --- | --- | --- | --- |
| C4.08 dựng D3, C4.09 đóng D4 trên dữ liệu thật | TV2 C2.02/C2.03; TV3 C3.04 cấp dữ liệu thô cho TV2 | `data/interim/ohlcv_1m_long.parquet` đúng D1, data_kind=historical, đủ venue đã chốt và khoảng mẫu; T0 chất lượng + SHA-256 | TV2 giao trước **12:00 ngày 20/10**; TV4 giao M2 cuối 20/10 |
| C4.08/C4.09 giữ đúng mẫu nghiên cứu | TV1 C1.02 cùng kết quả khả thi TV3 C3.01 | Xác nhận sample.start/end theo ts_available; config hiện đã có nhưng còn chú thích cần nhóm xác nhận khoảng mẫu. Danh sách sàn đã được nhóm chốt, không còn là đầu vào đang chờ | Trước khi đóng M2 |

Hiện chưa có D1 thật trong data/interim, chưa có D3/D4 thật trong data/processed. Cấu hình hiện chọn 6 venue. Pipeline raw/historical yêu cầu tập venue bên trong sample khớp chính xác config, báo rõ missing/unexpected trước khi ghi D1/D3/D4. Sau khi nhận D1, TV4 tự kiểm schema/checksum, dựng D3/D4, kiểm missing/fingerprint/biên 60/20/20 và bàn giao TV5/TV6/TV2.

Cập nhật 20/10: đã nhận sáu raw OHLCV từ thư mục bàn giao và chạy T0 sơ bộ. Sau khi Ly gửi thêm năm SHA-256, cả sáu file đều đã đối chiếu khớp hash người gửi; không cần tải lại. D1 chuẩn hóa vẫn là bàn giao tiếp theo; nhận raw chưa thay nghiệm thu D1/D3/M2. C3.03/C3.04 được review ở PR #8, chi tiết trong docs/TV3_PR8_REVIEW.md. D2 v1.1 và kiểm tương thích được bổ sung theo bàn giao Ly; vẫn chờ D2 live thật và coverage theo phiên để nghiệm thu dữ liệu.

Nhóm đã chốt nghiên cứu Binance, OKX và Bybit, mỗi sàn gồm spot và perp. Thu live rộng hơn vẫn hợp lệ: config/live_sessions.yaml còn Kraken spot và Coinbase spot để giữ dữ liệu tại thời điểm thu. Không xóa dữ liệu thu dư hoặc thu hẹp collector; kiểm D2 thu rộng không truyền config nghiên cứu, còn bàn giao cho phân tích 6 venue dùng bản đã chọn riêng. sample lịch sử không áp vào D2 live.

M2 và lead-lag từ D3 không phụ thuộc mô hình IS/ILS. D2 live và funding/OI phục vụ các nhánh phân tích khác, không là đầu vào bắt buộc của D1→D3/D4.

## Các nhánh công việc còn lại

| Việc TV4 | Người/đầu vào còn chờ | Tình trạng và bước TV4 sẽ làm khi nhận |
| --- | --- | --- |
| C4.10 số liệu lead-lag/F6 thật | D1 thật TV2 → D3 do TV4 dựng | Thuật toán và test lag trên hai fixtures đã có; chạy development của D3 thật sau M2, giữ lưới missing và quy ước dấu |
| Tích hợp/kiểm đồng bộ D2 thật, hỗ trợ C4.11/C4.12 | TV3 C3.05/C3.06: `live_quotes_long.parquet`, báo cáo T0b coverage theo phiên và checksum | Có code nhận/đồng bộ D2. Chưa có file D2 chuẩn trong data/processed; cần kiểm UTC, size BTC, depth censored, provenance và tuổi quote trước tích hợp |
| C4.13 kiểm chứng IS/CS/ILS trên fixtures | Đã hoàn thành kiểm chứng và merge PR TV5 #7 vào dev d318a36 ngày 20/10 | Strict 4/4 trên original/identified_cs × multi/spot_perp; không còn chờ hàm TV5. Xem docs/TV5_PR7_REVIEW.md. Kết quả synthetic không thay nghiệm thu mẫu thật |
| C4.12 pipeline nghiên cứu đầy đủ | CLI và đầu ra thật của các stage ở bảng dưới | Khung điều phối đã có; pipeline_stages hiện rỗng. Chỉ đăng ký command đã bàn giao/đã review, rồi chạy nối chuỗi và kiểm đầu ra, lỗi/timeout, provenance và tái lập |
| C4.11 review hằng ngày | PR/commit tiếp theo của các thành viên | Việc liên tục tới 24/10; các nhánh được đọc lần này chưa có cập nhật mới sau các lần tích hợp vừa thực hiện |
| R4.01 phương pháp 4.1/4.10 | Biên và chất lượng mẫu thật sau M2 | Bản phương pháp đã có; điền/đối chiếu số quan sát, missing và biên thực tế khi nhận D1 |
| R4.02 kiến trúc và tái lập | Kết quả C4.12 đầy đủ + C4.13 thật | Bản cấu trúc/lệnh chạy đã có; còn bằng chứng IS/ILS và tái lập pipeline nghiên cứu để hoàn tất phụ lục |

## Bàn giao cần để đăng ký năm stage C4.12

| Stage | Chủ sở hữu | Đầu ra/CLI cần nhận |
| --- | --- | --- |
| features | TV2 C2.04 | CLI tạo D3b features_1m từ D3; giữ từng venue và dispersion riêng spot/perp |
| regime_flags | TV1 C1.04/C1.06 | CLI tạo D5 từ mẫu đúng split; ngưỡng high-vol fit trên development và events có nguồn |
| price_discovery | TV5 | CLI run_price_discovery đã merge từ PR #7 và kiểm thử synthetic; cần review đặc tả và chạy trên D3 thật trước đăng ký stage nghiên cứu. available_at chờ cả window_end và thời điểm chọn spec |
| cost_backtest | TV6, đầu vào D2 từ TV3 | CLI backtest A/B, D7/D8 và bảng chi phí; half-spread proxy cần mẫu live được hiệu chỉnh, không tự đặt số thay dữ liệu |
| evaluation | TV3 + TV6 | CLI hồi quy/stress/sensitivity và các bảng T4/T6/T5b/T6b phù hợp; không dùng final test để chọn lại cấu hình |

Funding/OI D9 của TV3 và D5 regime của TV1 là phụ thuộc của các stage nghiên cứu sau M2. Dashboard TV2 là sản phẩm riêng; TV4 không cần chờ dashboard để bàn giao D3/D4.

## Phần TV4 đã tự xử lý trong lượt sửa này

- Chặn synthetic ghi vào vùng nghiên cứu qua data_dir/output_dir, kể cả đường dẫn đã resolve; kiểm các outputs khai báo của stage trước khi tạo dữ liệu. Command của thành viên vẫn cần review, vì kiểm đường dẫn khai báo không phải sandbox cho mọi hành vi của command.
- Dùng chung validate_panel_split trong các bước tạo/đọc manifest, chọn dev/val, claim test và công cụ intake. Kiểm fingerprint, biên và metadata chia mẫu trước khi dùng, giữ cờ/khóa test hiện có.
- Kiểm config venues hợp lệ và đủ/đúng trong sample ở cả raw/historical; không ghi dữ liệu khi thiếu/thừa venue.
- Các phần nền/schema/fixtures/templates/đồng bộ/lead-lag và bản nháp phương pháp có thể tiếp tục bảo trì mà không chờ mô hình thành viên khác.

Chưa thể đánh dấu toàn bộ TV4 hoặc M2/M3 hoàn tất bằng fixtures. Phần đang chờ có đầu vào và chủ sở hữu cụ thể như trên; không tự viết thay mô hình TV5/TV6 để đóng checklist.
