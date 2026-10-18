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

## Phần có thể hoàn thiện độc lập của TV4

Đối chiếu sheet «Việc Code», hàng 32-44, sheet «Việc Báo cáo», hàng 22-23 và PDF mục 3.1-3.4: các việc độc lập là nền repo, schema, fixtures, tiện ích, templates, thuật toán đồng bộ/chia mẫu/lead-lag, khung điều phối và phần phương pháp/phụ lục. Không lấy trạng thái trong Excel làm bằng chứng đã có dữ liệu thật hoặc hàm mô hình.

| Công việc | Phần đã sẵn sàng trong repo | Điều kiện nghiệm thu còn chờ |
| --- | --- | --- |
| C4.01-C4.07 | Repo, hợp đồng, hai bộ fixtures, tiện ích, mẫu code và test thời gian | Không có phụ thuộc code mới cho phần nền |
| C4.08-C4.09 | D1 -> D3 -> D4, fingerprint và cơ chế claim test cuối; kiểm chứng bằng fixtures | D1 mẫu nghiên cứu thật của TV2 để bàn giao M2 |
| C4.10 | Lead-lag với quy ước lag dương: venue_a dẫn venue_b; thu lại độ trễ đã cài trên cả hai bộ fixtures | D3 thật để xuất số liệu nghiên cứu/F6 |
| C4.11 | Có quy trình và test tích hợp PR; tiếp tục review khi thành viên nộp | PR mới của nhóm; công việc kéo dài tới 24/10 |
| C4.12 | Điều phối argv, kiểm tra cấu hình, timeout, đầu ra từng bước và báo cáo trạng thái mỗi lần chạy | CLI/đầu ra thật của năm bước nghiên cứu do nhóm bàn giao |
| C4.13 | Bộ test gọi đúng hàm thật, giữ phân biệt skip và nghiệm thu bắt buộc | TV5 bàn giao VECM/IS/CS/ILS |
| R4.01 | Bản phương pháp 4.1 và 4.10 ở trên; không cần số liệu nghiên cứu để viết | Đối chiếu biên mẫu thực tế sau M2 |
| R4.02 | Kiến trúc, lệnh chạy từng bước và cách đọc bằng chứng kiểm thử ở dưới | Điền kết quả IS/ILS thật sau C4.13 |

### Chạy và tái lập từng bước (C4.12, R4.02)

Các lệnh chạy từ gốc repo trong môi trường Python đã cài requirements.txt. Hai lệnh đầu tạo dữ liệu và kết quả **synthetic**, không cần mạng hoặc thành viên khác:

```powershell
python run_all.py --mode synthetic --foundation-only
python run_all.py --mode synthetic --fixture-case spot_perp --foundation-only
python -m pytest -q
```

Đầu ra tương ứng nằm trong `outputs/synthetic/multi` và `outputs/synthetic/spot_perp`: `data/panel_1m_wide.parquet`, `data/split_manifest.json`, `tables/T0_alignment_quality.csv`, `tables/T2_leadlag_xcorr.csv`, hình F1/F6 và `pipeline_report.json`. T2_leadlag_xcorr là bảng kỹ thuật cho F6; không phải bảng T2 kiểm định của TV5. Tất cả phân tích lead-lag và hình giá ở pipeline dùng development, không mở final test.

Khi TV2 bàn giao D1 thật và TV1 chốt config/config.yaml với sample.start/end có timezone (end không bao gồm), chạy riêng nền để dựng D3/D4:

```powershell
python run_all.py --mode historical --config config/config.yaml --foundation-only
```

Nếu đầu vào là các file thô đúng mẫu `{venue}_BTCUSDT_1m.parquet`, có đủ venues đã chốt trong cấu hình, dùng:

```powershell
python run_all.py --mode raw --config config/config.yaml --foundation-only
```

Chế độ raw chuẩn hóa và lưu D1 vào data/interim trước khi dựng D3/D4. Có thể chọn thư mục raw bằng `--raw-dir`, D1 đọc sẵn bằng `--input` ở chế độ historical, thư mục D3/D4 bằng `--data-dir` và thư mục báo cáo bằng `--output-dir`. Các chế độ này không tự tải dữ liệu hoặc chạy collector live. Không gán nhãn historical cho fixtures để thay bàn giao thật.

### Giao diện đăng ký bước nghiên cứu

`pipeline_stages` là danh sách trong YAML, thực thi theo thứ tự khai báo. TV4 chỉ đăng ký command sau khi thành viên bàn giao CLI thực sự và test trên fixtures; không tự tạo kết quả mô hình thay thành viên. Năm tên dùng để kiểm tra độ đầy đủ là `features`, `regime_flags`, `price_discovery`, `cost_backtest`, `evaluation`.

| Trường | Quy tắc |
| --- | --- |
| name | Chuỗi không rỗng, duy nhất trong lần chạy |
| enabled | Boolean, mặc định true; false ghi disabled, không chạy |
| command | Danh sách argv các chuỗi, không dùng shell; phần tử đầu là executable |
| outputs | Danh sách file bắt buộc, không trùng giữa các bước; bước phải tạo hoặc ghi lại từng file trong lần chạy hiện tại |
| timeout_s | Số giây dương hữu hạn, mặc định 3600; chọn theo thời gian thực tế của bước |

Các token `{python}`, `{data_dir}`, `{output_dir}` được thay bằng Python hiện chạy và đường dẫn tuyệt đối. Cả command và outputs dùng cùng quy tắc đường dẫn: đường dẫn tương đối tính từ gốc repo, kể cả khi gọi run_all.py từ thư mục khác. Các dấu ngoặc khác trong argv giữ nguyên. Nếu bước chủ động dùng cache, CLI vẫn phải xuất lại file đã khai báo; chỉ tồn tại file từ lần trước không đủ để được đánh dấu passed.

Sau khi đăng ký đủ CLI, chạy lại historical hoặc raw và bỏ `--foundation-only` để gọi các bước của nhóm. Cờ `--foundation-only` luôn bỏ qua việc khởi chạy chúng. Một bước thoát mã lỗi, quá timeout, thiếu đầu ra hoặc để nguyên file cũ sẽ dừng chuỗi; bước sau không chạy. File cũ không bị xóa bởi kiểm tra freshness.

### Đọc báo cáo và điều kiện hoàn tất

`pipeline_report.json` được khởi tạo lại với run_id mới ngay khi bắt đầu, rồi cập nhật nguyên tử khi bước chạy/hoàn tất/lỗi. Vì vậy lần chạy thất bại không để báo cáo passed của lần trước đứng thay cho kết quả hiện tại. foundation_status mô tả phần nền; pipeline_status phân biệt running, foundation_only, pending_team_stages, passed và failed. team_stages giữ trạng thái, thời lượng và SHA-256 đầu ra của các bước đã chạy; pending_team_stages giữ các bước bắt buộc chưa passed. Khi lỗi có error_type và thông tin lỗi/mã thoát hoặc timeout tương ứng.

`full_research_pipeline_complete` chỉ có thể true khi chạy dữ liệu historical, đủ năm bước passed và không dùng foundation-only. Chạy synthetic thành công vẫn không được đánh dấu hoàn tất nghiên cứu thực nghiệm. Cấu hình đăng ký đủ bước và lệnh chạy thành công cũng không thay thế review phương pháp và dữ liệu của từng bước.

Để kiểm chứng riêng khung điều phối mà chưa cần TV2/TV3/TV5/TV6, chạy `python -m pytest tests/test_pipeline_stages.py tests/test_pipeline.py -q`. Bộ này dùng subprocess cục bộ trong thư mục tạm để kiểm tra thứ tự chạy, đường dẫn có khoảng trắng, lỗi/timeout, file thiếu/cũ và cập nhật báo cáo; các file thử nghiệm không phải kết quả nghiên cứu. Để nghiệm thu C4.13 sau bàn giao TV5, chạy `python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q`; hiện vẫn phải báo thiếu phụ thuộc.

Kiểm tra sau khi hoàn thiện khung điều phối ngày 18/10/2026: toàn repo **124 passed, 2 skipped** (hai ca C4.13 chờ TV5); demo multi và spot_perp đều dựng 720 dòng, final test chưa dùng. Đây là bằng chứng phần nền/điều phối, không đóng M2 hoặc nghiệm thu mô hình IS/ILS khi chưa có bàn giao thật.
