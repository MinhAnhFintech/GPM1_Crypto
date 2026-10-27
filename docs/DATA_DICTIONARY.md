# DATA DICTIONARY

Tài liệu mô tả tên cột, kiểu dữ liệu, đơn vị và ý nghĩa của các dataset D1–D9 theo `DATA_CONTRACT.md`.

**Nguồn:** Data Contract v1.0; D2 và D9 mở rộng v1.1. Tích hợp bản TV1 `4b84343` và đối chiếu dữ liệu chuẩn ngày 27/10/2026.

**Trạng thái đối chiếu:** D1–D9 đã có output và được đối chiếu theo trạng thái dữ liệu tương ứng.

## Quy ước chung

| Nội dung | Quy ước |
|---|---|
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
|---|---|---|---|
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

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **3.006.714 dòng × 11 cột**.
- File có đủ 11 cột bắt buộc theo Data Contract.
- `ts_available`: `datetime64[ns, UTC]`.
- `open`, `high`, `low`, `close`, `volume_base`, `volume_quote`: `float64`.
- `exchange`, `market_type`, `symbol`, `data_kind`: `object` trong pandas.
- Toàn bộ dữ liệu có `data_kind = historical`.
- Có đủ 6 venue thuộc Binance, Bybit và OKX, gồm thị trường `spot` và `perp`.
- Khoảng thời gian quan sát: từ `2025-10-18 00:01:00+00:00` đến `2026-09-30 23:59:00+00:00`.
- Không phát hiện dòng trùng hoàn toàn.

## D2 — Live Quotes

**File:** `data/processed/live_quotes_long.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
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
|---|---|---|---|
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

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **168.178 dòng × 27 cột; 13 phiên**.
- `ts_recv_utc` và `ts_grid_utc` không có giá trị thiếu.
- Khoảng thời gian `ts_recv_utc`: từ `2026-10-17 13:00:07.008524+00:00` đến `2026-10-23 15:59:55.845024+00:00`.
- `ts_event_utc` có 28.034 giá trị thiếu.
- Toàn bộ 168.178 quan sát có `data_kind = live`.
- Dữ liệu gồm 84.088 quan sát `spot` và 84.090 quan sát `perp`.
- Có đủ 6 venue: `binance_spot`, `binance_perp`, `bybit_spot`, `bybit_perp`, `okx_spot`, `okx_perp`.
- Không phát hiện dòng trùng hoàn toàn.

## D3 — Panel 1 phút

**File:** `data/processed/panel_1m_wide.parquet`

| Cột / Index | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `ts_available` | datetime64[ns, UTC] | UTC | Index thời gian |
| `close__{venue}` | float64 | USDT | Giá close của từng venue |
| `log_close__{venue}` | float64 | log-price | Log tự nhiên của giá close |
| `volume_base__{venue}` | float64 | BTC | Khối lượng theo venue |
| `missing__{venue}` | bool | — | Cờ dữ liệu thiếu của venue |

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **501.119 dòng × 24 cột**.
- Index `ts_available` là thời gian UTC, tăng dần và không trùng.
- Có đủ 6 venue: `binance_spot`, `binance_perp`, `bybit_spot`, `bybit_perp`, `okx_spot`, `okx_perp`.
- Mỗi venue có 4 trường: `close__{venue}`, `log_close__{venue}`, `volume_base__{venue}` và `missing__{venue}`.
- Các cột giá, log-price và volume có kiểu `float64`; các cột `missing__{venue}` có kiểu `bool`.
- Bản bàn giao không ghi nhận dòng thiếu trên panel.
- Khoảng thời gian quan sát: từ `2025-10-18 00:01:00+00:00` đến `2026-09-30 23:59:00+00:00`.

## D3b — Features 1 phút

**File:** `data/processed/features_1m.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `ret_1m__{venue}` | float64 | không đơn vị | Log return 1 phút của venue |
| `vol_share__{venue}` | float64 | tỷ lệ | Tỷ trọng volume của venue |
| `rv_60m__{venue}` | float64 | không đơn vị | Sample standard deviation của `ret_1m` trong rolling window 60 phút; không annualize |
| `dispersion_bps_spot` | float64 | bps | Độ phân tán giá giữa các venue spot |
| `dispersion_bps_perp` | float64 | bps | Độ phân tán giá giữa các venue perp |

`ret_1m__{venue}` là log return 1 phút, tương ứng với chênh lệch log-price giữa hai quan sát liên tiếp.

`rv_60m__{venue}` là sample standard deviation của `ret_1m` trong rolling window 60 phút gần nhất, không annualize. Các quan sát đầu kỳ chưa đủ số quan sát tối thiểu của rolling window có thể mang giá trị `NaN` theo cấu hình pipeline.

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **501.119 dòng × 20 cột**.
- Index `ts_available` là thời gian UTC.
- Khoảng thời gian quan sát: từ `2025-10-18 00:01:00+00:00` đến `2026-09-30 23:59:00+00:00`.
- Số quan sát và phạm vi thời gian khớp với panel D3.
- File chứa các nhóm biến đặc trưng được quy định cho D3b, gồm log return 1 phút, tỷ trọng volume, volatility 60 phút và độ phân tán giá giữa các venue.
- Không phát hiện dòng trùng hoàn toàn.

## D4 — Split Manifest

**File:** `data/processed/split_manifest.json`

| Trường | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `contract_version` | string | — | Phiên bản Data Contract áp dụng cho split manifest |
| `data_kind` | string | — | Loại dữ liệu được chia mẫu |
| `fingerprint` | string | — | Fingerprint nhận diện dữ liệu và schema đầu vào |
| `fingerprint_method` | string | — | Phương pháp tạo fingerprint |
| `n_obs` | integer | số quan sát | Tổng số quan sát của panel |
| `ratios` | array[float] | tỷ lệ | Tỷ lệ chia development, validation và final test |
| `counts` | object | số quan sát | Số dòng của từng tập `dev`, `val`, `test` |
| `test_policy` | string | — | Chính sách sử dụng final test |
| `test_consumed` | bool | — | Cho biết final test đã được sử dụng hay chưa |
| `dev_start`, `dev_end` | timestamp UTC | UTC | Bắt đầu và kết thúc development sample |
| `val_start`, `val_end` | timestamp UTC | UTC | Bắt đầu và kết thúc validation sample |
| `test_start`, `test_end` | timestamp UTC | UTC | Bắt đầu và kết thúc final test |

### Khóa `.test.lock`

`.test.lock` là khóa bảo vệ quy trình final test. Khóa này được sử dụng cùng với `test_policy` và `test_consumed` nhằm hạn chế việc final test bị chạy lặp lại hoặc được sử dụng trong quá trình lựa chọn tham số.

Development và validation được sử dụng trong quá trình xây dựng, điều chỉnh và lựa chọn mô hình. Final test chỉ được sử dụng cho lần đánh giá cuối theo chính sách của pipeline. Vì vậy, không chỉnh tay hoặc xóa `.test.lock` để vượt qua cơ chế bảo vệ final test.

### Đối chiếu dữ liệu thật

- `contract_version = "1.0"`.
- `data_kind = "historical"`.
- `n_obs = 501119`.
- `ratios = [0.6, 0.2, 0.2]`.
- `counts = {dev: 300671, val: 100223, test: 100225}`.
- Development: `2025-10-18T00:01:00+00:00` đến `2026-05-14T19:11:00+00:00`.
- Validation: `2026-05-14T19:12:00+00:00` đến `2026-07-23T09:34:00+00:00`.
- Final test: `2026-07-23T09:35:00+00:00` đến `2026-09-30T23:59:00+00:00`.
- `fingerprint = 9420d92ff9d5d0a334d8289b16bc56cb9b86720150d86316b03ff768325356a4`.
- `fingerprint_method = "sha256:pandas_hash_values+schema+index+attrs"`.
- `test_policy = "single_final_evaluation"`.
- `test_consumed = true`; final test đã dùng một lần cho T5b và khóa `.test.lock` còn nguyên.

## D5 — Regime Flags

**File:** `data/processed/regime_flags.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `ts_available` | datetime64[ns, UTC] | UTC | Thời điểm quan sát |
| `flag_high_vol` | bool | — | Cờ giai đoạn biến động cao |
| `flag_weekend` | bool | — | Cờ cuối tuần |
| `flag_event` | bool | — | Cờ cửa sổ sự kiện |
| `regime_bull_bear` | string | — | Nhãn chế độ bull/bear |

## Quy tắc tạo cờ

- `flag_high_vol`: được xác định bằng ngưỡng P90 của `rv_60m` trên **development sample**. Ngưỡng được xác định trên development để không sử dụng validation hoặc final test cho việc lựa chọn ngưỡng. Dev-only P90 của lần chạy đã đối chiếu là `0.001007695261053493`.
- `flag_weekend`: nhận `true` khi `ts_available` theo UTC rơi vào thứ Bảy hoặc Chủ nhật.
- `flag_event`: nhận `true` khi `ts_available` nằm trong khoảng `start_utc`–`end_utc` của một event trong `config/events.yaml`. Output hiện tại sử dụng 8 event `E01`–`E08`; số quan sát được gắn cờ phụ thuộc vào nội dung chính xác của file cấu hình này.
- `regime_bull_bear`: được xác định bằng cách so sánh `close__binance_spot` với trung bình trượt 30 ngày (trailing rolling mean, gồm cả quan sát hiện tại). Quan sát được gắn `bull` khi giá nằm trên trung bình trượt và `bear` khi giá nằm dưới trung bình trượt. Giai đoạn 30 ngày đầu được để trống do chưa đủ dữ liệu cho rolling window.

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **501.119 dòng × 5 cột**.
- Có đủ 5 trường: `ts_available`, `flag_high_vol`, `flag_weekend`, `flag_event`, `regime_bull_bear`.
- Khoảng thời gian quan sát: từ `2025-10-18 00:01:00+00:00` đến `2026-09-30 23:59:00+00:00`.
- `flag_high_vol = true`: 40.635 quan sát.
- `flag_weekend = true`: 143.999 quan sát.
- `flag_event = true`: 28.800 quan sát.
- `regime_bull_bear`: 218.496 quan sát `bull`, 239.424 quan sát `bear` và 43.199 quan sát chưa có nhãn.
- Tổng số quan sát của các trạng thái `regime_bull_bear` bằng 501.119.
- Không phát hiện dòng trùng hoàn toàn.

## Tái lập D3b và D5

Foundation historical:

```powershell
py -3.12 run_all.py --mode historical --config config/config.yaml --foundation-only
```

D3b:

```powershell
py -3.12 scripts/build_features_1m.py
```

D5:

```powershell
py -3.12 -m src.features.regime_flags
```

Sau khi tái lập, đối chiếu số dòng, schema, phạm vi timestamp và các đặc trưng dữ liệu với artifact bàn giao tương ứng. Không sử dụng final test để lựa chọn hoặc hiệu chỉnh ngưỡng.

## D6 — Rolling Information Share

**File:** `outputs/tables/rolling_is.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `window_start` | datetime64[ns, UTC] | UTC | Đầu cửa sổ rolling |
| `window_end` | datetime64[ns, UTC] | UTC | Cuối cửa sổ rolling |
| `available_at` | datetime64[ns, UTC] | UTC | Thời điểm kết quả có thể sử dụng |
| `venue` | string | — | `{exchange}_{market_type}` |
| `is_lower` | float64 | tỷ lệ | Cận dưới Information Share |
| `is_upper` | float64 | tỷ lệ | Cận trên Information Share |
| `is_mid` | float64 | tỷ lệ | Giá trị giữa Information Share |
| `component_share` | float64 | không đơn vị | Component Share |
| `ils` | float64 | tỷ lệ | Information Leadership Share |
| `leader` | string, nullable | — | Venue dẫn giá nếu xác định được |
| `status` | string | — | Trạng thái ước lượng |
| `reason` | string, nullable | — | Lý do khi không xác định được leader/kết quả |

### Đối chiếu dữ liệu thật

Đã đối chiếu với `outputs/tables/rolling_is.parquet` do TV5 bàn giao.

- Kích thước thực tế: **39.948 dòng**, tương ứng **6.658 rolling window × 6 venue**.
- `window_start`: từ `2025-10-18 00:01:00+00:00` đến `2026-07-22 09:01:00+00:00`; không có giá trị thiếu.
- `window_end`: từ `2025-10-19 00:00:00+00:00` đến `2026-07-23 09:00:00+00:00`; không có giá trị thiếu.
- `available_at`: từ `2026-05-14 19:11:00+00:00` đến `2026-07-23 09:00:00+00:00`; không có giá trị thiếu.
- Có đủ 6 venue; mỗi venue có 6.658 quan sát.
- Toàn bộ 39.948 dòng có `status = ok`.
- `leader` không được gán ở cả 39.948 dòng.
- `reason = overlapping_bounds` ở cả 39.948 dòng. Điều này có nghĩa các cận Information Share bị chồng lấn và không có rolling window nào tách được một venue dẫn giá theo tiêu chí của hợp đồng. Đây là **kết quả hợp lệ theo Data Contract, không phải lỗi pipeline**.
- Cột `ils` là trung bình ILS của các cặp có venue tương ứng, do đó ILS của 6 venue trong cùng một cửa sổ không được yêu cầu cộng lại bằng 1.
- `component_share` có dấu và có thể âm hoặc lớn hơn 1. Trong output đã đối chiếu, giá trị nằm trong khoảng xấp xỉ **−15,983 đến +19,402**.
- Component Share vẫn cộng lại bằng 1 trong từng rolling window và Data Contract không yêu cầu ép từng giá trị riêng lẻ vào `[0, 1]`.
- Độ lớn của `component_share` cho thấy ước lượng có thể không ổn định khi giá giữa các venue gần như cùng biến động. Vì vậy, **không nên diễn giải riêng lẻ `component_share` như một tỷ trọng kinh tế trực tiếp**; cần xem xét cùng các chỉ số price discovery khác.
- Khi ghép D6 với dataset khác, sử dụng `available_at` để tránh sử dụng thông tin tương lai.
- Không phát hiện dòng trùng hoàn toàn.

**Lưu ý diễn giải:** Ở dữ liệu 1 phút, cận Information Share của các venue chồng lấn nên D6 không xác định được một venue dẫn giá riêng biệt trong 6.658 rolling window. ILS có thể nghiêng về một số venue nhưng không nên được sử dụng riêng để kết luận leadership khi kết quả không ổn định giữa development và validation.

## D7 — Gap Events

**File:** `outputs/tables/gap_events.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `event_id` | string | — | ID sự kiện |
| `buy_exchange` | string | — | Sàn mua |
| `sell_exchange` | string | — | Sàn bán |
| `market_type` | string | — | `spot` hoặc `perp` |
| `ts_start` | datetime64[ns, UTC] | UTC | Bắt đầu sự kiện |
| `ts_end` | datetime64[ns, UTC] | UTC | Kết thúc sự kiện |
| `n_obs` | integer | số quan sát | Số quan sát thuộc sự kiện |
| `duration_s` | numeric | giây | Thời lượng sự kiện |
| `gross_bps` | float64 | bps | Chênh lệch trước chi phí |
| `net_bps` | float64 | bps | Chênh lệch sau chi phí |
| `max_gross_gap_bps` | float64 | bps | Gross gap lớn nhất trong sự kiện |
| `mean_gross_gap_bps` | float64 | bps | Gross gap trung bình |
| `max_net_gap_bps` | float64 | bps | Net gap lớn nhất |
| `any_net_positive` | bool | — | Có ít nhất một quan sát net gap dương |
| `censored_start` | bool | — | Sự kiện bị censor ở đầu |
| `censored_end` | bool | — | Sự kiện bị censor ở cuối |
| `regime` | string, nullable | — | Chế độ thị trường |
| `data_kind` | string | — | Loại dữ liệu |

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **3.209 dòng × 18 cột**.
- Toàn bộ 3.209 quan sát có `data_kind = live`.
- Có 1.702 quan sát `perp` và 1.507 quan sát `spot`.
- `ts_start` và `ts_end` không có giá trị thiếu.
- Khoảng `ts_start`: từ `2026-10-17 15:34:05+00:00` đến `2026-10-23 15:54:30+00:00`.
- Khoảng `ts_end`: từ `2026-10-17 15:34:05+00:00` đến `2026-10-23 15:54:30+00:00`.
- `regime` hiện chưa được gán cho 3.209 quan sát.
- Không phát hiện dòng trùng hoàn toàn.
- Các cặp arbitrage chỉ được xây dựng **trong cùng `market_type`**; không ghép spot với perpetual.

## D8 — Trades Ledger

**File:** `outputs/tables/trades_ledger.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `signal_id` | string | — | ID tín hiệu |
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

### Các trường mở rộng trong output hiện tại

`buy_venue`, `sell_venue`, `session_id`, `latency_s`, `decision_mid_gap_bps`, `decision_gross_bps`, `decision_net_bps`, `gross_bps`, `net_bps`, `fee_total_bps`, `other_cost_usdt`, `buy_top_size_btc`, `sell_top_size_btc`, `buy_quote_age_s`, `sell_quote_age_s`, `method`, `data_kind`.

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **2 dòng × 30 cột**.
- Cả 2 quan sát có `data_kind = live`.
- Cả 2 giao dịch thuộc `market_type = perp`.
- Cả 2 tín hiệu có `status = filled`.
- `ts_decision` và `ts_fill` không có giá trị thiếu.
- Hai giao dịch có `ts_decision = 2026-10-21 13:04:40+00:00`.
- `ts_fill = 2026-10-21 13:04:45+00:00`.
- Không phát hiện dòng trùng hoàn toàn.

## D9 — Funding Rate và Open Interest

**File:** `data/processed/funding_oi.parquet`

| Cột | Kiểu | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `ts_available` | datetime64[ns, UTC] | UTC | Thời điểm thông tin có thể sử dụng |
| `exchange` | string | — | Sàn giao dịch |
| `market_type` | string | — | `perp` |
| `symbol` | string | — | `BTC/USDT` |
| `funding_rate` | float64 | tỷ lệ/chu kỳ | Funding rate của từng chu kỳ, không annualize |
| `funding_interval_h` | float64 | giờ | Độ dài chu kỳ funding |
| `open_interest_base` | float64 | BTC | Open interest theo tài sản cơ sở |
| `open_interest_usdt` | float64 | USDT | Open interest quy đổi sang USDT |
| `data_kind` | string | — | Loại dữ liệu |
| `in_live_window` | bool, không null | — | `ts_available >= live_start_utc`; không phải nhãn nguồn dữ liệu |

### Đối chiếu dữ liệu thật

- Kích thước thực tế: **4.412 dòng × 15 cột**.
- File thực tế có thêm các trường `ts_utc`, `venue`, `funding_rate_bps`, `funding_ts_utc`, `oi_ts_utc` và `in_live_window`.
- `ts_available` không có giá trị thiếu.
- Khoảng `ts_available`: từ `2025-10-18 08:00:00+00:00` đến `2026-10-23 17:00:00+00:00`.
- Toàn bộ 4.412 quan sát có `data_kind = historical` và `market_type = perp`.
- Có 3 venue perpetual: `binance_perp` (1.746 quan sát), `bybit_perp` (1.746) và `okx_perp` (920).
- Không phát hiện dòng trùng hoàn toàn.

### D9 v1.1 — ý nghĩa `in_live_window`

Cờ dùng mốc bắt đầu ngày thu live theo `config/live_sessions.yaml`: 17/10/2026 00:00 giờ Việt Nam = **16/10/2026 17:00 UTC**. True khi `ts_available` từ mốc này trở đi; không đặt trần bằng ngày kết thúc và không kiểm tra dòng nằm trong phiên S1/S2. Toàn bộ D9 vẫn mang `data_kind=historical`, kể cả khi cờ True. Không đổi nhãn thành live.

Bản hiện hành có **507 dòng True**, **3905 dòng False**, không null. Bản v1.0 không có cờ vẫn được chấp nhận; v1.1 bắt buộc có cờ. Nếu có mặt ở bất kỳ phiên bản nào, kiểu phải là boolean không null; không ép chuỗi hoặc 0/1.
