# Phần báo cáo của Anh (TV4)

## 4.1 Đồng bộ thời gian và kiểm soát look-ahead (R4.01)

Đơn vị quan sát là venue, được định nghĩa bởi sàn giao dịch và loại hợp đồng, ví dụ binance_spot và binance_perp. Dữ liệu dài lưu exchange và market_type riêng; bảng rộng dùng hậu tố venue để tránh gộp giá spot với perpetual của cùng một sàn. Mọi thời điểm lưu bằng datetime64[ns, UTC].

Với nến một phút, giá đóng cửa chỉ được coi là biết được tại thời gian mở nến cộng một phút. Do đó nhóm sử dụng ts_available, không dùng timestamp mở nến hay close_time sớm hơn biên phút của nhà cung cấp. Với báo giá live, thông tin có hiệu lực từ ts_recv_utc; thời gian sàn ts_event_utc chỉ giúp mô tả độ trễ và có thể không có.

Để đưa các venue lên cùng lưới một phút, nhóm dùng previous-tick. Tại thời điểm lưới g, thuật toán chỉ tìm quan sát của đúng venue có ts_available không lớn hơn g, rồi chọn quan sát gần g nhất. Nếu tuổi g-ts_available vượt max_age, giá và volume của venue được để trống và missing__{venue}=True. Ở biên đúng max_age, quan sát vẫn hợp lệ. Thuật toán không nội suy hoặc tìm giá phía sau g để lấp chỗ thiếu.

Kiểm thử bao gồm: dữ liệu tương lai không xuất hiện trong quá khứ, giá cũ hết hạn thành missing, không có nội suy, timestamp phải ở UTC, cùng sàn khác loại hợp đồng giữ tách biệt và việc nối thêm quan sát tương lai không đổi panel quá khứ. Các trường hợp này kiểm tra trực tiếp bất biến thời gian, không suy ra từ việc code chạy không lỗi.

## 4.10 Chia mẫu và quy tắc test cuối (R4.01)

Panel được chia theo thứ tự thời gian thành development/validation/final test với tỷ lệ 60/20/20. Số dòng development và validation làm tròn xuống; phần còn lại dành cho test. Nhóm giữ nguyên dòng missing trong lưới khi chia và lưu các biên không giao nhau trong split_manifest.json. Manifest ghi số quan sát, provenance và fingerprint SHA-256 của dữ liệu, schema, UTC index và metadata để phát hiện thay đổi đầu vào.

Tham số và lựa chọn đặc tả được phát triển trên development, xác nhận trên validation rồi đóng băng. Final test chỉ được dùng một lần sau khi ghi checksum của cấu hình đóng băng. API claim_final_test dùng khóa độc quyền và đánh dấu đã tiêu thụ test; chạy lại pipeline không đặt lại cờ này. Quy trình hiện mặc định chỉ tính lead–lag trên development và không tự mở final test. Việc quản lý quyền đọc dữ liệu gốc vẫn là trách nhiệm quy trình của cả nhóm.

## Phụ lục: kiến trúc và tái lập (R4.02)

Mã thu thập nằm ở src/collectors; tiện ích UTC, schema và I/O ở src/utils; đồng bộ, chuẩn hóa và chia mẫu ở src/synchronization; lead–lag ở src/price_discovery/leadlag.py. Module kinh tế lượng và IS/CS/ILS do TV5 cung cấp; chi phí và backtest do TV6 cung cấp. run_all.py kết nối phần nền và các bước nghiên cứu đăng ký bằng argv trong cấu hình, kiểm tra mã thoát và file đầu ra, rồi ghi pipeline_report.json. Khi còn thiếu bước nghiên cứu, chế độ production đầy đủ báo thiếu và trả mã lỗi, không nhận là đã hoàn tất.

Tái lập demo bằng `python run_all.py --mode synthetic --verify`. Lệnh tạo dữ liệu giả lập từ seed cấu hình, kiểm schema, dựng panel, tạo manifest chia mẫu, xuất bảng lead–lag, hình F1/F6 và minh họa OLS-HAC. Dữ liệu giả lập được giữ trong data/fixtures, đầu ra trong outputs/synthetic, có nhãn synthetic trên hình và metadata.

Bộ fixtures gồm nhiều venue với binance_spot dẫn và một cặp spot/perp có binance_perp dẫn. Chúng dùng cùng một quá trình giá ngẫu nhiên tích lũy, độ trễ riêng từng venue và nhiễu dừng độc lập. Truth.json lưu venue dẫn và độ trễ đã cài. Kiểm chứng lead–lag phải thu lại độ trễ này; việc thu lại IS/CS/ILS phải chạy hàm thật của TV5 qua tests/test_info_share_synthetic.py. Hai nhóm bằng chứng không thay thế nhau.

## Kiểm tra bàn giao và ảnh hưởng tích hợp (18/10/2026)

Theo C4.02/C4.04/C4.11, TV4 đối chiếu mô hình chi phí C6.01/C6.02 với hợp đồng dữ liệu và kiểm tra trước khi tích hợp vào dev. Công thức gross/net, phí taker, chữ ký hàm và tên cột bàn giao được giữ nguyên. Kiểm tra đầu vào bổ sung chặn thời gian sai UTC, trộn nguồn, giá không hợp lệ, book có bid vượt ask và chi phí âm hoặc không hữu hạn; quan sát missing không sinh cơ hội. Các trường hợp hợp lệ tiếp tục qua đủ 19 test gốc của TV6.

Phí maker do PR thêm còn được ghi là placeholder, nên để null cho tới khi TV1 xác minh; gọi kịch bản maker khi chưa có phí sẽ báo thiếu. Half-spread cho proxy lịch sử vẫn phải lấy từ mẫu live. Đây là hai đầu vào còn chờ, không được thay bằng số giả để tạo kết quả nghiên cứu.

Validator D6 được đối chiếu lại với C5.04, sheet «Việc Code», hàng 48: Component Share phải hữu hạn và giữ nguyên giá trị, kể cả ngoài [0,1]. Chỉ IS và ILS bị ràng buộc trong [0,1]. Không đổi cột, đơn vị, schema bàn giao hoặc fixtures D1-D3.

Kiểm chứng trên Python 3.12 với dependencies ghim theo requirements.txt:

- `python run_all.py --mode synthetic --verify`: **104 passed, 2 skipped**; foundation multi thành công, 720 dòng, final test chưa dùng.
- `python run_all.py --mode synthetic --fixture-case spot_perp`: foundation thành công, 720 dòng, final test chưa dùng.
- Kiểm tra tương thích trong bản sao nhánh TV6 gap-events sau khi thay đúng hai module costs/pairs đã review: **36 passed** cho test costs và gap-events; runner multi sinh 500 sự kiện synthetic, cặp spot/perp sinh 0 sự kiện đúng quy tắc không arbitrage chéo loại hợp đồng. Nhánh C6.03 chưa được tích hợp trong lần bàn giao này.

C4.13 vẫn chờ TV5 bàn giao fit_vecm/hasbrouck_is/component_share/information_leadership. Test thường skip và ghi rõ hàm thiếu; `--require-tv5` phải fail cho tới khi có hàm thật, không tính skip là bằng chứng IS/ILS đúng. C4.08/C4.09 có code nền và kiểm thử fixtures nhưng chưa nghiệm thu M2 trên mẫu nghiên cứu thật: checkout hiện chưa có D1/D2 thật trong data/interim và data/processed. C4.12 chưa thể được coi là pipeline nghiên cứu đầy đủ khi còn thiếu các bước của nhóm.
