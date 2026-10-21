# Review TV2 PR #10 — C2.07 (21/10/2026)

PR: https://github.com/MinhAnhFintech/GPM1_Crypto/pull/10. Head ban đầu `8685081bd654e27ea6e6086fee1dee2de8bfb83f`, nền `dev` tại `08d1d006d5d5a9a958ee617b011d71e8bdf0d392`. PR ban đầu chọn nhầm `main`; đã đổi base sang `dev`, không sửa main. Diff đúng phạm vi gồm app, sáu ảnh/credits và test dashboard.

## Đối chiếu PCCV

Đọc lại C2.07/C2.08/C2.09 trong GPM1_CRYPTO.xlsx và nguyên tắc thời gian/provenance trong PDF. C2.07 yêu cầu khung Streamlit chạy trên fixtures của TV4 với bảy khối: báo giá có market_type, dispersion, venue dẫn, xếp hạng rolling IS, gross gap, net gap ước tính và cờ arbitrage. App có đủ bảy khối trong các tab; hiển thị USDT/bps/UTC và cảnh báo mô phỏng. Thiếu D6/D7 thì ghi chờ, không tự tạo IS/ILS hay số liệu cơ hội.

Nghiệm thu trong PR này là **C2.07 trên synthetic**, không đóng C2.08 nối dữ liệu thật hoặc C2.09 nghiệm thu sản phẩm cuối. D3 là close nến, chưa phải bid/ask live. Kết nối D2 thật, D6/D7 nghiên cứu và đối chiếu kết quả thật vẫn thuộc bàn giao sau.

## Findings và sửa tích hợp

| Vị trí | Hành vi trước sửa / tác động | Cách xử lý |
| --- | --- | --- |
| `_load_table`, `_default_path` | D6/D7 chỉ kiểm tên cột, không kiểm provenance; panel fixtures có thể hiển thị số historical dưới nhãn synthetic. Tự tìm file synthetic nếu thiếu file chuẩn | Chọn nguồn rõ ràng; không fallback. Yêu cầu data_kind hợp lệ, khớp panel và metadata; sai nguồn thì ghi lý do và bỏ bảng |
| `_load_table`, `leader_of` | Có thể công nhận leader do file ghi sẵn dù bounds chồng nhau; chuỗi thay số, timestamp sai hoặc thiếu timezone không được kiểm đúng | Dùng validator D6/D7 chung, giữ UTC ns đã bàn giao, không tự gán timezone. Lỗi đọc/kiểu được hiển thị; không làm dừng toàn app. Leader chỉ khi status đạt và lower vượt upper của mọi venue khác trong cửa sổ |
| `current_window` | Các cửa sổ cũ có cùng available_at sau khi chọn spec bị ghép thành một bảng xếp hạng | Chọn cửa sổ có window_end mới nhất trong các kết quả đã sẵn sàng, rồi chọn window_start/available_at của cửa sổ đó |
| D6/D7 trong `main` | D7 tương lai hoặc ngoài mẫu vẫn được tổng hợp/đếm là cơ hội của panel đang hiển thị | D6 chỉ nhận cửa sổ trong mẫu và available_at <= asof; D7 chỉ nhận sự kiện bắt đầu trong mẫu, ts_end <= asof. CSV dùng cùng các bảng đã lọc |
| `thin`, biểu đồ dispersion | Đỉnh trong một nhóm bị gán về timestamp đầu nhóm; dropna làm nối đường qua khoảng thiếu | Giữ timestamp thật của đỉnh và marker missing; biểu đồ ngắt đường, hover nêu thời gian điểm được giữ, không lấy điểm tương lai cho hover |
| Kiểm định danh | Bảng D7 có thể chứa tự ghép hoặc venue không thuộc panel; D6 trùng window/venue bị đếm nhiều lần | Từ chối self pair, market_type/venue không hợp lệ, venue không thuộc panel, ID sự kiện trùng và khóa D6 trùng; không sửa hoặc suy diễn lại dữ liệu |

Không sửa collector, số liệu nguồn, công thức chi phí/IS, cấu hình nghiên cứu hoặc fixtures gốc. Các test của Ny dùng D6/D7 đã được bổ sung metadata, UTC ns và các cặp thực sự có trong fixtures thay vì để dữ liệu sai hợp đồng được coi là hợp lệ.

## Kiểm chứng

- Bộ test dashboard ban đầu: **17 passed**. Điều này kiểm giao diện/hàm cơ bản, chưa bao phủ các finding trên.
- Bộ regression bổ sung: 16 ca không đạt trên head ban đầu (gồm ca kiểm API nhận expected_kind mới), sau sửa đều đạt. Các ca tái hiện trực tiếp gồm leader sai bounds, timestamp lỗi, gộp nhiều cửa sổ, peak sai thời điểm, fallback synthetic, duplicate, sự kiện tương lai, self pair và kiểu số sai.
- Toàn repo sau sửa: **423 passed, 1 skipped**, chạy `python -m pytest -q --require-tv5`. Ca skip duy nhất là tạo symlink thư mục không khả dụng trên Windows; strict TV5 đã chạy.
- Streamlit AppTest kiểm đủ bảy khối, file thiếu, hai nguồn D6/D7 hợp lệ, nguồn sai, trạng thái giao diện sáng/tối và không đếm sự kiện tương lai.
- `run_all.py` với stage CLI thật của TV5 đã chạy nối nền synthetic cho cả multi và spot_perp: mỗi bộ 720 dòng, sáu cửa sổ, zero failed; các outputs của stage có SHA-256. Đây là kiểm tích hợp kỹ thuật C4.12, không phải kết quả nghiên cứu thật.
- Final test không bị claim/reset; data thật vẫn local và được ignore. `git diff --check` đạt.

Kết luận: bản đã sửa đủ điều kiện tích hợp **C2.07 vào dev**. Chưa nghiệm thu dữ liệu thật, M3, toàn C4.12 hoặc toàn dashboard.
