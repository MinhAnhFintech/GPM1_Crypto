# Bàn giao collector live TV3 sau tích hợp

Cấu hình phiên nằm ở `config/live_sessions.yaml`; collector vẫn cho phép chỉ định `--config`. Các venue thu thập live gồm cả venue ứng viên, không thay danh sách mẫu nghiên cứu trong config/config.yaml. Phần lịch/coverage chạy offline; chỉ lệnh run mới gọi sàn:

```powershell
python -m src.collectors.live.collect_live schedule
python -m src.collectors.live.collect_live coverage --csv-report outputs/tables/T0b_live_coverage.csv
```

Mỗi venue có thể đặt book_limit và params; params chỉ đi với yêu cầu sổ sâu. Nếu yêu cầu sâu gặp lỗi không tạm thời, collector thử sổ nông 5 mức. Quote còn bid/ask, size, mid và spread; depth để missing và depth_censored=True. Ba lỗi sâu liên tiếp (trong khi sổ nông thành công) tắt sổ sâu đến hết phiên; một lần sâu thành công đặt lại bộ đếm. Timeout, lỗi mạng và rate limit bỏ tick rồi backoff, không phát thêm yêu cầu sổ nông.

`fetch_top_of_book(ex, symbol, limit)` vẫn hoạt động cho code/test cũ. Giao diện mới `fetch_book` hỗ trợ params và chế độ shallow. Size được chuyển từ hợp đồng tuyến tính sang BTC bằng contractSize trong metadata CCXT. size_multiplier nếu có chỉ là kiểm tra nhất quán với metadata; sai thì báo lỗi. Hợp đồng inverse bị từ chối. Symbol đầu ra là BTC/USDT, kể cả khi kết nối dùng BTC/USDT:USDT.

D2 giữ các cột mid, spread_abs, spread_bps, depth_censored và timestamp UTC ns. Các trường bổ sung book_levels_bid/ask là số mức quan sát. Không thay tên depth_censored thành depth_truncated. Các part giữ session_start_utc/end_utc; coverage vẫn đếm trên lịch đã trôi qua, không dùng dòng trùng để tăng độ phủ hoặc bỏ qua phiên mất toàn bộ dữ liệu.

Mỗi phiên có errors.csv gồm ts_utc, venue, error_type, message, action. n_errors trong coverage đếm dòng chẩn đoán (bao gồm thông báo tắt sổ sâu), không đồng nghĩa số quote mất. Các thống kê depth báo tỷ lệ censored và median phần quan sát được; không diễn giải depth censored thành tổng độ sâu đầy đủ.

ZIP mẫu live và checksum đã được giữ local và loại khỏi cây file được theo dõi trong bản sửa; không thay nội dung dữ liệu. File đã từng nằm trong các commit cũ vẫn có trong lịch sử Git: lần tích hợp này không rewrite/force-push lịch sử. Bàn giao dữ liệu thật qua ổ chung kèm SHA-256 theo CONTRIBUTING.md.

Kiểm thử dùng order book giả lập, không khởi chạy collector hoặc xác minh khả dụng API thật. Dữ liệu live cũ cần được TV3 kiểm schema trước khi bàn giao D2; sửa code không tự chuyển đổi các part cũ hoặc nghiệm thu coverage/depth trên mẫu thật.

## C3.03/C3.04 bổ sung trong PR #8 (20/10/2026)

Live giữ 5 sàn/8 venue. Thêm các cột depth_bid/ask_5bps_usdt, depth_bid/ask_2bps_usdt và depth_cover_bps; các cột depth nhỏ cũng chỉ là phần quan sát nếu sổ chưa phủ đủ băng tương ứng. Khi fallback shallow, mọi cột depth đều missing và depth_censored=True. Độ phủ bps tính quanh mid của cùng snapshot; không thay định nghĩa D2 10bps.

OHLCV lịch sử chạy `python -m src.collectors.historical.ccxt_ohlcv`; mặc định dùng config/config.yaml và config/exchanges.yaml, bỏ Binance spot vì TV2 tải qua Vision. `--smoke-hours 2` ghi riêng trong _smoke/, không lẫn file nghiên cứu. Nếu đã nhận file raw từ ổ chung, không cần chạy lại bộ tải để kiểm chất lượng; dùng `python -m src.features.ohlcv_quality`.

Raw CCXT dùng ts_open_utc và volume_base; ts_available vẫn do chuẩn hóa tạo bằng giờ mở + 1 phút. Unified OHLCV của CCXT đã cho base volume BTC ở các venue đã chốt; không nhân contractSize như order-book. volume_quote không được quan sát thì giữ NaN. Bộ T0 đọc được cả raw Binance và CCXT.

Resume chỉ dùng part có checksum/biên/venue/schema hợp lệ và đầy đủ. Part thiếu được thử lại. File chưa đủ giữ trong data/raw/ohlcv/_incomplete/ cùng status=incomplete trong fetch_log; CLI trả 1, không thay file final tại thư mục gốc. _parts/ và _incomplete/ không là file bàn giao D1, không được dùng để bỏ qua các cờ chất lượng. Chi tiết findings và kiểm chứng: docs/TV3_PR8_REVIEW.md.
