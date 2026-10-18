# Hợp đồng dữ liệu v1.0

Chủ sở hữu: Anh (TV4). Chốt triển khai ngày 17/10/2026 theo GPM1_CRYPTO.pdf, mục 2 (trang 6–10), và sheet «Việc Code» trong GPM1_CRYPTO.xlsx. Thay đổi schema phải được TV4 review, tăng phiên bản và tái sinh fixtures. Kiểm tra tự động nằm trong `src/utils/schema.py`.

## Quy ước chung

| Nội dung | Quy định |
| --- | --- |
| Thời gian | `datetime64[ns, UTC]`; cấm datetime không timezone. Đọc thời gian có timezone khác bằng `utc_series`, sau đó kiểm schema. |
| Venue | `{exchange}_{market_type}`, ví dụ `binance_spot`, `binance_perp`. Trong bảng dài giữ riêng `exchange`, `market_type`. |
| Exchange | ID chuẩn của nhóm, chữ thường: binance, okx, bybit, kraken, coinbase. `ccxt_id` có thể khác, ví dụ coinbaseexchange, binanceusdm, và chỉ thuộc cấu hình kết nối. |
| Market type | `spot` hoặc `perp`. |
| Symbol dữ liệu | `BTC/USDT`, kể cả perpetual. Symbol kết nối `BTC/USDT:USDT` nằm ở config. Tên file dùng `BTCUSDT`. |
| Đơn vị | Giá USDT; size/volume_base BTC; volume_quote/depth USDT; chi phí bps một chiều. Số tiền và giá dùng float64. |
| Data kind | Một trong synthetic, historical, live. Mỗi file chỉ có một nhãn; không trộn nguồn. |
| File | Parquet cho dữ liệu, CSV cho bảng, PNG cho hình. Bảng rộng lưu provenance trong Parquet/pandas attrs. |
| Thiếu | NaN và `missing__{venue}`; không nội suy, không backfill từ tương lai. |
| Random | Seed đọc từ cấu hình, ghi vào truth.json của fixtures. |

`ts_available` của nến 1 phút = thời gian **mở nến + đúng 1 phút**; không lấy trường Binance `close_time` (thường sớm hơn 1 ms). Với live, dữ liệu chỉ được biết từ `ts_recv_utc`, không phải `ts_event_utc` hoặc lưới dự kiến `ts_grid_utc`. `available_at` của kết quả rolling không được trước `window_end`.

## D1–D9 và đường dẫn bàn giao

| Mã / chủ sở hữu | Đường dẫn | Các cột bắt buộc |
| --- | --- | --- |
| D1 / TV2 | data/interim/ohlcv_1m_long.parquet | ts_available, exchange, market_type, symbol, open, high, low, close, volume_base, volume_quote, data_kind |
| D2 / TV3 | data/processed/live_quotes_long.parquet | ts_recv_utc, ts_event_utc, exchange, market_type, symbol, bid, ask, bid_size, ask_size, mid, spread_abs, spread_bps, depth_bid_10bps_usdt, depth_ask_10bps_usdt, data_kind |
| D3 / TV4 | data/processed/panel_1m_wide.parquet | Index ts_available; close__{venue}, log_close__{venue}, volume_base__{venue}, missing__{venue} |
| D3b / TV2 | data/processed/features_1m.parquet | Index ts_available; ret_1m__{venue}, vol_share__{venue}, rv_60m__{venue}, dispersion_bps_spot, dispersion_bps_perp |
| D4 / TV4 | data/processed/split_manifest.json | dev_start, dev_end, val_start, val_end, test_start, test_end, fingerprint |
| D5 / TV1 | data/processed/regime_flags.parquet | ts_available, flag_high_vol, flag_weekend, flag_event, regime_bull_bear |
| D6 / TV5 | outputs/tables/rolling_is.parquet | window_start, window_end, available_at, venue, is_lower, is_upper, is_mid, component_share, ils, leader, status, reason |
| D7 / TV6 | outputs/tables/gap_events.parquet | event_id, ts_start, ts_end, duration_s, market_type, buy_exchange, sell_exchange, gross_bps, net_bps, regime |
| D8 / TV6 | outputs/tables/trades_ledger.parquet | signal_id, ts_decision, ts_fill, market_type, buy_exchange, sell_exchange, qty_btc, buy_px, sell_px, fees_usdt, pnl_usdt, status, reject_reason |
| D9 / TV3 | data/processed/funding_oi.parquet | ts_available, exchange, market_type, symbol, funding_rate, funding_interval_h, open_interest_base, open_interest_usdt, data_kind |

Giải quyết hai chỗ không thống nhất trong bản nguồn: phần quy ước chung ghi `missing__{exchange}` nhưng D3 và C4.08 yêu cầu suffix theo venue; dùng **missing__{venue}** để không nhập nhằng spot/perp. Quy ước chung bắt buộc market_type nhưng bảng D9 bỏ sót; D9 triển khai thêm **market_type=perp**. Bảng rộng giữ loại hợp đồng trong suffix và attrs. Đây là các lựa chọn TV4 chốt trong v1.0, không âm thầm đổi tên giữa các thành viên.

Các cột bổ sung được phép nếu không thay nghĩa cột bắt buộc. D2 nên có `depth_censored` (bool). Độ sâu bị cắt là cận dưới quan sát được; không diễn giải thành độ sâu đầy đủ. Dữ liệu raw live có thêm session_id, session_start_utc, session_end_utc, latency_ms và ts_grid_utc để kiểm coverage; không dùng ts_grid_utc làm thời điểm có thông tin.

## Kiểu và bất biến

- Các cột thời gian dùng UTC ns; ts_event_utc được phép NaT; ts_fill được phép NaT cho lệnh bị từ chối.
- Cột định danh/nhãn là chuỗi; giá, volume, depth, share và chi phí là số. `missing__`, `flag_high_vol`, `flag_weekend`, `flag_event` là boolean.
- D1 duy nhất theo (ts_available, exchange, market_type, symbol). Giá OHLC dương, high/low bao các giá còn lại, volume không âm. Không tự xóa dòng trùng.
- D2: bid > 0, ask >= bid; mid=(bid+ask)/2; spread_abs=ask-bid; spread_bps=spread_abs/mid*10000. Size perpetual tuyến tính từ CCXT phải nhân contractSize để ra BTC; không hỗ trợ hợp đồng inverse trong schema BTC/USDT này.
- D3: index duy nhất, tăng dần; `log_close=ln(close)`; missing phải khớp NaN của close, log_close và volume khi hết tuổi dữ liệu.
- D6: IS và ILS hữu hạn trong [0,1] với status=ok; is_lower <= is_mid <= is_upper. Component Share phải hữu hạn nhưng giữ nguyên dấu và giá trị, không ép vào [0,1] (C5.04, sheet «Việc Code», hàng 48). Không gán leader nếu bounds chồng nhau; để trống và reason=overlapping_bounds. IS và ILS khác nhau phải báo cáo cả hai.
- Funding là tỷ lệ theo chu kỳ, không annualize; funding_interval_h là số giờ mỗi chu kỳ. OI base tính BTC, OI USDT phải có cách quy đổi rõ. Không lấy OI hiện tại làm lịch sử.

## Đồng bộ và chia mẫu

```python
align_previous_tick(df_long, grid_freq="1min", max_age="5min")
```

Đầu vào D1, đầu ra D3. Lưới UTC từ floor(min ts_available) đến ceil(max ts_available). Tại g, chọn quan sát của đúng venue có ts_available lớn nhất nhưng <= g; chỉ chấp nhận nếu g-ts_available <= max_age. Đúng biên max_age còn hợp lệ. Quá biên để NaN. Các venue có cùng tên sàn nhưng khác market_type được xử lý độc lập. Không kéo dài panel ngoài khoảng của toàn bộ đầu vào; nếu cần khoảng lịch cố định phải bàn giao rõ ở config.

D4 chia theo thứ tự thời gian, không shuffle: floor(60%N) development, floor(20%N) validation, phần còn lại final test. Giữ các dòng missing khi chia để không đổi ý nghĩa thời gian. Biên start/end bao gồm hai đầu, ba tập không giao nhau. Fingerprint SHA-256 gồm schema, UTC index, dữ liệu và attrs, với phiên bản pandas đã ghim. Có thêm counts, ratios, data_kind và test_consumed để kiểm tra.

`select_split` chỉ cho dev/val. `claim_final_test` yêu cầu file cấu hình đã đóng băng, ghi SHA-256 và khóa độc quyền bằng file `.test.lock` rồi đánh dấu test_consumed; lần gọi sau bị từ chối. Chạy lại pipeline không xóa khóa. Đây là kiểm soát trong API dự án, không ngăn việc mở Parquet trực tiếp; nhóm vẫn phải giữ quy tắc chỉ dùng test cuối một lần.

## Công thức và quan hệ giữa các loại hợp đồng

```text
gross_gap_bps = (bid_sell - ask_buy) / ask_buy * 10000
fee_total_bps = fee_buy_bps + fee_sell_bps
net_gap_bps = gross_gap_bps - fee_total_bps - slippage_bps - extra_cost_bps
```

Chỉ ghép cùng market_type cho arbitrage. Spot/perp được phép ghép cho nghiên cứu lead–lag và RQ3. Khi đã dùng bid/ask thật, không trừ spread lần hai. Proxy close lịch sử cần assumed_half_spread mỗi chân, có nguồn từ mẫu live. Độ trễ mô phỏng bằng thời gian thực thi, không cộng thành phí. Funding không nằm trong net_gap_bps.

### Giao diện mô hình chi phí (review C6.01/C6.02)

`load_fees(path)` trả `FeeSchedule`; `generate_pairs(venues)` tạo cả hai hướng mua/bán trong cùng market_type, loại cặp tự giao dịch. `compute_gaps(panel, fees, venues=None, mode="auto", fee_type="taker", slippage_bps=None, extra_cost_bps=None, assumed_half_spread_bps=None)` nhận bảng rộng với index hoặc cột `ts_available` duy nhất, tăng dần, kiểu UTC ns và đúng một `data_kind`. Nếu có cả cột và metadata provenance, hai nhãn phải khớp.

Chế độ `quotes` dùng `bid__{venue}`, `ask__{venue}`; mỗi venue phải đủ hai cột và bid không vượt ask. `auto` chọn quotes khi có bất kỳ cột bid/ask, báo lỗi nếu thiếu một phía, không tự chuyển sang close để che dữ liệu thiếu. Chế độ `close` nhận D3; giá thực thi proxy là close nhân (1 + h/10000) ở chân mua, close nhân (1 - h/10000) ở chân bán. h phải được hiệu chỉnh từ mẫu live; không có h thì báo lỗi. Các ví dụ synthetic chỉ dùng h giả định để kiểm thử.

Giá hiện hữu phải dương, hữu hạn; giá thiếu giữ NaN, `missing__{venue}=True` vô hiệu hóa giá tương ứng. Không fill hoặc dịch thời gian trong mô hình chi phí. Chi phí phải hữu hạn, không âm; half-spread phải nhỏ hơn 10000 bps. Thiếu taker fee báo lỗi; maker chưa xác minh để null và không được dùng để tính kết quả. Phí taker đã bàn giao được giữ nguyên.

Bảng gap trung gian có `ts_available`, `buy_exchange`, `sell_exchange`, `market_type`, `buy_venue`, `sell_venue`, `ask_buy`, `bid_sell`, `fee_total_bps`, `gross_gap_bps`, `net_gap_bps`; metadata giữ `data_kind`. Đây là đầu vào cho TV6, chưa phải D7. Khi xuất D7, TV6 đổi hai tên gap thành `gross_bps`, `net_bps` và bổ sung cột sự kiện theo hợp đồng. Những kiểm tra trên làm rõ bất biến v1.0, không thay schema D1-D9.

Các hàm TV5 giữ chữ ký trong PDF: fit_vecm, hasbrouck_is, component_share, information_leadership. Chữ `exchange` trong đầu ra hasbrouck_is phải chứa **venue ID**; test tích hợp chấp nhận tên khóa exchange hoặc venue nhưng không chấp nhận mất hậu tố spot/perp. ILS được kiểm trên từng cặp rồi tổng hợp theo phương pháp TV5 xác nhận.

## Nguồn kỹ thuật của triển khai

Previous-tick dùng [pandas.merge_asof](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html) với direction=backward và tolerance. Mẫu hồi quy dùng [statsmodels OLS.fit](https://www.statsmodels.org/stable/generated/statsmodels.regression.linear_model.OLS.fit.html), covariance HAC và maxlags cấu hình. Các nguồn này hỗ trợ cách dùng thư viện; yêu cầu nghiên cứu vẫn lấy từ tài liệu phân công của nhóm.
