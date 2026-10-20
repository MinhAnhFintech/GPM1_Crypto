# DATA DICTIONARY

Tài liệu mô tả tên cột, kiểu dữ liệu, đơn vị và ý nghĩa của các dataset
D1–D9 theo `DATA_CONTRACT.md`.

**Nguồn:** Data Contract v1.0 (D2 mở rộng v1.1).

**Trạng thái đối chiếu:** Tại ngày 20/10/2026, D1 đã có dữ liệu thực tế
và được đối chiếu. D2–D9 chưa có output thực tế nên hiện được mô tả theo
Data Contract và sẽ được cập nhật sau khi dữ liệu được bàn giao.

## Quy ước chung

| Nội dung | Quy ước |
| --- | --- |
| Timestamp | UTC, `datetime64[ns, UTC]` |
| `exchange` | ID sàn viết thường, ví dụ `binance`, `okx`, `bybit` |
| `market_type` | `spot` hoặc `perp` |
| `venue` | `{exchange}_{market_type}`, ví dụ `binance_spot`, `okx_perp` |
| Symbol | `BTC/USDT`; symbol kết nối perpetual có thể là `BTC/USDT:USDT` nhưng chỉ nằm trong config |
| Giá | USDT |
| `volume_base`, size | BTC |
| `volume_quote`, depth | USDT |
| `_bps` | basis points (bps) |
| `data_kind` | `synthetic`, `historical` hoặc `live`; không trộn trong cùng file |
| Dữ liệu thiếu | `NaN`; bảng rộng dùng `missing__{venue}`; không nội suy bằng dữ liệu tương lai |

## D1 — OHLCV 1 phút

**File:** `data/interim/ohlcv_1m_long.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `ts_available` | datetime64[ns, UTC] | UTC | Thời điểm nến có thể được sử dụng; với nến 1 phút bằng giờ mở nến + 1 phút |
| `exchange` | string | — | Sàn giao dịch |
| `market_type` | string | — | Loại thị trường: `spot` hoặc `perp` |
| `symbol` | string | — | Cặp giao dịch chuẩn `BTC/USDT` |
| `open` | float64 | USDT | Giá mở cửa |
| `high` | float64 | USDT | Giá cao nhất |
| `low` | float64 | USDT | Giá thấp nhất |
| `close` | float64 | USDT | Giá đóng cửa |
| `volume_base` | float64 | BTC | Khối lượng theo tài sản cơ sở |
| `volume_quote` | float64 | USDT | Khối lượng theo tài sản định giá |
| `data_kind` | string | — | Loại dữ liệu |

**Đối chiếu dữ liệu thật:** 
Đã đối chiếu với file `ohlcv_1m_long.parquet` hiện có.

- Kích thước thực tế: 3.006.714 dòng × 11 cột.
- File có đủ 11 cột bắt buộc theo Data Contract.
- `ts_available`: `datetime64[ns, UTC]`.
- `open`, `high`, `low`, `close`, `volume_base`, `volume_quote`: `float64`.
- `exchange`, `market_type`, `symbol`, `data_kind`: `object` trong pandas.
- Dữ liệu quan sát trong file có `data_kind = historical`.
- Có đủ 6 venue thuộc Binance, Bybit và OKX, gồm thị trường `spot` và `perp`.
- Không phát hiện khóa trùng theo `(ts_available, exchange, market_type, symbol)`.

## D2 — Live Quotes

**File:** `data/processed/live_quotes_long.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `ts_recv_utc` | datetime64[ns, UTC] | UTC | Thời điểm hệ thống nhận dữ liệu |
| `ts_event_utc` | datetime64[ns, UTC], nullable | UTC | Thời điểm sự kiện do sàn cung cấp |
| `exchange` | string | — | Sàn giao dịch |
| `symbol` | string | — | Cặp giao dịch |
| `bid` | float64 | USDT | Best bid |
| `ask` | float64 | USDT | Best ask |
| `bid_size` | float64 | BTC | Khối lượng tại best bid |
| `ask_size` | float64 | BTC | Khối lượng tại best ask |
| `mid` | float64 | USDT | Giá giữa bid và ask |
| `spread_abs` | float64 | USDT | `ask - bid` |
| `spread_bps` | float64 | bps | Bid–ask spread theo bps |
| `depth_bid_10bps_usdt` | float64 | USDT | Depth phía bid trong phạm vi 10 bps |
| `depth_ask_10bps_usdt` | float64 | USDT | Depth phía ask trong phạm vi 10 bps |
| `data_kind` | string | — | Loại dữ liệu |

### D2 v1.1 — trường mở rộng

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `market_type` | string | — | `spot` hoặc `perp`; trường bắt buộc |
| `venue` | string | — | `{exchange}_{market_type}` |
| `session_id` | string | — | ID phiên thu thập dữ liệu |
| `ts_grid_utc` | datetime64[ns, UTC] | UTC | Nhịp dự kiến gửi request |
| `depth_truncated` | bool | — | Cho biết order book chưa phủ đầy đủ vùng ±10 bps |
| `depth_cover_bps` | float64 | bps | Mức depth tối thiểu được phủ ở cả hai phía |
| `depth_bid_5bps_usdt` | float64 | USDT | Depth bid trong phạm vi 5 bps |
| `depth_ask_5bps_usdt` | float64 | USDT | Depth ask trong phạm vi 5 bps |
| `depth_bid_2bps_usdt` | float64 | USDT | Depth bid trong phạm vi 2 bps |
| `depth_ask_2bps_usdt` | float64 | USDT | Depth ask trong phạm vi 2 bps |
| `book_levels_bid` | integer | số mức | Số mức bid nhận được |
| `book_levels_ask` | integer | số mức | Số mức ask nhận được |
| `latency_ms` | float64 | ms | Thời gian thực hiện request |

**Đối chiếu:** Chưa có output thực tế.

## D3 — Panel 1 phút

**File:** `data/processed/panel_1m_wide.parquet`

| Cột / Index | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `ts_available` | datetime64[ns, UTC] | UTC | Index thời gian |
| `close__{venue}` | float64 | USDT | Giá close của từng venue |
| `log_close__{venue}` | float64 | log-price | Log tự nhiên của giá close |
| `volume_base__{venue}` | float64 | BTC | Khối lượng theo venue |
| `missing__{venue}` | bool | — | Cờ dữ liệu thiếu của venue |

**Đối chiếu:** Chưa có output thực tế.

## D3b — Features 1 phút

**File:** `data/processed/features_1m.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `ret_1m__{venue}` | float64 | tỷ lệ | Lợi suất 1 phút của venue |
| `vol_share__{venue}` | float64 | tỷ lệ | Tỷ trọng volume của venue |
| `rv_60m__{venue}` | float64 | — | Realized volatility cửa sổ 60 phút |
| `dispersion_bps_spot` | float64 | bps | Độ phân tán giá giữa các venue spot |
| `dispersion_bps_perp` | float64 | bps | Độ phân tán giá giữa các venue perp |

**Đối chiếu:** Chưa có output thực tế.

## D4 — Split Manifest

**File:** `data/processed/split_manifest.json`

| Trường | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `dev_start`, `dev_end` | timestamp UTC | UTC | Bắt đầu và kết thúc development sample |
| `val_start`, `val_end` | timestamp UTC | UTC | Bắt đầu và kết thúc validation sample |
| `test_start`, `test_end` | timestamp UTC | UTC | Bắt đầu và kết thúc final test |
| `fingerprint` | string | — | SHA-256 fingerprint của dữ liệu/schema |

**Đối chiếu:** Chưa có output thực tế.

## D5 — Regime Flags

**File:** `data/processed/regime_flags.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `ts_available` | datetime64[ns, UTC] | UTC | Thời điểm quan sát |
| `flag_high_vol` | bool | — | Cờ giai đoạn biến động cao |
| `flag_weekend` | bool | — | Cờ cuối tuần |
| `flag_event` | bool | — | Cờ cửa sổ sự kiện |
| `regime_bull_bear` | string | — | Nhãn chế độ bull/bear |

**Đối chiếu:** Chưa có output thực tế.

## D6 — Rolling Information Share

**File:** `outputs/tables/rolling_is.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `window_start` | datetime64[ns, UTC] | UTC | Đầu cửa sổ rolling |
| `window_end` | datetime64[ns, UTC] | UTC | Cuối cửa sổ rolling |
| `available_at` | datetime64[ns, UTC] | UTC | Thời điểm kết quả có thể sử dụng |
| `venue` | string | — | `{exchange}_{market_type}` |
| `is_lower` | float64 | tỷ lệ | Cận dưới Information Share |
| `is_upper` | float64 | tỷ lệ | Cận trên Information Share |
| `is_mid` | float64 | tỷ lệ | Giá trị giữa Information Share |
| `component_share` | float64 | không đơn vị | Component Share |
| `ils` | float64 | tỷ lệ | Information Leadership Share |
| `leader` | string, nullable | — | Venue dẫn giá |
| `status` | string | — | Trạng thái ước lượng |
| `reason` | string, nullable | — | Lý do khi không xác định được kết quả/leader |

**Đối chiếu:** Chưa có output thực tế.

## D7 — Gap Events

**File:** `outputs/tables/gap_events.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `event_id` | string/int | — | ID sự kiện |
| `ts_start` | datetime64[ns, UTC] | UTC | Bắt đầu sự kiện |
| `ts_end` | datetime64[ns, UTC] | UTC | Kết thúc sự kiện |
| `duration_s` | numeric | giây | Thời lượng sự kiện |
| `market_type` | string | — | `spot` hoặc `perp` |
| `buy_exchange` | string | — | Sàn mua |
| `sell_exchange` | string | — | Sàn bán |
| `gross_bps` | float64 | bps | Chênh lệch trước chi phí |
| `net_bps` | float64 | bps | Chênh lệch sau chi phí |
| `regime` | string | — | Chế độ thị trường |

**Đối chiếu:** Chưa có output thực tế.

## D8 — Trades Ledger

**File:** `outputs/tables/trades_ledger.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `signal_id` | string/int | — | ID tín hiệu |
| `ts_decision` | datetime64[ns, UTC] | UTC | Thời điểm ra quyết định |
| `ts_fill` | datetime64[ns, UTC], nullable | UTC | Thời điểm khớp lệnh |
| `market_type` | string | — | `spot` hoặc `perp` |
| `buy_exchange` | string | — | Sàn mua |
| `sell_exchange` | string | — | Sàn bán |
| `qty_btc` | float64 | BTC | Khối lượng giao dịch |
| `buy_px` | float64 | USDT | Giá mua |
| `sell_px` | float64 | USDT | Giá bán |
| `fees_usdt` | float64 | USDT | Tổng phí |
| `pnl_usdt` | float64 | USDT | Lãi/lỗ |
| `status` | string | — | Trạng thái giao dịch |
| `reject_reason` | string, nullable | — | Lý do từ chối nếu có |

**Đối chiếu:** Chưa có output thực tế.

## D9 — Funding Rate và Open Interest

**File:** `data/processed/funding_oi.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
| --- | --- | --- | --- |
| `ts_available` | datetime64[ns, UTC] | UTC | Thời điểm thông tin có thể sử dụng |
| `exchange` | string | — | Sàn giao dịch |
| `market_type` | string | — | `perp` |
| `symbol` | string | — | `BTC/USDT` |
| `funding_rate` | float64 | tỷ lệ/chu kỳ | Funding rate của từng chu kỳ, không annualize |
| `funding_interval_h` | float64 | giờ | Độ dài chu kỳ funding |
| `open_interest_base` | float64 | BTC | Open interest theo tài sản cơ sở |
| `open_interest_usdt` | float64 | USDT | Open interest quy đổi sang USDT |
| `data_kind` | string | — | Loại dữ liệu |

**Đối chiếu:** Chưa có output thực tế.
