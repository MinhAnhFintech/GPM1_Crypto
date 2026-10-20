# TV4 còn chờ bàn giao gì? — cập nhật 21/10/2026

Đối chiếu GPM1_CRYPTO.xlsx (C4.01–C4.13, R4.01/R4.02, Mốc và Phụ thuộc) và PDF mục 2, 3.3, outline báo cáo. Trạng thái dưới đây tổng hợp các bàn giao đã kiểm và lượt dựng D3/D4 thật ngày 21/10. Không cập nhật trạng thái Excel từ tài liệu này.

## Ưu tiên trước M2

| TV4 cần làm | Chờ ai / mã việc | Cần nhận cụ thể | Mốc gốc |
| --- | --- | --- | --- |
| C4.08 dựng D3, C4.09 đóng D4 trên dữ liệu thật | Đã thực hiện và kiểm kỹ thuật trên D1/T0 của TV2 qua PR #9 | D3 501.119 × 24, đủ 6 venue, không missing; D4 300.671/100.223/100.225, fingerprint/biên hợp lệ, final test chưa dùng. Không còn chờ TV2 hoặc code TV4 cho bước nền này | Mốc gốc: TV2 trước **12:00 ngày 20/10**; TV4 M2 cuối 20/10 |
| C4.08/C4.09 giữ đúng mẫu nghiên cứu | TV1 C1.02 cùng kết quả khả thi TV3 C3.01 | Xác nhận sample.start/end theo ts_available; config hiện đã có nhưng còn chú thích cần nhóm xác nhận khoảng mẫu. Danh sách sàn đã được nhóm chốt, không còn là đầu vào đang chờ | Trước khi đóng M2 |

Đã nhận và kiểm D1 thật của TV2 qua PR #9, chi tiết trong docs/TV2_PR9_REVIEW.md. Ngày 21/10 đã dựng và kiểm D3/D4 thật theo sáu venue trong config; các artifact nằm đúng đường dẫn data/processed, giữ local và được ignore. C4.08/C4.09/C4.10 đã có bằng chứng kỹ thuật trên mẫu hiện tại; xem docs/TV4_HISTORICAL_HANDOFF.md. Không còn chờ TV2 giao D1; M2 vẫn cần nhóm xác nhận TODO về khoảng mẫu trong config. Chưa gửi tin nhắn hoặc xuất bản bản báo cáo chung cho các thành viên.

Cập nhật nguồn: bộ raw cũ khớp sáu SHA-256 người gửi được giữ nguyên để đối chiếu. D1 Ny gửi qua PR #9 đã khớp hash và tái lập từ bộ raw mới; raw mới lưu riêng trong data/raw/ohlcv/_handoffs/pr9-db2cabd/. C3.03/C3.04 được review ở PR #8, chi tiết trong docs/TV3_PR8_REVIEW.md. D2 v1.1 và kiểm tương thích đã có; vẫn chờ D2 live thật và coverage theo phiên để nghiệm thu nhánh live.

Nhóm đã chốt nghiên cứu Binance, OKX và Bybit, mỗi sàn gồm spot và perp. Thu live rộng hơn vẫn hợp lệ: config/live_sessions.yaml còn Kraken spot và Coinbase spot để giữ dữ liệu tại thời điểm thu. Không xóa dữ liệu thu dư hoặc thu hẹp collector; kiểm D2 thu rộng không truyền config nghiên cứu, còn bàn giao cho phân tích 6 venue dùng bản đã chọn riêng. sample lịch sử không áp vào D2 live.

M2 và lead-lag từ D3 không phụ thuộc mô hình IS/ILS. D2 live và funding/OI phục vụ các nhánh phân tích khác, không là đầu vào bắt buộc của D1→D3/D4.

## Các nhánh công việc còn lại

| Việc TV4 | Người/đầu vào còn chờ | Tình trạng và bước TV4 sẽ làm khi nhận |
| --- | --- | --- |
| C4.10 số liệu lead-lag/F6 thật | Đã chạy trên development của D3 thật | 165 giá trị cho 15 cặp × 11 lag; kiểm độc lập NumPy khớp. Cả 15 đỉnh ở lag 0; không gán leader IS/ILS từ tương quan này |
| Tích hợp/kiểm đồng bộ D2 thật, hỗ trợ C4.11/C4.12 | TV3 C3.05/C3.06: `live_quotes_long.parquet`, báo cáo T0b coverage theo phiên và checksum | Có code nhận/đồng bộ D2. Chưa có file D2 chuẩn trong data/processed; cần kiểm UTC, size BTC, depth censored, provenance và tuổi quote trước tích hợp |
| C4.13 kiểm chứng IS/CS/ILS trên fixtures | Đã hoàn thành kiểm chứng và merge PR TV5 #7 vào dev d318a36 ngày 20/10 | Strict 4/4 trên original/identified_cs × multi/spot_perp; không còn chờ hàm TV5. Xem docs/TV5_PR7_REVIEW.md. Kết quả synthetic không thay nghiệm thu mẫu thật |
| C4.12 pipeline nghiên cứu đầy đủ | CLI và đầu ra thật của các stage ở bảng dưới | Khung điều phối đã có; pipeline_stages hiện rỗng. Chỉ đăng ký command đã bàn giao/đã review, rồi chạy nối chuỗi và kiểm đầu ra, lỗi/timeout, provenance và tái lập |
| C4.11 review hằng ngày | PR/commit tiếp theo của các thành viên | Việc liên tục tới 24/10; đã xử lý PR #7/#8/#9, tiếp tục khi nhóm bàn giao thêm |
| R4.01 phương pháp 4.1/4.10 | Nhóm xác nhận khoảng mẫu; TV1 đưa nội dung vào báo cáo chung | Đã viết và bổ sung số dòng/biên/missing thật; không còn chờ dữ liệu để viết phần này |
| R4.02 kiến trúc và tái lập | Kết quả C4.12 đầy đủ | Đã có kiến trúc/lệnh nền, bằng chứng IS/ILS C4.13 và lượt tái lập D3/D4 thật; còn các stage nghiên cứu để hoàn tất phụ lục toàn pipeline |

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
