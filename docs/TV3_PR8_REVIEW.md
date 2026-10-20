# Review PR #8 — TV3 C3.03/C3.04, 20/10/2026

PR #8: https://github.com/MinhAnhFintech/GPM1_Crypto/pull/8. Head gốc cebf75b1e3eb0ca8cd26fcf57bb1c8b1c6b13246. Đối chiếu GPM1_CRYPTO.xlsx «Việc Code» hàng 25–26 và hợp đồng D1/D2 trong PDF. Base đã đổi từ main sang dev; bản kiểm tra kết hợp dev d318a36 (đã có PR #7 TV5). Không thay main hoặc danh sách 5 sàn/8 venue thu live.

## Kết luận theo PCCV

- C3.03: giữ depth 10bps, ts_recv/ts_event, retry/backoff/log; nhận thêm depth 5bps/2bps và độ phủ bps, giữ số mức sổ lệnh. Perpetual vẫn bắt buộc. OKX spot dùng books-full theo thay đổi của TV3; params chỉ đi với yêu cầu sâu.
- C3.04: có OHLCV 1 phút theo venue, phân trang từ nến cuối thực nhận, retry, chia tháng, kiểm continuity, nến đóng và cửa sổ availability-time đúng start-inclusive/end-exclusive. Tên file raw theo {venue}_BTCUSDT_1m.parquet. Binance spot vẫn do TV2 bàn giao, không tải lại mặc định.

## Lỗi tái hiện và sửa tích hợp

1. Bản PR làm mất fetch_top_of_book/BookSampler: hai file test không import được. Bản sửa giữ collector đã tích hợp trên dev và ghép các trường depth mới. Giữ mid/spread, BTC conversion theo metadata, UTC ns, depth_censored, session boundaries, coverage kể cả phiên không có dữ liệu, resume part, bảo toàn buffer khi flush lỗi, stop/backoff. Không đổi canonical depth_censored sang depth_truncated.
2. Cache cũ được bỏ qua chỉ vì file tồn tại, kể cả tháng tải thiếu hoặc bị sửa. Nay kiểm schema/venue/provenance/UTC, checksum biên nhận và coverage trước khi reuse; part thiếu phải fetch lại, part không được xác thực không lọt vào final khi refetch rỗng. Chỉ ghép các part đúng cửa sổ hiện tại.
3. File tải thiếu không được xuất như bàn giao hoàn tất: giữ tại _incomplete/, fetch_log status=incomplete và CLI trả 1. Không ghi đè file final đã có bằng một lần tải thiếu. Không xuất nến chưa đóng từ cả dữ liệu mới lẫn cache. Không nội suy hoặc chèn nến.
4. Thêm kiểm limit/cửa sổ phút; timestamp trùng cùng payload được đếm, payload xung đột bị từ chối. Raw sai giá/volume/venue/provenance không vào cache.
5. Bộ T0 của TV2 chưa đọc raw ts_open_utc/volume_base mà TV3 xuất. Adapter đọc thêm đúng hai trường, giữ các định dạng Binance cũ và giờ mở nến; không dịch thành availability trong báo cáo T0.

Đơn vị OHLCV khác order book: trong CCXT 4.5.88, OKX parse_ohlcv chọn base volume index 6 cho perp (spot index 5); unified OHLCV cho các linear BTC/USDT đã chốt là BTC. Không nhân contractSize lần hai. Order-book size vẫn đổi theo metadata như collector đã nghiệm thu. volume_quote không có nguồn quan sát được tiếp tục NaN, không thay bằng close×volume. Có test gọi parser thật của thư viện ghim, không gọi API sàn.

## Dữ liệu bàn giao nhận ngày 20/10

Đã nhận sáu file raw vào data/raw/ohlcv, không đưa vào Git. Binance spot 525.600 dòng, hash đối chiếu người gửi:

`B0AFF42F30110C2622B202A90D68583855A0E1723FA07ACC4FFE00A21AE57D88`

Năm file còn lại mỗi file 501.119 dòng. Đã ghi hash tải về riêng; chưa có hash người gửi để đối chiếu cho năm file này. T0 sơ bộ cả sáu file không có thiếu/trùng phút, timestamp lệch lưới, giá thiếu/không dương/high-low sai hoặc volume âm trong khoảng quan sát. Giữ các bước nhảy giá và ghi cờ theo ngưỡng T0 (300bps), không tự loại quan sát. Chưa coi đây là nghiệm thu toàn bộ D1/D3/M2 hoặc mô hình trên dữ liệu thật.

## Kiểm thử

Test offline bao gồm live hiện có, depth nhiều băng, phân trang thiếu hơn limit, retry/hết retry, timestamp/payload trùng, limit/biên sai, resume part thiếu, tamper checksum, cửa sổ cache khác, nến chưa đóng, raw sai và adapter T0 không sửa input. Bộ test dùng fixtures/thư mục tạm; không khởi chạy collector dài hạn, không claim final test. Lượt kiểm thử toàn repo và CI được xác nhận trước khi merge PR.

Kết quả local cuối: `python -m pytest -q --require-tv5`: **355 passed, 1 skipped** (symlink Windows). Nhóm historical/live hiện có đạt 46 passed; nhóm historical + T0 sau bổ sung adapter đạt 36 passed. Demo nền multi thành công, 720 dòng, final test chưa dùng. Kiểm thử live không thay xác minh API/độ phủ của các phiên thu thật; CI của đúng head bản sửa phải xanh trước khi merge.
