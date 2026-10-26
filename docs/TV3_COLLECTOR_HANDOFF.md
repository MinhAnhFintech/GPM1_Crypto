# Bàn giao collector live TV3 sau tích hợp

Cấu hình phiên nằm ở `config/live_sessions.yaml`; collector vẫn cho phép chỉ định `--config`. Các venue thu thập live gồm cả venue ứng viên, không thay danh sách mẫu nghiên cứu trong config/config.yaml. Phần lịch/coverage chạy offline; chỉ lệnh run mới gọi sàn:

```powershell
python -m src.collectors.live.collect_live schedule
python -m src.collectors.live.collect_live coverage --csv-report outputs/tables/T0b_live_coverage.csv
```

Mỗi venue có thể đặt book_limit và params; params chỉ đi với yêu cầu sổ sâu. Nếu yêu cầu sâu gặp lỗi không tạm thời, collector thử sổ nông 5 mức. Quote còn bid/ask, size, mid và spread; depth để missing và depth_censored=True. Ba lỗi sâu liên tiếp (trong khi sổ nông thành công) tắt sổ sâu đến hết phiên; một lần sâu thành công đặt lại bộ đếm. Timeout, lỗi mạng và rate limit bỏ tick rồi backoff, không phát thêm yêu cầu sổ nông.

`fetch_top_of_book(ex, symbol, limit)` vẫn hoạt động cho code/test cũ. Giao diện mới `fetch_book` hỗ trợ params và chế độ shallow. Size được chuyển từ hợp đồng tuyến tính sang BTC bằng contractSize trong metadata CCXT. size_multiplier nếu có chỉ là kiểm tra nhất quán với metadata; sai thì báo lỗi. Hợp đồng inverse bị từ chối. Symbol đầu ra là BTC/USDT, kể cả khi kết nối dùng BTC/USDT:USDT.

D2 v1.1 giữ 14 cột gốc trước, rồi phần mở rộng theo docs/DATA_CONTRACT.md. market_type vẫn bắt buộc; venue khớp exchange_market_type. depth_truncated là tên bàn giao TV3, depth_censored giữ làm alias tương thích sau phần mở rộng; hai cột phải khớp nhau. Timestamp UTC ns, book_levels_bid/ask là số mức quan sát. Các part giữ session_start_utc/end_utc; coverage vẫn đếm trên lịch đã trôi qua, không dùng dòng trùng để tăng độ phủ hoặc bỏ qua phiên mất toàn bộ dữ liệu. Bản v1.0 đã thu không tự bị ghi lại.

Mỗi phiên có errors.csv gồm ts_utc, venue, error_type, message, action. n_errors trong coverage đếm dòng chẩn đoán (bao gồm thông báo tắt sổ sâu), không đồng nghĩa số quote mất. Các thống kê depth báo tỷ lệ censored và median phần quan sát được; không diễn giải depth censored thành tổng độ sâu đầy đủ.

ZIP mẫu live và checksum đã được giữ local và loại khỏi cây file được theo dõi trong bản sửa; không thay nội dung dữ liệu. File đã từng nằm trong các commit cũ vẫn có trong lịch sử Git: lần tích hợp này không rewrite/force-push lịch sử. Bàn giao dữ liệu thật qua ổ chung kèm SHA-256 theo CONTRIBUTING.md.

Kiểm thử dùng order book giả lập, không khởi chạy collector hoặc xác minh khả dụng API thật. Dữ liệu live cũ cần được TV3 kiểm schema trước khi bàn giao D2; sửa code không tự chuyển đổi các part cũ hoặc nghiệm thu coverage/depth trên mẫu thật.

## C3.03/C3.04 bổ sung trong PR #8 (20/10/2026)

Live giữ 5 sàn/8 venue. Thêm các cột depth_bid/ask_5bps_usdt, depth_bid/ask_2bps_usdt và depth_cover_bps; các cột depth nhỏ cũng chỉ là phần quan sát nếu sổ chưa phủ đủ băng tương ứng. Khi fallback shallow, mọi cột depth đều missing và depth_censored=True. Độ phủ bps tính quanh mid của cùng snapshot; không thay định nghĩa D2 10bps.

OHLCV lịch sử chạy `python -m src.collectors.historical.ccxt_ohlcv`; mặc định dùng config/config.yaml và config/exchanges.yaml, bỏ Binance spot vì TV2 tải qua Vision. `--smoke-hours 2` ghi riêng trong _smoke/, không lẫn file nghiên cứu. Nếu đã nhận file raw từ ổ chung, không cần chạy lại bộ tải để kiểm chất lượng; dùng `python -m src.features.ohlcv_quality`.

Raw CCXT dùng ts_open_utc và volume_base; ts_available vẫn do chuẩn hóa tạo bằng giờ mở + 1 phút. Unified OHLCV của CCXT đã cho base volume BTC ở các venue đã chốt; không nhân contractSize như order-book. volume_quote không được quan sát thì giữ NaN. Bộ T0 đọc được cả raw Binance và CCXT.

Resume chỉ dùng part có checksum/biên/venue/schema hợp lệ và đầy đủ. Part thiếu được thử lại. File chưa đủ giữ trong data/raw/ohlcv/_incomplete/ cùng status=incomplete trong fetch_log; CLI trả 1, không thay file final tại thư mục gốc. _parts/ và _incomplete/ không là file bàn giao D1, không được dùng để bỏ qua các cờ chất lượng. Chi tiết findings và kiểm chứng: docs/TV3_PR8_REVIEW.md.

## C3.06 (D2) và C3.09 (D9): bàn giao dữ liệu 22/10/2026

Ly giao qua ổ chung, code ở nhánh `feat/tv3-fixc3.06-c3.09` (chưa merge). TV4 kiểm ngày 22/10 trong runtime đúng requirements.txt; hai file khớp hash Ly gửi.

| File | SHA-256 | Nội dung |
| --- | --- | --- |
| live_quotes_long.parquet | 9A2F9BBDEE31034B3D1DC42B7D2FC66F192D80FCD58FE2064DC94628A7F32EEA | 116.461 dòng × 27 cột; 9 phiên 17/10 S2 → 21/10 S2; 6 venue nghiên cứu (Kraken/Coinbase đã lọc) |
| funding_oi.parquet | A06B8C07E281F965566BDCBB54B34D2163E35C7DA22DC6B863C1B88FF675D2C2 | 4.322 dòng × 14 cột; funding Binance/Bybit 1.106 kỳ, OKX 280 kỳ; OI 696 nến 1h mỗi sàn |

Bản này thiếu phiên 22/10 S1/S2 và 23/10 S1/S2; Ly chạy lại sau 23/10, tên cột và cấu trúc giữ nguyên. Khi nhận bản mới, kiểm lại hash và các mục dưới đây.

**Đã đạt.** D2: 14 cột gốc và phần mở rộng v1.1 đúng thứ tự; khóa (session_id, exchange, market_type, ts_grid_utc) duy nhất; bid > 0, ask ≥ bid; mid/spread đúng công thức (sai số 0); data_kind=live; venue = exchange_market_type; depth không âm, 2 ≤ 5 ≤ 10 bps. Thiếu 0,15% so với 2.160 dòng mỗi phiên × venue, không nội suy. D9: market_type=perp, không trùng (exchange, ts_available), funding_rate_bps = funding_rate × 10⁴, OI ts_available = mốc sàn + 1h, OKX OI ×0,01 khớp giá (giá ngầm định 83.492 so với Binance 83.480).

**Chưa đạt validator, cần TV3 sửa trong code rồi xuất lại:**

1. D2 `depth_truncated` phải là bool không null. Toàn bộ 12.954 dòng phiên 17/10 S2 (file thô đời đầu, không đo depth) đang NaN; theo quy ước collector phải là True. Sửa ở `make_live_quotes_long.py` khi bổ sung cột cho file thô cũ. Đồng thời ghi attrs `contract_version="1.1"` (có thể qua `upgrade_d2_frame`). Đã thử trong bộ nhớ: sau hai sửa này D2 qua validator.
2. D9 trộn `data_kind` (3.938 historical, 384 live) nên validator báo `mixed or missing provenance`. Funding/OI đều lấy từ API lịch sử; đề xuất đặt `data_kind="historical"` cho cả file và thêm cột bool `in_live_window` thay cho nhãn theo dòng. Đã thử trong bộ nhớ: qua validator. Chờ nhóm chốt trước khi đưa vào DATA_CONTRACT.

Chỉ đặt file vào `data/processed/live_quotes_long.parquet` và `data/processed/funding_oi.parquet` sau khi qua validator.

**Bản cuối, TV4 kiểm lại 25/10.** `live_quotes_long.parquet` SHA-256 `50104EDC92BF1D46CE7EB91BBA45344F7D287978C5BF70C59B7DA3BAC59E6F64`: 168.178 dòng × 27 cột, 13 phiên 17/10 S2 → 23/10 S2 (đủ bốn phiên 22–23/10 còn thiếu), 6 venue nghiên cứu, attrs `contract_version="1.1"`, `depth_truncated` bool không null, khóa (session_id, exchange, market_type, ts_grid_utc) không trùng, ts_recv_utc/ts_grid_utc UTC ns. Qua `validate_frame(..., "D2")`; hash trùng `inputs.live_quotes_sha256` trong `config/frozen_t5b.yaml`. `funding_oi.parquet`: 4.412 dòng, một nhãn `data_kind=historical`, qua validator D9. Hai mục «chưa đạt» ở trên đã được TV3 xử lý trong bản này.

**Cách dùng D2 (C3.06), đã đối chiếu với dữ liệu:**

- Một dòng cho mỗi venue × mốc lưới 5 giây. Thông tin chỉ biết tại `ts_recv_utc`; không dùng `ts_grid_utc` làm mốc biết. Phiên S1 07–10h UTC, S2 13–16h UTC; không phủ 24 giờ.
- Spread bị giới hạn bởi tick: trung vị 0,012 bps, Binance spot 0,0012 bps.
- Độ sâu: phiên 17/10 S2 không có số đo độ sâu, không dùng cho T4/T6. Phiên 18/10 S1/S2 chỉ có cột 10 bps (dùng `depth_truncated == False`). Từ 19/10 S1 có thêm cột 5/2 bps và `depth_cover_bps` (trung vị ~15 bps); dùng cột 5 bps với `depth_cover_bps >= 5`. Bybit perp bị cắt ở 10 bps khoảng 89% số dòng.
- Lệch đồng hồ (trung vị ts_recv − ts_event): phiên 18/10 S1 khoảng −3.000 ms, cần đánh dấu hoặc loại khi tính lead-lag; các phiên khác trong khoảng ±630 ms.
- Binance spot không có ts_event (100% trống), dùng ts_recv_utc.
- Size OKX perp phiên 17/10 S2 đã quy đổi ×0,01 (2.159 dòng, ghi trong `size_fixes` của manifest); dữ liệu thô không sửa.

**Cách dùng D9 (C3.09), đã đối chiếu với dữ liệu:**

- Một dòng mỗi quan sát: dòng funding tại giờ chốt kỳ, dòng OI tại giờ nến khép, trùng giờ thì gộp (258 dòng có cả hai). Đếm số kỳ funding bằng các dòng có `funding_rate`. Mỗi sàn có một dòng funding đầu tiên với `funding_interval_h` trống.
- Funding và OI chỉ là biến mô tả cho RQ3 và bảng T1; không đưa funding vào `net_gap_bps`.
- Funding OKX chỉ có từ 20/07/2026; Binance và Bybit từ 18/10/2025. So sánh ba sàn dùng khoảng chung từ 20/07/2026.
- OI chỉ có 29 ngày (ts_available 23/09–22/10). Chỉ dùng OI làm biến mô tả cho giai đoạn live, không đưa vào hồi quy trên mẫu lịch sử dài.
- Bybit không có OI theo USDT (100% trống); so sánh OI giữa các sàn bằng `open_interest_base` (BTC).
