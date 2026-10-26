# Crypto Price Discovery & Cross-Exchange Arbitrage

Dự án nghiên cứu quá trình **price discovery** của Bitcoin trên nhiều sàn giao dịch và đánh giá khả năng **cross-exchange arbitrage** sau khi tính đến chi phí giao dịch. Phân tích tập trung vào **BTC/USDT** trên Binance, Bybit và OKX, bao gồm cả thị trường **spot** và **perpetual**.

Dự án kết hợp dữ liệu lịch sử tần suất **1 phút** với dữ liệu **bid/ask live** để thực hiện kiểm định đồng liên kết, ước lượng VECM, Information Share (IS), Component Share (CS), Information Leadership Share (ILS), phân tích rolling, đánh giá mối liên hệ giữa vai trò dẫn giá với thanh khoản và trạng thái thị trường, phân tích gap giữa các sàn và backtest arbitrage sau chi phí.

---

## 1. Cấu trúc repository

```text
GPM1_Crypto/
│
├── run_all.py
├── requirements.txt
│
├── config/
│   ├── config.yaml
│   ├── exchanges.yaml
│   ├── fees.yaml
│   ├── events.yaml
│   ├── live_sessions.yaml
│   └── frozen_t5b.yaml
│
├── docs/
│   ├── DATA_CONTRACT.md
│   ├── DATA_DICTIONARY.md
│   ├── SOURCE_LOG.md
│   └── EXCHANGE_FEASIBILITY.md
│
├── data/
│   ├── raw/
│   ├── interim/
│   ├── processed/
│   └── fixtures/
│
├── src/
│   ├── collectors/
│   ├── synchronization/
│   ├── features/
│   ├── cointegration/
│   ├── price_discovery/
│   ├── cost_model/
│   ├── backtest/
│   ├── evaluation/
│   └── utils/
│
├── scripts/
├── notebooks/
│   ├── templates/
│   ├── tv2/
│   └── tv3/
│
├── dashboard/
├── tests/
│
└── outputs/
    ├── tables/
    ├── figures/
    └── synthetic/
```

Trong đó:

- `run_all.py`: điều phối các bước nền của pipeline.
- `config/`: lưu cấu hình sàn, phí giao dịch, sự kiện, phiên live và các tham số nghiên cứu.
- `docs/`: tài liệu về cấu trúc dữ liệu, nguồn dữ liệu và quy ước của dự án.
- `data/`: chứa dữ liệu thô, dữ liệu trung gian, dữ liệu đã xử lý và dữ liệu giả lập.
- `src/`: mã nguồn chính cho thu thập, đồng bộ, price discovery, mô hình chi phí, backtest và evaluation.
- `scripts/`: các script hỗ trợ tạo đặc trưng, bảng và hình.
- `notebooks/`: chứa các script phân tích và trực quan hóa bổ sung.
- `dashboard/`: ứng dụng Streamlit dùng để trực quan hóa kết quả.
- `tests/`: các kiểm thử của dự án.
- `outputs/`: chứa bảng, hình và các kết quả được tạo từ pipeline.

---

## 2. Yêu cầu môi trường

Dự án sử dụng **Python 3.12**.

Có thể kiểm tra phiên bản Python bằng:

```bash
python --version
```

Tùy hệ điều hành và cách cài đặt Python, lệnh gọi trình thông dịch có thể khác nhau:

```text
Windows        : python hoặc py
macOS / Linux  : python3 hoặc python
```

Các lệnh trong README sử dụng `python` làm quy ước chung. Nếu máy không nhận lệnh `python`, có thể thay `python` bằng `py` trên Windows hoặc `python3` trên macOS/Linux.

Ví dụ:

```bash
# Cách viết được sử dụng trong README
python -m pytest -q

# Windows nếu không nhận "python"
py -m pytest -q

# macOS/Linux nếu sử dụng "python3"
python3 -m pytest -q
```

Các thư viện cần thiết được khai báo trong:

```text
requirements.txt
```

Từ thư mục gốc của repository, cài đặt bằng:

```bash
python -m pip install -r requirements.txt
```

Ví dụ, trên Windows nếu máy chỉ nhận Python Launcher:

```bash
py -m pip install -r requirements.txt
```

Seed sử dụng cho các bước có yếu tố ngẫu nhiên là:

```text
20261017
```

và được đọc từ `config/config.yaml`.

---

## 3. Dữ liệu đầu vào

Ba bộ dữ liệu chính được sử dụng trong nghiên cứu:

| Dữ liệu | Đường dẫn |
|---|---|
| D1 – Nến 1 phút của 6 venue | `data/interim/ohlcv_1m_long.parquet` |
| D2 – Báo giá live của 13 phiên | `data/processed/live_quotes_long.parquet` |
| D9 – Funding rate và Open Interest | `data/processed/funding_oi.parquet` |

Dữ liệu lịch sử bao gồm sáu venue:

```text
binance_spot
binance_perp
bybit_spot
bybit_perp
okx_spot
okx_perp
```

Dữ liệu thật không được đưa trực tiếp lên Git. Trước khi chạy pipeline, cần đặt các file dữ liệu vào đúng đường dẫn tương ứng.

Các file trung gian quan trọng được tạo trong quá trình xử lý gồm:

```text
data/processed/panel_1m_wide.parquet
data/processed/split_manifest.json
data/processed/features_1m.parquet
data/processed/regime_flags.parquet
data/processed/live_session_is.parquet
```

Không thay thế dữ liệu nghiên cứu bằng dữ liệu tải lại tại thời điểm khác nếu mục tiêu là tái lập chính xác kết quả trong báo cáo.

---

## 4. Kiểm tra mã nguồn

Trước khi chạy các bước nghiên cứu, có thể kiểm tra toàn bộ test bằng:

```bash
python -m pytest -q
```

Các test kiểm tra những thành phần như:

- đồng bộ thời gian;
- schema dữ liệu;
- chia mẫu;
- Information Share và ILS;
- mô hình chi phí;
- ledger backtest;
- điều phối pipeline;
- các hàm tạo bảng và hình.

---

## 5. Kiểm chứng trên dữ liệu giả lập

Dữ liệu giả lập được sử dụng để kiểm tra pipeline trong trường hợp venue dẫn giá được xác định trước.

### 5.1. Kiểm chứng tổng quát

```bash
python run_all.py --mode synthetic --verify
```

### 5.2. Trường hợp spot–perpetual

```bash
python run_all.py --mode synthetic --fixture-case spot_perp
```

### 5.3. Pipeline giả lập nhiều venue

```bash
python run_all.py --mode synthetic --config config/pipeline.synthetic.example.yaml --fixture-case multi --data-dir outputs/synthetic/integrated/multi/data --output-dir outputs/synthetic/integrated/multi
```

Các kết quả giả lập được lưu riêng tại:

```text
outputs/synthetic/
```

để tránh nhầm lẫn với kết quả nghiên cứu trên dữ liệu thật.

---

## 6. Chuẩn bị và kiểm tra dữ liệu thật

### 6.1. Kiểm tra chất lượng dữ liệu OHLCV lịch sử

Chạy:

```bash
python src/features/ohlcv_quality.py
```

Đầu ra:

```text
outputs/tables/T0_data_quality.csv
```

Bảng T0 kiểm tra chất lượng dữ liệu OHLCV 1 phút của từng venue, bao gồm mức độ bao phủ dữ liệu, timestamp, giá trị thiếu, tính nhất quán của OHLC và các vấn đề chất lượng dữ liệu liên quan.

Bước này có mục đích kiểm tra và báo cáo chất lượng dữ liệu, không tự động chỉnh sửa dữ liệu nguồn.

### 6.2. Kiểm tra coverage dữ liệu live

Dữ liệu live được thu theo phiên từ cấu hình `config/live_sessions.yaml`. Dữ liệu theo từng phiên được lưu trong:

```text
data/raw/live/
```

Để tạo lại báo cáo coverage từ dữ liệu live đã thu, chạy:

```bash
python src/collectors/live/collect_live.py coverage --csv-report outputs/tables/T0b_live_coverage.csv
```

Đầu ra:

```text
outputs/tables/T0b_live_coverage.csv
```

T0b thống kê coverage theo `session × exchange × market_type`, bao gồm số quan sát kỳ vọng, số quan sát thực tế, duplicate/off-grid rows, coverage, gap, latency và các thông tin liên quan đến depth.

Không cần chạy lại quá trình thu thập live để tái lập T0b; chỉ cần sử dụng dữ liệu live đã được lưu.

### 6.3. Xây dựng historical foundation

Từ thư mục gốc của repository, chạy:

```bash
python run_all.py --mode historical --config config/config.yaml --foundation-only
```

Bước này thực hiện các xử lý nền từ D1, bao gồm:

- xây dựng panel 1 phút đồng bộ;
- tạo `panel_1m_wide.parquet`;
- tạo hoặc kiểm tra `split_manifest.json`;
- chia dữ liệu theo thời gian;
- thực hiện phân tích lead–lag nền;
- tạo các đầu ra nền liên quan.

Sau đó có thể kiểm tra dữ liệu bàn giao bằng:

```bash
python -m src.utils.check_handoff --input data/processed/panel_1m_wide.parquet --schema D3 --expected-kind historical --config config/config.yaml --manifest data/processed/split_manifest.json --report outputs/D3_D4_handoff.json
```

Nếu `split_manifest.json` đã tồn tại, chương trình sử dụng lại manifest sau khi kiểm tra fingerprint và ranh giới mẫu thay vì tự tạo lại.

Thiết kế chia mẫu của nghiên cứu gồm:

```text
Development : 60%
Validation  : 20%
Final test  : 20%
```

Final test được giữ riêng và không được sử dụng trong quá trình lựa chọn mô hình.

---

## 7. Chạy các bước nghiên cứu

Sau khi hoàn tất phần chuẩn bị dữ liệu, chạy các bước dưới đây theo thứ tự.

### 7.1. Tạo đặc trưng 1 phút

```bash
python scripts/build_features_1m.py
```

Đầu ra:

```text
data/processed/features_1m.parquet
```

File này chứa các đặc trưng được sử dụng cho các bước sau như log return, realized volatility và volume share.

### 7.2. Xác định market regime

```bash
python -m src.features.regime_flags
```

Đầu ra:

```text
data/processed/regime_flags.parquet
```

Các regime được sử dụng trong nghiên cứu gồm:

- weekend / weekday;
- high / low volatility;
- bull / bear;
- event / normal.

### 7.3. Price discovery

```bash
python -m src.price_discovery.run_price_discovery --data-dir data/processed --output-dir outputs --config config/config.yaml --workers 8
```

Bước này thực hiện các phân tích chính về price discovery, bao gồm:

- kiểm định tính dừng;
- kiểm định đồng liên kết;
- VECM;
- Information Share;
- Component Share;
- Information Leadership Share;
- rolling Information Share;
- sensitivity analysis;
- so sánh spot và perpetual.

Các đầu ra chính gồm:

```text
outputs/tables/T2_*.csv
outputs/tables/T3_*.csv
outputs/tables/T3b_sensitivity.csv
outputs/tables/T3c_spot_perp.csv
outputs/tables/T3d_ils.csv
outputs/tables/T3d_ils_summary.csv
outputs/tables/rolling_is.parquet
```

Các phân tích này sử dụng development và validation theo thiết kế nghiên cứu, không sử dụng final test để lựa chọn mô hình.

### 7.4. Hồi quy thanh khoản và vai trò dẫn giá

Mô hình T4 đánh giá mối liên hệ giữa Information Share và các đặc điểm thanh khoản/thị trường.

Có thể kiểm tra trước các file đầu vào bằng:

```bash
python notebooks/tv3/liquidity_regression.py --inspect
```

Chạy mô hình trên mẫu lịch sử:

```bash
python notebooks/tv3/liquidity_regression.py
```

Đầu ra:

```text
outputs/tables/T4_leadership_determinants.csv
```

Mô hình lịch sử sử dụng các biến như:

```text
vol_share
rv_60m
is_perp
```

Các biến liên tục được chuẩn hóa trước khi ước lượng. Kết quả được diễn giải là **mối liên hệ**, không phải quan hệ nhân quả.

#### T4 – mô hình live

Để tái lập mô hình live, trước tiên tạo Information Share theo từng phiên live bằng:

```bash
python -m src.price_discovery.run_live_is
```

Bước này tạo:

```text
data/processed/live_session_is.parquet
```

Sau khi file trên được tạo, chạy:

```bash
python notebooks/tv3/liquidity_regression.py --live-is data/processed/live_session_is.parquet
```

Phần live sử dụng các biến được xây dựng trực tiếp từ dữ liệu live như:

```text
rv_live
depth_share
spread_bps
log_depth
is_perp
```

Nếu có dữ liệu nến 1 phút tương ứng với các phiên live, có thể truyền thêm `--live-candles` để ước lượng mô hình mở rộng có `vol_share`.

Do mẫu live ngắn, kết quả phần này được xem là bằng chứng thăm dò.

### 7.5. Phân tích Information Share theo market regime

```bash
python -m src.evaluation.stress_regime
```

Đầu vào:

```text
outputs/tables/rolling_is.parquet
data/processed/regime_flags.parquet
```

Đầu ra:

```text
outputs/tables/T6_stress.csv
```

Bảng T6 so sánh rolling Information Share giữa các trạng thái:

```text
weekend vs weekday
high vs low volatility
bull vs bear
event vs normal
```

Regime được gắn với mỗi cửa sổ rolling IS theo thông tin đã có tại thời điểm cửa sổ kết thúc để tránh sử dụng thông tin tương lai.

### 7.6. Xác định gap trên dữ liệu lịch sử

Gap lịch sử được xác định từ panel 1 phút:

```bash
python -m src.cost_model.run_gap_events --panel data/processed/panel_1m_wide.parquet
```

Đầu ra:

```text
outputs/tables/gap_events_historical.parquet
```

Phân tích lịch sử sử dụng dữ liệu 1 phút nên thời gian tồn tại của gap được đo theo độ phân giải của panel lịch sử.

### 7.7. Xác định gap trên dữ liệu live

Gap live được xác định trực tiếp từ dữ liệu bid/ask:

```bash
python -m src.cost_model.run_gap_events --quotes data/processed/live_quotes_long.parquet --config config/config.yaml
```

Đầu ra:

```text
outputs/tables/gap_events.parquet
```

Không chạy `run_gap_events` mà không truyền `--panel` hoặc `--quotes` khi tái lập kết quả nghiên cứu, vì chế độ không có input thật có thể sử dụng dữ liệu fixture/synthetic.

### 7.8. Backtest A – dữ liệu lịch sử

Chạy:

```bash
python -m src.backtest.run_backtest_a
```

Backtest A sử dụng **development split** của panel lịch sử 1 phút. Half-spread được hiệu chỉnh từ dữ liệu live D2 trước khi chạy backtest.

Các đầu ra được lưu trong:

```text
outputs/tables/
```

với prefix:

```text
backtest_a_dev_L{latency}
```

bao gồm:

```text
backtest_a_dev_L{latency}_ledger.parquet
backtest_a_dev_L{latency}_summary.csv
backtest_a_dev_L{latency}_params.json
half_spread_calibration.csv
```

Trong đó `{latency}` được lấy từ `backtest_a.latency_min` trong `config/config.yaml` nếu không được ghi đè bằng `--latency-min`.

Runner chỉ cho phép sử dụng `dev` hoặc `val`; **final test không được đọc trong Backtest A**.

Tên ledger, summary, params và file hiệu chỉnh half-spread được tạo trực tiếp theo prefix của split và latency.

### 7.9. Backtest B – dữ liệu live

```bash
python -m src.backtest.run_backtest_b
```

Backtest B sử dụng dữ liệu bid/ask live để đánh giá khả năng thực hiện arbitrage trong điều kiện gần với giao dịch thực tế hơn.

Đầu ra chính:

```text
outputs/tables/trades_ledger.parquet
```

Ledger này thuộc **Backtest B/live** và được tách biệt với ledger của Backtest A.

### 7.10. Tổng hợp kết quả arbitrage sau chi phí

```bash
python -m src.backtest.make_t5
```

Đầu ra:

```text
outputs/tables/T5_arbitrage_net.csv
```

Bảng T5 tổng hợp kết quả arbitrage sau khi tính các thành phần chi phí được đưa vào mô hình.

### 7.11. Phân tích độ nhạy chi phí T5b – không chạy lại

T5b sử dụng **final test** một lần theo thiết kế nghiên cứu.

Lệnh đã được sử dụng trước đó là:

```bash
python -m src.backtest.run_t5b --split test
```

Tuy nhiên, **không chạy lại lệnh này khi tái lập repository hiện tại**.

Trạng thái:

```text
test_consumed = true
```

được lưu trong:

```text
data/processed/split_manifest.json
```

và cơ chế khóa `.test.lock` được sử dụng để ngăn final test bị mở lại.

Đầu ra đã được lưu:

```text
outputs/tables/T5b_cost_sensitivity.csv
```

Khi tái lập kết quả, giữ nguyên file T5b hiện có và chuyển sang bước tiếp theo.

**Không xóa `.test.lock`, không chỉnh `test_consumed` về `false` và không chạy lại T5b trên final test.**

### 7.12. Phân tích gap theo regime

```bash
python -m src.backtest.make_t6b
```

Đầu ra:

```text
outputs/tables/T6b_gap_by_regime.csv
```

T6b mô tả tần suất và thời gian tồn tại của gap trong các trạng thái thị trường khác nhau.

### 7.13. Tạo F1 và F2

```bash
python -m notebooks.tv2.plot_c2_05
```

Đầu ra:

```text
outputs/figures/F1_synchronized_prices.png
outputs/figures/F2_price_dispersion.png
```

F1 trình bày giá đã được đồng bộ giữa các venue.

F2 trình bày độ phân tán giá giữa các sàn theo bps, tách riêng spot và perpetual.

Price dispersion được xác định theo:

```text
(max price - min price) / mean price × 10,000
```

Nếu bất kỳ venue nào thuộc market đang xét bị thiếu giá tại một thời điểm, dispersion của market đó tại thời điểm tương ứng được giữ là missing thay vì nội suy.

### 7.14. Tạo F3 – Rolling Information Share

```bash
python scripts/make_figure_f3.py
```

Đầu vào:

```text
outputs/tables/rolling_is.parquet
```

Đầu ra:

```text
outputs/figures/F3_rolling_is_bounds.png
```

F3 trình bày rolling Information Share và khoảng cận dưới/cận trên theo từng venue.

Các cửa sổ có trạng thái khác `ok` được để trống, không nội suy.

### 7.15. Tạo F4, F5 và đối chiếu ledger

```bash
python -m src.backtest.make_figures
python -m src.backtest.reconcile
```

Các đầu ra chính gồm:

```text
outputs/figures/F4_*.png
outputs/figures/F5_*.png
outputs/tables/T5c_*
```

F4 mô tả thời gian tồn tại của gap trên dữ liệu lịch sử và live.

F5 trình bày P&L tích lũy của các backtest.

Bước reconcile được sử dụng để đối chiếu ledger và các kết quả tổng hợp.

### 7.16. Tạo F6 – Lead–lag

```bash
python scripts/make_figure_f6.py
```

Đầu ra:

```text
outputs/figures/F6_leadlag_xcorr.png
```

F6 sử dụng log return 1 phút trên development sample để mô tả tương quan lead–lag giữa các cặp venue.

Quy ước:

```text
corr(ret_A(t), ret_B(t + lag))
```

Lag dương nghĩa là venue A đứng trước venue B theo quy ước của hình.

F6 mang tính mô tả; tương quan tại lag 0 thường chiếm ưu thế ở dữ liệu tần suất 1 phút nên không được sử dụng riêng để kết luận venue dẫn giá.

### 7.17. Tạo F7 – Spot và perpetual

```bash
python scripts/make_figure_f7.py
```

Đầu vào:

```text
outputs/tables/T3c_spot_perp.csv
```

Đầu ra:

```text
outputs/figures/F7_spot_perp_is_ils.png
```

F7 so sánh Information Share và Information Leadership Share giữa spot và perpetual trong cùng một sàn trên development và validation.

Khi khoảng Information Share của hai thị trường chồng nhau, kết quả không được diễn giải như bằng chứng chắc chắn rằng một bên dẫn bên còn lại.

### 7.18. Đặc điểm venue

```bash
python -m scripts.exchange_characteristics --ohlcv data/interim/ohlcv_1m_long.parquet --live data/processed/live_quotes_long.parquet --funding data/processed/funding_oi.parquet --manifest data/processed/split_manifest.json --fees config/fees.yaml --out outputs/tables/T1_exchange_characteristics.csv --sample dev_val
```

Đầu ra:

```text
outputs/tables/T1_exchange_characteristics.csv
```

Bảng T1 tổng hợp các đặc điểm chính của các venue được sử dụng trong nghiên cứu.

Nếu D1, D2 hoặc các dữ liệu đầu vào liên quan được cập nhật hoặc điều chỉnh, T1 cần được chạy lại trên phiên bản dữ liệu tương ứng để đảm bảo kết quả đồng nhất với dữ liệu nghiên cứu.

---

## 8. Thứ tự tái lập kết quả

Từ một môi trường mới đã có đầy đủ dữ liệu đầu vào, có thể chạy toàn bộ historical pipeline bằng một lệnh:

```bash
python run_all.py --mode historical --config config/config.yaml
```

Lệnh trên chạy toàn bộ các bước được điều phối trong historical pipeline và không mở lại final test. Danh sách bên dưới được giữ lại để có thể tái lập hoặc kiểm tra từng bước riêng lẻ.

```bash
# 1. Cài thư viện
python -m pip install -r requirements.txt

# 2. Kiểm tra mã nguồn
python -m pytest -q

# 3. Kiểm tra chất lượng OHLCV lịch sử
python src/features/ohlcv_quality.py

# 4. Tạo báo cáo coverage dữ liệu live đã thu
python src/collectors/live/collect_live.py coverage --csv-report outputs/tables/T0b_live_coverage.csv

# 5. Kiểm chứng synthetic
python run_all.py --mode synthetic --verify

# 6. Xây dựng historical foundation
python run_all.py --mode historical --config config/config.yaml --foundation-only

# 7. Kiểm tra D3/D4
python -m src.utils.check_handoff --input data/processed/panel_1m_wide.parquet --schema D3 --expected-kind historical --config config/config.yaml --manifest data/processed/split_manifest.json --report outputs/D3_D4_handoff.json

# 8. Tạo features
python scripts/build_features_1m.py

# 9. Tạo market regimes
python -m src.features.regime_flags

# 10. Price discovery
python -m src.price_discovery.run_price_discovery --data-dir data/processed --output-dir outputs --config config/config.yaml --workers 8

# 11. Liquidity regression – historical
python notebooks/tv3/liquidity_regression.py

# 12. Tạo Information Share theo phiên live
python -m src.price_discovery.run_live_is

# 13. Liquidity regression – live
python notebooks/tv3/liquidity_regression.py --live-is data/processed/live_session_is.parquet

# 14. Stress/regime analysis
python -m src.evaluation.stress_regime

# 15. Historical gap
python -m src.cost_model.run_gap_events --panel data/processed/panel_1m_wide.parquet

# 16. Live gap
python -m src.cost_model.run_gap_events --quotes data/processed/live_quotes_long.parquet --config config/config.yaml

# 17. Backtest A
python -m src.backtest.run_backtest_a

# 18. Backtest B
python -m src.backtest.run_backtest_b

# 19. Tổng hợp T5
python -m src.backtest.make_t5

# T5b KHÔNG được chạy lại vì final test đã được sử dụng và khóa.

# 20. Gap theo regime
python -m src.backtest.make_t6b

# 21. F1 và F2
python -m notebooks.tv2.plot_c2_05

# 22. F3
python scripts/make_figure_f3.py

# 23. F4 và F5
python -m src.backtest.make_figures

# 24. F6
python scripts/make_figure_f6.py

# 25. F7
python scripts/make_figure_f7.py

# 26. Đối chiếu ledger
python -m src.backtest.reconcile

# 27. Đặc điểm venue
python -m scripts.exchange_characteristics --ohlcv data/interim/ohlcv_1m_long.parquet --live data/processed/live_quotes_long.parquet --funding data/processed/funding_oi.parquet --manifest data/processed/split_manifest.json --fees config/fees.yaml --out outputs/tables/T1_exchange_characteristics.csv --sample dev_val
```

Nếu môi trường không nhận `python`, thay toàn bộ tiền tố `python` trong chuỗi lệnh trên bằng `py` trên Windows hoặc `python3` trên macOS/Linux.

T5b không thuộc chuỗi chạy lại vì final test đã được sử dụng một lần và khóa theo thiết kế nghiên cứu.

---

## 9. Các bảng kết quả chính

Các bảng nghiên cứu được lưu tại:

```text
outputs/tables/
```

Các đầu ra chính gồm:

```text
T0_D1_quality.csv
T0_D1_volume_flags.csv
T0_alignment_quality.csv
T0_data_quality.csv
T0b_live_coverage.csv
T1_exchange_characteristics.csv

T2_*.csv
T3_*.csv
T3b_sensitivity.csv
T3c_spot_perp.csv
T3d_ils.csv
T3d_ils_summary.csv
rolling_is.parquet

T4_leadership_determinants.csv
T6_stress.csv

gap_events_historical.parquet
gap_events.parquet

backtest_a_dev_L{latency}_ledger.parquet
backtest_a_dev_L{latency}_summary.csv
backtest_a_dev_L{latency}_params.json
half_spread_calibration.csv

trades_ledger.parquet
T5_arbitrage_net.csv
T5b_cost_sensitivity.csv
T6b_gap_by_regime.csv
```

Tên file cụ thể có thể phụ thuộc vào bước phân tích và tham số được sử dụng, nhưng toàn bộ kết quả nghiên cứu thật được lưu ngoài `outputs/synthetic/`.

---

## 10. Các hình chính

Các hình nghiên cứu được lưu tại:

```text
outputs/figures/
```

Các hình chính gồm:

```text
F1_synchronized_prices.png
F2_price_dispersion.png
F3_rolling_is_bounds.png
F4_*.png
F5_*.png
F6_leadlag_xcorr.png
F7_spot_perp_is_ils.png
```

Trong đó:

- **F1**: giá BTC/USDT đã đồng bộ giữa các venue;
- **F2**: độ phân tán giá giữa các sàn;
- **F3**: rolling Information Share và khoảng cận;
- **F4**: thời gian tồn tại của gap;
- **F5**: P&L tích lũy của backtest;
- **F6**: tương quan lead–lag;
- **F7**: so sánh spot và perpetual bằng IS và ILS.

---

## 11. Kiểm chứng Information Share và ILS

Các hàm Information Share và Information Leadership Share được kiểm chứng bằng dữ liệu giả lập có venue dẫn giá được xác định trước.

Hai trường hợp chính gồm:

```text
multi
spot_perp
```

Trong trường hợp `multi`, `binance_spot` được thiết lập là venue dẫn.

Trong trường hợp `spot_perp`, `binance_perp` được thiết lập là venue dẫn.

Có thể chạy kiểm chứng chuyên biệt bằng:

```bash
python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q
```

Tùy chọn `--require-tv5` yêu cầu test thất bại nếu thiếu các hàm price discovery cần thiết thay vì bỏ qua kiểm tra.

IS, CS và ILS được sử dụng đồng thời vì các thước đo phản ánh những khía cạnh khác nhau của quá trình price discovery. Không kết luận vai trò dẫn giá chỉ dựa trên một chỉ số duy nhất.

---

## 12. Dashboard

Dashboard của dự án được xây dựng bằng Streamlit tại:

```text
dashboard/app.py
```

Sau khi các bước nghiên cứu đã tạo đầy đủ đầu ra trong `outputs/`, khởi động dashboard từ thư mục gốc của repository bằng:

```bash
python -m streamlit run dashboard/app.py
```

Dashboard sử dụng các đầu ra của pipeline để trình bày:

- báo giá và trạng thái dữ liệu;
- độ phân tán giá;
- rolling Information Share;
- venue dẫn giá;
- gap giữa các sàn;
- kết quả arbitrage sau chi phí.

Dashboard là lớp trực quan hóa các kết quả đã được pipeline tạo ra, không thay thế các bước phân tích nghiên cứu.

---

## 13. Lưu ý về khả năng tái lập

### 13.1. Dữ liệu lịch sử

D1 sử dụng trong nghiên cứu là phiên bản đã được kiểm tra và điều chỉnh đơn vị `volume_base` của OKX spot từ USDT sang BTC.

Khi tái lập kết quả báo cáo, cần sử dụng đúng phiên bản dữ liệu đã bàn giao thay vì tải lại dữ liệu lịch sử và giả định hai bộ dữ liệu hoàn toàn giống nhau.

Các bảng và hình phụ thuộc vào D1 cần được tạo từ cùng phiên bản dữ liệu để đảm bảo tính nhất quán.

### 13.2. Dữ liệu live

D2 được thu trong **13 phiên live** và phụ thuộc vào thời điểm thu thập thực tế.

Dữ liệu bid/ask, spread và market depth không thể được tái tạo chính xác bằng cách chạy collector ở một thời điểm khác.

Vì vậy, tái lập kết quả live phải sử dụng dữ liệu đã lưu trong nghiên cứu. Báo cáo T0b có thể được tạo lại từ raw live bằng chế độ `coverage`; không cần thu lại dữ liệu thị trường.

`live_session_is.parquet` được tạo từ dữ liệu live đã lưu bằng `src.price_discovery.run_live_is` và được sử dụng làm đầu vào cho mô hình T4 live.

### 13.3. Funding rate và Open Interest

Funding rate và Open Interest phụ thuộc vào giới hạn lịch sử và khả năng cung cấp dữ liệu của từng sàn.

Việc tải lại tại thời điểm khác có thể tạo ra khoảng dữ liệu khác với bộ dữ liệu được sử dụng trong nghiên cứu.

### 13.4. Final test

Final test đã được sử dụng một lần cho T5b.

Repository hiện tại giữ trạng thái final test đã consumed và không tự mở lại tập này.

Không:

```text
xóa .test.lock
chỉnh test_consumed về false
chạy lại T5b trên final test
```

chỉ để tái tạo lại một file output đã tồn tại.

### 13.5. Historical và live không hoàn toàn tương đương

Dữ liệu lịch sử có tần suất 1 phút, trong khi dữ liệu live sử dụng bid/ask thực tế với độ phân giải cao hơn.

Do đó:

- gap duration của historical và live không có cùng độ phân giải;
- historical backtest sử dụng proxy từ dữ liệu nến;
- live backtest sử dụng báo giá thực tế;
- spread và market depth lịch sử không được quan sát trực tiếp.

Các kết quả giữa hai nguồn dữ liệu cần được diễn giải theo đúng thiết kế này.

---

## 14. Quy ước chính

- Biến, hàm và tên cột sử dụng `snake_case`.
- Lớp sử dụng `PascalCase`.
- Hằng số sử dụng `UPPER_SNAKE`.
- Cột theo venue có dạng `{chỉ_số}__{venue}`.
- Phí và chênh lệch giá được biểu diễn bằng **bps**.
- Giá được biểu diễn bằng **USDT**.
- Khối lượng cơ sở được biểu diễn bằng **BTC**.
- Thời gian sử dụng **UTC**.
- Giá trị thiếu được giữ dưới dạng `NaN`; dữ liệu không được tự động nội suy nếu thiết kế không cho phép.
- Các cặp arbitrage ghi venue mua trước và venue bán sau.
- Kết quả synthetic được lưu riêng dưới `outputs/synthetic/`.
- Development và validation được sử dụng cho phát triển và kiểm tra độ ổn định của mô hình.
- Final test không được sử dụng để điều chỉnh mô hình sau khi đã mở.

---

## 15. Luồng nghiên cứu

Luồng xử lý chính của dự án có thể tóm tắt như sau:

```text
Historical OHLCV
      │
      ├──► T0 – Data quality
      │
      ▼
D1 – Historical data
      │
      ▼
Synchronization
      │
      ▼
D3 – panel_1m_wide
      │
      ├──────────────► D3b – features_1m
      │                        │
      │                        ▼
      │                  regime_flags
      │
      ▼
Price Discovery
      │
      ├── T2
      ├── T3 / T3b / T3c / T3d
      ├── rolling_is
      │       │
      │       ├──► T4 – Liquidity regression (historical)
      │       ├──► T6 – Stress/regime analysis
      │       ├──► F3 – Rolling IS
      │       └──► F7 – Spot vs perpetual
      │
      ├──► F1 / F2
      └──► F6 – Lead–lag


Raw live quotes
      │
      ├──► T0b – Live coverage
      │
      ▼
D2 – live_quotes_long
      │
      ├──► Session-level Information Share
      │          │
      │          ▼
      │    live_session_is.parquet
      │          │
      │          └──► T4 – Liquidity regression (live)
      │
      ├──────────────► Half-spread calibration
      │                         │
      │                         ▼
Historical panel ─────────► Backtest A
      │
      └──► Historical gap ──────────────┐
                                        │
D2 – Live quotes ─► Live gap ─► Backtest B
                                        │
                                        ├──► T5 / T5b / T5c
                                        │
regime_flags ───────────────────────────└──► T6b

Historical + live gap ──► F4
Backtest results ───────► F5
```

---

## 16. Kết quả và phạm vi sử dụng

Dự án được xây dựng cho mục đích nghiên cứu về:

- price discovery trên thị trường crypto phân mảnh;
- sự thay đổi vai trò dẫn giá theo thời gian;
- mối liên hệ giữa vai trò dẫn giá và thanh khoản;
- khác biệt giữa spot và perpetual;
- thời gian tồn tại của chênh lệch giá;
- khả năng thực hiện cross-exchange arbitrage sau chi phí.

Các kết quả backtest là kết quả nghiên cứu trên dữ liệu và giả định của mô hình, không phải khuyến nghị đầu tư hay cam kết về khả năng sinh lời trong giao dịch thực tế.
