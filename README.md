# Crypto Price Discovery & Cross-Exchange Arbitrage

Dự án nghiên cứu quá trình **price discovery** của Bitcoin trên nhiều sàn giao dịch và đánh giá khả năng **cross-exchange arbitrage** sau khi tính đến chi phí giao dịch. Phân tích tập trung vào **BTC/USDT** trên Binance, Bybit và OKX, bao gồm cả thị trường **spot** và **perpetual**.

Dự án kết hợp dữ liệu lịch sử tần suất **1 phút** với dữ liệu **bid/ask live** để thực hiện kiểm định đồng liên kết, ước lượng VECM, Information Share (IS), Component Share (CS), Information Leadership Share (ILS), phân tích rolling, đánh giá mối liên hệ giữa vai trò dẫn giá với thanh khoản và trạng thái thị trường, phân tích chênh lệch giá giữa các sàn và backtest arbitrage sau chi phí.

---

## 1. Chạy nhanh dự án

Phần này dành cho người dùng muốn **khởi chạy trực tiếp dashboard để xem kết quả của dự án** mà không cần chạy lại toàn bộ pipeline nghiên cứu.

### 1.1. Tải mã nguồn

Tải repository dưới dạng **ZIP** và giải nén.

Sau đó mở PowerShell hoặc Terminal tại **thư mục gốc của dự án**, tức thư mục chứa:

```text
run_all.py
requirements.txt
config/
data/
outputs/
dashboard/
```

Dự án sử dụng **Python 3.12**.

Kiểm tra phiên bản Python:

**Windows:**

```powershell
py --version
```

**macOS/Linux:**

```bash
python3 --version
```

---

### 1.2. Windows – PowerShell

Tạo virtual environment:

```powershell
py -m venv tmp\venv-req
```

Cài các thư viện cần thiết:

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m pip install -r requirements.txt
```

Khởi chạy dashboard:

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m streamlit run dashboard/app.py
```

**Cách thay thế:** Nếu đã hoàn tất các bước trên nhưng không khởi chạy được dashboard bằng lệnh trực tiếp, kích hoạt virtual environment:

```powershell
.\tmp\venv-req\Scripts\Activate.ps1
```

Sau đó chạy:

```powershell
streamlit run dashboard/app.py
```

---

### 1.3. macOS/Linux

Tạo virtual environment:

```bash
python3 -m venv tmp/venv-req
```

Cài các thư viện cần thiết:

```bash
./tmp/venv-req/bin/python -m pip install -r requirements.txt
```

Khởi chạy dashboard:

```bash
./tmp/venv-req/bin/python -m streamlit run dashboard/app.py
```

**Cách thay thế:** Kích hoạt virtual environment:

```bash
source tmp/venv-req/bin/activate
```

Sau đó chạy:

```bash
streamlit run dashboard/app.py
```

---

### 1.4. Mở dashboard

Sau khi Streamlit khởi động thành công, terminal sẽ hiển thị địa chỉ local, thông thường:

```text
Local URL: http://localhost:8501
```

Mở địa chỉ này trên trình duyệt để sử dụng dashboard.

Repository đã chứa các dữ liệu và kết quả cần thiết trong `data/` và `outputs/` để phục vụ việc trình bày dashboard.

**Nếu mục đích chỉ là xem và đánh giá sản phẩm, không cần chạy lại toàn bộ pipeline nghiên cứu.**

Nếu muốn tái lập quá trình xử lý dữ liệu và tạo lại kết quả nghiên cứu, xem **Mục 5 – Tái lập kết quả nghiên cứu**.

---

## 2. Cấu trúc repository

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

- `run_all.py`: điều phối các bước chính của pipeline nghiên cứu.
- `config/`: lưu cấu hình sàn, phí giao dịch, sự kiện, phiên live và các tham số nghiên cứu.
- `docs/`: tài liệu về cấu trúc dữ liệu, nguồn dữ liệu và quy ước của dự án.
- `data/`: chứa dữ liệu thô, dữ liệu trung gian, dữ liệu đã xử lý và dữ liệu giả lập.
- `src/`: mã nguồn chính cho thu thập, đồng bộ, price discovery, mô hình chi phí, backtest và evaluation.
- `scripts/`: các script hỗ trợ xử lý và tạo kết quả.
- `notebooks/`: các script phân tích và trực quan hóa bổ sung.
- `dashboard/`: ứng dụng Streamlit dùng để trực quan hóa kết quả.
- `tests/`: các kiểm thử của dự án.
- `outputs/`: chứa các bảng, hình và kết quả được tạo từ pipeline.

---

## 3. Môi trường và dữ liệu nghiên cứu

### 3.1. Môi trường

Dự án sử dụng:

```text
Python 3.12
```

Các dependencies được khai báo trong:

```text
requirements.txt
```

Seed sử dụng cho các bước có yếu tố ngẫu nhiên là:

```text
20261017
```

và được đọc từ `config/config.yaml`.

Virtual environment được tạo cục bộ tại:

```text
tmp/venv-req
```

### 3.2. Dữ liệu nghiên cứu

Nghiên cứu sử dụng ba nhóm dữ liệu chính:

| Dữ liệu | Đường dẫn |
| --- | --- |
| D1 – OHLCV 1 phút của 6 venue | `data/interim/ohlcv_1m_long.parquet` |
| D2 – Báo giá bid/ask live | `data/processed/live_quotes_long.parquet` |
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

Các file OHLCV nguồn theo từng venue được đặt tại:

```text
data/raw/ohlcv/
```

Nếu bắt đầu từ repository mới giải nén, D1 có thể được tạo từ các file OHLCV nguồn bằng `run_all.py --mode raw`.

Một số file dữ liệu trung gian và đã xử lý quan trọng gồm:

```text
data/processed/panel_1m_wide.parquet
data/processed/split_manifest.json
data/processed/features_1m.parquet
data/processed/regime_flags.parquet
data/processed/live_session_is.parquet
```

Để tái lập chính xác kết quả trong báo cáo, cần sử dụng đúng phiên bản dữ liệu nghiên cứu đã được bàn giao. Không nên thay thế bằng dữ liệu tải lại tại thời điểm khác vì coverage và dữ liệu live có thể thay đổi.

---

## 4. Kiểm tra mã nguồn

Sau khi cài đặt môi trường, có thể chạy toàn bộ test để kiểm tra mã nguồn.

**Windows:**

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m pytest -q
```

**macOS/Linux:**

```bash
./tmp/venv-req/bin/python -m pytest -q
```

Các test kiểm tra những thành phần chính của dự án như đồng bộ thời gian, schema dữ liệu, chia mẫu, Information Share và ILS, mô hình chi phí, backtest và pipeline.

---

## 5. Tái lập kết quả nghiên cứu

> Nếu mục đích chỉ là **mở dashboard để xem và đánh giá sản phẩm**, không cần thực hiện phần này.

Pipeline nghiên cứu được điều phối thông qua `run_all.py`.

### 5.1. Chạy pipeline từ dữ liệu gốc

Các file OHLCV lịch sử của 6 venue được lưu tại:

```text
data/raw/ohlcv/
```

Để xử lý dữ liệu và chạy pipeline từ các file này:

```bash
python run_all.py --mode raw --config config/config.yaml
```

Lệnh trên sẽ tạo dữ liệu trung gian cần thiết và thực hiện các stage nghiên cứu được cấu hình trong `config/config.yaml`.

### 5.2. Chạy lại từ dữ liệu đã chuẩn bị

Nếu file D1 đã tồn tại tại:

```text
data/interim/ohlcv_1m_long.parquet
```

có thể chạy lại historical pipeline bằng:

```bash
python run_all.py --mode historical --config config/config.yaml
```

Nếu chưa kích hoạt virtual environment, có thể chạy bằng Python trong môi trường đã tạo.

**Windows:**

```powershell
& ".\tmp\venv-req\Scripts\python.exe" run_all.py --mode historical --config config/config.yaml
```

**macOS/Linux:**

```bash
./tmp/venv-req/bin/python run_all.py --mode historical --config config/config.yaml
```

### 5.3. Kết quả đầu ra

Sau khi pipeline hoàn tất:

```text
outputs/tables/
```

chứa các bảng kết quả và:

```text
outputs/figures/
```

chứa các hình trực quan.

Chi tiết về từng nhóm kết quả được trình bày tại **Mục 6 – Đọc kết quả**.

### 5.4. Lưu ý về final test

Final test đã được sử dụng một lần cho phân tích T5b và được khóa theo thiết kế nghiên cứu. Pipeline thông thường **không tự động chạy lại final test**.

Kết quả T5b đã được lưu tại:

```text
outputs/tables/T5b_cost_sensitivity.csv
```

Không xóa test lock hoặc thay đổi trạng thái `test_consumed` chỉ để tạo lại kết quả đã tồn tại.

---

## 6. Đọc kết quả

Kết quả nghiên cứu được lưu chủ yếu trong hai thư mục:

```text
outputs/tables/
outputs/figures/
```

### 6.1. Các bảng kết quả

| Nhóm kết quả | File chính | Nội dung |
| --- | --- | --- |
| Chất lượng dữ liệu | `T0_D1_quality.csv`, `T0_D1_volume_flags.csv`, `T0_alignment_quality.csv`, `T0_data_quality.csv` | Kiểm tra chất lượng, tính đầy đủ và đồng bộ của dữ liệu lịch sử |
| Coverage dữ liệu live | `T0b_live_coverage.csv` | Kiểm tra coverage của các phiên thu thập dữ liệu live |
| Đặc điểm venue | `T1_exchange_characteristics.csv` | Tổng hợp đặc điểm dữ liệu, phí và thông tin thị trường của các venue |
| Kiểm định và price discovery | `T2_*.csv`, `T3_*.csv` | Kết quả kiểm định, VECM, Information Share và Component Share |
| Robustness | `T3b_sensitivity.csv` | Kiểm tra độ nhạy của kết quả price discovery |
| Spot và perpetual | `T3c_spot_perp.csv` | So sánh vai trò price discovery giữa thị trường spot và perpetual |
| ILS | `T3d_ils.csv`, `T3d_ils_summary.csv` | Information Leadership Share và kết quả tổng hợp |
| Rolling analysis | `rolling_is.parquet` | Information Share theo thời gian |
| Thanh khoản | `T4_leadership_determinants.csv` | Mối liên hệ giữa thanh khoản và vai trò dẫn giá |
| Market regime | `T6_stress.csv` | Price discovery trong các trạng thái thị trường |
| Historical price gap | `gap_events_historical.parquet` | Các sự kiện chênh lệch giá từ dữ liệu lịch sử 1 phút |
| Live price gap | `gap_events.parquet` | Các sự kiện chênh lệch giá từ dữ liệu bid/ask live |
| Backtest A | `backtest_a_dev_L{latency}_ledger.parquet`, `backtest_a_dev_L{latency}_summary.csv`, `backtest_a_dev_L{latency}_params.json` | Giao dịch, kết quả và tham số của historical backtest |
| Hiệu chỉnh spread | `half_spread_calibration.csv` | Tham số half-spread sử dụng trong mô hình chi phí |
| Backtest live | `trades_ledger.parquet` | Ledger giao dịch từ backtest trên dữ liệu live |
| Arbitrage sau chi phí | `T5_arbitrage_net.csv` | Tổng hợp kết quả arbitrage sau chi phí giao dịch |
| Final test | `T5b_cost_sensitivity.csv` | Phân tích độ nhạy chi phí trên final test đã khóa |
| Gap theo regime | `T6b_gap_by_regime.csv` | Đặc điểm price gap theo các trạng thái thị trường |

### 6.2. Các hình kết quả

Các hình được lưu tại:

```text
outputs/figures/
```

| Hình | Nội dung |
| --- | --- |
| `F1_synchronized_prices.png` | Giá BTC/USDT sau khi đồng bộ giữa các venue |
| `F2_price_dispersion.png` | Mức độ phân tán giá giữa các venue |
| `F3_rolling_is_bounds.png` | Sự thay đổi của Information Share theo thời gian |
| `F4_*.png` | Phân bố thời gian tồn tại của price gap |
| `F5_*.png` | Kết quả P&L của backtest arbitrage |
| `F6_leadlag_xcorr.png` | Quan hệ lead–lag giữa các venue |
| `F7_spot_perp_is_ils.png` | So sánh price discovery giữa spot và perpetual |

### 6.3. Xem kết quả trên dashboard

Để xem các kết quả chính theo cách trực quan hơn, khởi chạy **Streamlit Dashboard** theo hướng dẫn tại **Mục 1**.

Dashboard tổng hợp dữ liệu và outputs của dự án để hỗ trợ theo dõi price discovery, price gap và kết quả arbitrage mà không cần mở riêng từng file kết quả.

---

## 7. Kiểm chứng Information Share và ILS

Các hàm Information Share và Information Leadership Share được kiểm chứng bằng dữ liệu giả lập có venue dẫn giá được xác định trước:

```bash
python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q
```

IS, CS và ILS phản ánh các khía cạnh khác nhau của quá trình price discovery. Vì vậy, vai trò dẫn giá được đánh giá dựa trên nhiều thước đo thay vì chỉ dựa vào một chỉ số duy nhất.

---

## 8. Dashboard

Dashboard được xây dựng bằng Streamlit tại:

```text
dashboard/app.py
```

Dashboard sử dụng dữ liệu và outputs của dự án để trực quan hóa các kết quả chính, bao gồm:

- báo giá và trạng thái dữ liệu;
- độ phân tán giá giữa các venue;
- rolling Information Share;
- vai trò dẫn giá;
- price gap giữa các sàn;
- kết quả arbitrage sau chi phí.

Dashboard là lớp trực quan hóa các kết quả của pipeline và không thay thế các bước phân tích nghiên cứu.

Để khởi chạy dashboard, sử dụng hướng dẫn tại **Mục 1 – Chạy nhanh dự án**.

---

## 9. Lưu ý về khả năng tái lập

### 9.1. Dữ liệu lịch sử

D1 sử dụng trong nghiên cứu là phiên bản dữ liệu đã được kiểm tra và chuẩn hóa trước khi thực hiện các phân tích tiếp theo.

Khi tái lập kết quả báo cáo, cần sử dụng đúng phiên bản dữ liệu đã bàn giao.

### 9.2. Dữ liệu live

D2 được thu trong **13 phiên live** và phụ thuộc vào thời điểm thu thập thực tế.

Dữ liệu bid/ask, spread và các thông tin thị trường live không thể được tái tạo chính xác bằng cách chạy collector tại một thời điểm khác. Vì vậy, tái lập kết quả live phải sử dụng dữ liệu đã lưu trong nghiên cứu.

### 9.3. Funding rate và Open Interest

Funding rate và Open Interest phụ thuộc vào giới hạn lịch sử và khả năng cung cấp dữ liệu của từng sàn. Việc tải lại tại thời điểm khác có thể tạo ra khoảng dữ liệu khác với bộ dữ liệu được sử dụng trong nghiên cứu.

### 9.4. Final test

Final test đã được sử dụng một lần cho T5b.

Không xóa test lock, không chỉnh `test_consumed` về `false` và không chạy lại T5b chỉ để tái tạo output đã tồn tại.

### 9.5. Historical và live không hoàn toàn tương đương

Dữ liệu lịch sử có tần suất 1 phút, trong khi dữ liệu live sử dụng bid/ask thực tế với độ phân giải cao hơn.

Do đó:

- gap duration của historical và live không có cùng độ phân giải;
- historical backtest sử dụng proxy từ dữ liệu nến;
- live backtest sử dụng báo giá thực tế;
- spread lịch sử không được quan sát trực tiếp như dữ liệu live.

Các kết quả giữa hai nguồn dữ liệu cần được diễn giải theo đúng thiết kế này.

---

## 10. Quy ước chính

- Biến, hàm và tên cột sử dụng `snake_case`.
- Lớp sử dụng `PascalCase`.
- Hằng số sử dụng `UPPER_SNAKE`.
- Cột theo venue có dạng `{chỉ_số}__{venue}`.
- Phí và chênh lệch giá được biểu diễn bằng **bps**.
- Giá được biểu diễn bằng **USDT**.
- Khối lượng cơ sở được biểu diễn bằng **BTC**.
- Thời gian sử dụng **UTC**.
- Giá trị thiếu được giữ dưới dạng `NaN`.
- Các cặp arbitrage ghi venue mua trước và venue bán sau.
- Kết quả synthetic được lưu riêng dưới `outputs/synthetic/`.
- Development và validation được sử dụng cho phát triển và kiểm tra độ ổn định của mô hình.
- Final test không được sử dụng để điều chỉnh mô hình sau khi đã mở.

---

## 11. Luồng nghiên cứu

```text
Historical OHLCV
      │
      ├──► Data quality
      ▼
Historical data
      │
      ▼
Synchronization
      │
      ▼
Synchronized panel
      │
      ├──► Features
      │       │
      │       └──► Market regimes
      │
      ▼
Price Discovery
      │
      ├──► Cointegration / VECM
      ├──► IS / CS / ILS
      ├──► Rolling analysis
      ├──► Liquidity analysis
      └──► Spot vs Perpetual

Historical panel
      │
      └──► Historical price gaps
                    │
                    ▼
              Arbitrage analysis

Live bid/ask data
      │
      ├──► Live price discovery
      ├──► Live price gaps
      └──► Live arbitrage

Research outputs
      │
      ├──► Tables
      ├──► Figures
      └──► Streamlit Dashboard
```

---

## 12. Phạm vi sử dụng

Dự án được xây dựng cho mục đích nghiên cứu về:

- price discovery trên thị trường crypto phân mảnh;
- sự thay đổi vai trò dẫn giá theo thời gian;
- mối liên hệ giữa vai trò dẫn giá và thanh khoản;
- khác biệt giữa spot và perpetual;
- thời gian tồn tại của chênh lệch giá;
- khả năng thực hiện cross-exchange arbitrage sau chi phí.

Các kết quả backtest là kết quả nghiên cứu trên dữ liệu và giả định của mô hình, **không phải khuyến nghị đầu tư hoặc cam kết về khả năng sinh lời trong giao dịch thực tế**.
