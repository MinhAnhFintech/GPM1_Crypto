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

## Bàn giao đồng bộ quotes live cho TV6 (C4.04/C4.06/C4.11)

`src/synchronization/live.py:align_live_quotes` bổ sung previous-tick cho D2 theo ts_recv_utc, độc lập với đồng bộ nến D1. Mỗi venue chỉ dùng thông tin đã nhận tại mốc lưới; giá, size và depth quá tuổi thành missing. Cột nguồn và tuổi quote được giữ để kiểm tra. Lưới kết thúc tại floor(lần nhận cuối), không sinh mốc tương lai. Chi tiết giao diện và đơn vị nằm trong DATA_CONTRACT.md; schema D1–D9 giữ v1.0.

Runner C6.03 dùng hàm này trước compute_gaps. Với D2 lệch lưới, phải khai báo `gap_events.live_max_age_s` trong bản config đã review hoặc truyền `--live-max-age-s` theo giây; CLI ưu tiên. Chọn tuổi quote dựa trên nhịp nhận dữ liệu thật. Không tự áp ngưỡng 5 phút của nến hoặc dùng thông số synthetic làm hiệu chỉnh live. Khi chạy fixtures đúng lưới và không khai báo tuổi, runner dùng khớp chính xác như trước.

Sau khi cấu hình tuổi quote, chạy D2 đã bàn giao bằng:

```powershell
python -m src.cost_model.run_gap_events --quotes data/processed/live_quotes_long.parquet --config config/config.yaml
```

`tests/test_live_alignment.py` kiểm tra nhận lệch nhịp, không nhìn trước, biên hết tuổi, giữ nguồn UTC/NaT qua Parquet, spot/perp độc lập, trùng khóa, provenance và luồng D2 -> D7. Trường hợp synthetic lưới 5 giây, tuổi tối đa 6 giây tạo hai sự kiện riêng khi một chân hết tuổi, không kéo dài cơ hội qua khoảng missing. Kết quả gap trên fixtures đúng lưới phải bằng cách pivot cũ. Đây là kiểm chứng thuật toán và tích hợp, chưa nghiệm thu dữ liệu live thật hoặc C4.13 của TV5.

Kiểm chứng ngày 18/10/2026 trên Python 3.12 với dependencies ghim: `python run_all.py --mode synthetic --verify` đạt **170 passed, 2 skipped**; hai ca skip vẫn chờ hàm thật của TV5. Foundation multi và spot_perp đều dựng 720 dòng, final test chưa dùng. Runner gap-events multi tạo 500 sự kiện synthetic, spot_perp tạo 0 sự kiện theo quy tắc không arbitrage chéo loại hợp đồng. Chưa có kết quả thực nghiệm từ D2 live thật trong lần bàn giao này.

## Công cụ kiểm tra bàn giao D1/D2/D3/D4 (19/10/2026)

Theo C4.04/C4.11, `python -m src.utils.check_handoff` kiểm tra file nhận từ nhóm bằng schema hiện có, provenance khai báo rõ, checksum tùy chọn và đối chiếu venue/config. Với D3 có thể đối chiếu manifest D4, fingerprint và biên chia mẫu 60/20/20 mà không sửa manifest hoặc claim/reset final test. Hướng dẫn lệnh và phạm vi kết luận ở `docs/TV4_HANDOFF_CHECK.md`.

CLI phân biệt passed/failed/missing và mã thoát 0/1/2. D1 được thống kê trong/ngoài mẫu theo ts_available, D3 phải đúng lưới trong mẫu, còn D2 không chịu khoảng sample lịch sử. Passed chỉ xác nhận hợp đồng và tính nhất quán đã kiểm; chưa phải nghiệm thu coverage hoặc M2 trên dữ liệu thật. Fixtures vẫn dùng nhãn synthetic và đường dẫn outputs/synthetic.

Kiểm chứng ngày 19/10/2026: toàn repo **226 passed, 2 skipped** (hai ca C4.13 chờ TV5), gồm 31 ca mới cho công cụ bàn giao. Multi và spot_perp foundation đều dựng 720 dòng, final test chưa dùng. CLI kiểm D3/D4 synthetic trả passed, fingerprint và counts 432/144/144 khớp; kiểm D1 historical ở đường dẫn bàn giao hiện trả missing/mã 2. Test xác nhận không thay dữ liệu, manifest hoặc khóa final test và không cho JSON chẩn đoán ghi đè các file này. Chưa có nghiệm thu D1/D2/D3/D4 thật trong lần triển khai này.

## Khắc phục sau rà soát tech lead (19/10/2026)

Ba điểm F1–F3 được sửa trong phần TV4: kiểm đường dẫn synthetic và outputs khai báo của các stage; đối chiếu D4 với panel/tỷ lệ tại tất cả API dùng split; kiểm venue trong khoảng sample ở cả raw/historical. Tham số đường dẫn tùy chọn không được đưa synthetic vào vùng nghiên cứu. Manifest bị lệch biên dù fingerprint dữ liệu giữ nguyên bị từ chối trước khi trả dev/val hoặc tạo khóa test. Thiếu/thừa venue báo lỗi trước khi ghi D1/D3/D4.

Kiểm thử: **261 passed, 3 skipped**. Hai ca skip vẫn chờ TV5; một ca kiểm alias directory bị skip do máy Windows không cho tạo symlink. Các ca đường dẫn thường/`..`, bảo toàn marker nghiên cứu, stage preflight, cả ba API split và hai chế độ raw/historical đã pass. Demo multi dựng 720 dòng, final test chưa dùng. Chi tiết đầu vào TV4 còn chờ và người bàn giao nằm ở `docs/TV4_PENDING_HANDOFFS.md`; chưa có nghiệm thu M2 hoặc kết quả mô hình thật trong lần sửa này.

Kiểm tra CLI sau sửa: demo spot_perp cũng dựng 720 dòng; công cụ intake xác nhận D3/D4 multi khớp counts 432/144/144 và chưa có khóa/tiêu thụ test. Chạy synthetic với `--data-dir data/processed` bị từ chối trước thao tác ghi, mã thoát 1 đúng dự kiến.

## Xác nhận phạm vi sàn và hoàn thiện bàn giao độc lập (19/10/2026)

Nhóm xác nhận nghiên cứu Binance/OKX/Bybit, mỗi sàn gồm spot và perp. Collector live giữ phạm vi 5 sàn/8 venue với Kraken spot và Coinbase spot; việc thu dư là có chủ đích. TV4 không sửa collector hoặc config thu live. Tài liệu bàn giao phân biệt kiểm D2 thu rộng không kèm config nghiên cứu với kiểm bản nghiên cứu đã chọn riêng 6 venue. Danh sách sàn không còn là đầu vào TV4 đang chờ; khoảng mẫu lịch sử và các file thật vẫn cần được bàn giao/xác nhận.

Kiểm chứng bổ sung chạy trên D2 synthetic 5 sàn/8 venue: intake nhận đủ tập thu rộng, align_live_quotes giữ đủ 8 venue; bản chọn riêng 6 venue qua kiểm config nghiên cứu, bản gốc giữ nguyên SHA-256. Không đổi provenance của fixtures thành live. Toàn repo đạt **262 passed, 3 skipped**; hai ca chờ TV5, một ca symlink bị giới hạn trên Windows. Demo multi và spot_perp đều dựng 720 dòng; D3/D4 multi qua intake với counts 432/144/144, final test chưa dùng và chưa có khóa. Đây là kiểm chứng phần nền TV4; không thay thế dữ liệu thật hoặc nghiệm thu C4.13.

## C4.11/C4.13 — review PR #7 TV5 (19/10/2026)

Đã nhận C5.01–C5.09 từ head fef42fd và kiểm tra trong bản kết hợp với dev 59df287. PR được chuyển base từ main sang dev, không thay main. Công thức CS/IS/IL/ILS đã đối chiếu accepted manuscript Putniņš (2013), trang 14–15, phương trình (4), (7), (8), (9). Finding, giới hạn và phạm vi nghiệm thu nằm trong docs/TV5_PR7_REVIEW.md.

C4.13 dùng hàm thật của TV5 và chạy cả hai fixtures gốc lẫn hai biến thể benchmark có nhiễu follower khai báo rõ. IS và ILS tìm đúng leader trên cả hai bộ gốc; CS được kiểm hữu hạn/tổng bằng 1 và không clip, đồng thời kiểm thu lại leader trên benchmark bổ sung phù hợp. Không áp giả định CS luôn cùng thứ hạng với tốc độ phản ứng khi mức nhiễu khác nhau. Không sửa các file fixtures/config gốc để làm xanh CI.

Bản sửa local đạt **330 passed, 1 skipped** (symlink Windows), strict TV5 **4 passed**. Demo nền hai bộ đều 720 dòng; CLI TV5 xuất T2/T3/T3c/T3d/T3b/D6 với multi 6 cửa sổ, spot_perp 8 cửa sổ, 0 failed. Final test chưa dùng. Các sửa bao gồm không nén thời gian qua gap, available_at chờ spec đã chọn trên dev, dùng chung bảo vệ thư mục synthetic, đủ bảng stationarity level/diff1 và hai adapter đúng chữ ký PDF. Dữ liệu thật/M2, các stage nghiên cứu khác và kết quả thực nghiệm vẫn chưa được nghiệm thu bằng các demo này.

## C4.11 — tích hợp TV5 và review TV3 PR #8 (20/10/2026)

PR #7 TV5 đã merge vào dev ở d318a36 sau kiểm lại 330 passed, 1 skipped và cả hai lượt CI xanh; main không đổi. PR #8 TV3 được chuyển base về dev và review theo C3.03/C3.04. Bản tích hợp giữ hợp đồng D2 và các bảo vệ collector đã nghiệm thu, thêm depth nhiều băng và bộ tải lịch sử với kiểm cache/coverage/checksum, nến đóng và status chưa đủ dữ liệu. Adapter T0 đọc được raw CCXT. Findings và bằng chứng: docs/TV3_PR8_REVIEW.md.

Local toàn repo sau sửa đạt **355 passed, 1 skipped** với --require-tv5, không còn skip hàm TV5. Sáu raw được nhận local và kiểm T0 sơ bộ, Binance spot khớp hash người gửi; không đưa raw lên Git. D1/D3/D4 thật chưa được tạo bởi việc review này, final test chưa dùng. Không đánh dấu M2 hoàn thành từ raw/T0 hoặc demo synthetic.
