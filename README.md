# Crypto Price Discovery & Cross-Exchange Arbitrage

Dự án nghiên cứu quá trình **price discovery** của Bitcoin trên nhiều sàn giao dịch và đánh giá khả năng **cross-exchange arbitrage** sau khi tính đến chi phí giao dịch. Phân tích tập trung vào **BTC/USDT** trên Binance, Bybit và OKX, bao gồm cả thị trường **spot** và **perpetual**.

Dự án kết hợp dữ liệu lịch sử tần suất **1 phút** với dữ liệu **bid/ask live** để thực hiện kiểm định đồng liên kết, ước lượng VECM, Information Share (IS), Component Share (CS), Information Leadership Share (ILS), phân tích rolling, đánh giá mối liên hệ giữa vai trò dẫn giá với thanh khoản và trạng thái thị trường, phân tích gap giữa các sàn và backtest arbitrage sau chi phí.

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

> Nếu file ZIP sau khi giải nén tạo thêm một thư mục lồng bên trong, cần chuyển vào đúng thư mục chứa `run_all.py` trước khi chạy các lệnh bên dưới.

Dự án sử dụng **Python 3.12**.

Có thể kiểm tra Python bằng:

**Windows:**

```powershell
py --version
```

**macOS / Linux:**

```bash
python3 --version
```

---

### 1.2. Windows – PowerShell

Tạo virtual environment:

```powershell
py -m venv tmp\venv-req
```

Cài toàn bộ thư viện:

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m pip install -r requirements.txt
```

Khởi chạy dashboard:

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m streamlit run dashboard/app.py
```

---

### 1.3. macOS / Linux

Tạo virtual environment:

```bash
python3 -m venv tmp/venv-req
```

Cài toàn bộ thư viện:

```bash
./tmp/venv-req/bin/python -m pip install -r requirements.txt
```

Khởi chạy dashboard:

```bash
./tmp/venv-req/bin/python -m streamlit run dashboard/app.py
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

Nếu muốn tái lập quá trình xử lý dữ liệu và tạo lại các kết quả nghiên cứu, xem **Mục 5 – Tái lập kết quả nghiên cứu**.

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

- `run_all.py`: điều phối các bước chính của pipeline.
- `config/`: lưu cấu hình sàn, phí giao dịch, sự kiện, phiên live và các tham số nghiên cứu.
- `docs/`: tài liệu về cấu trúc dữ liệu, nguồn dữ liệu và quy ước của dự án.
- `data/`: chứa dữ liệu thô, dữ liệu trung gian, dữ liệu đã xử lý và dữ liệu giả lập.
- `src/`: mã nguồn chính cho thu thập, đồng bộ, price discovery, mô hình chi phí, backtest và evaluation.
- `scripts/`: các script hỗ trợ tạo đặc trưng, bảng và hình.
- `notebooks/`: các script phân tích và trực quan hóa bổ sung.
- `dashboard/`: ứng dụng Streamlit dùng để trực quan hóa kết quả.
- `tests/`: các kiểm thử của dự án.
- `outputs/`: chứa bảng, hình và các kết quả được tạo từ pipeline.

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

README sử dụng trực tiếp Python executable bên trong virtual environment thay vì yêu cầu activate môi trường. Cách này giúp hạn chế khác biệt giữa PowerShell, Command Prompt, macOS và Linux.

---

### 3.2. Dữ liệu nghiên cứu

Nghiên cứu sử dụng ba nhóm dữ liệu chính:

| Dữ liệu | Đường dẫn |
| --- | --- |
| D1 – OHLCV 1 phút của 6 venue | `data/interim/ohlcv_1m_long.parquet` |
| D2 – Báo giá bid/ask live của 13 phiên | `data/processed/live_quotes_long.parquet` |
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

Các file trung gian quan trọng gồm:

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

Sau khi cài đặt môi trường, có thể chạy toàn bộ test.

### Windows

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m pytest -q
```

### macOS / Linux

```bash
./tmp/venv-req/bin/python -m pytest -q
```

Các test kiểm tra những thành phần chính như đồng bộ thời gian, schema dữ liệu, chia mẫu, Information Share và ILS, mô hình chi phí, ledger backtest, pipeline và các hàm tạo bảng/hình.

---

## 5. Tái lập kết quả nghiên cứu

> Nếu mục đích chỉ là **mở dashboard để xem và đánh giá sản phẩm**, không cần thực hiện phần này.

Các bước dưới đây dành cho việc kiểm tra hoặc tái lập pipeline nghiên cứu.

### 5.1. Chạy toàn bộ pipeline

Nếu bắt đầu từ các file OHLCV nguồn trong:

```text
data/raw/ohlcv/
```

chạy:

```bash
python run_all.py --mode raw --config config/config.yaml
```

`raw` mode sẽ chuẩn hóa dữ liệu OHLCV nguồn, tạo D1 và thực hiện các stage được đăng ký trong pipeline.

Nếu D1 đã được chuẩn bị tại:

```text
data/interim/ohlcv_1m_long.parquet
```

có thể chạy toàn bộ historical pipeline bằng:

```bash
python run_all.py --mode historical --config config/config.yaml
```

Pipeline thực hiện các stage nghiên cứu được cấu hình trong `config/config.yaml` và **không tự động mở hoặc chạy lại final test**.

Trên Windows, nếu sử dụng virtual environment được tạo ở Mục 1, có thể thay `python` bằng:

```text
& ".\tmp\venv-req\Scripts\python.exe"
```

Trên macOS/Linux:

```text
./tmp/venv-req/bin/python
```

---

### 5.2. Kiểm chứng trên dữ liệu giả lập

```bash
python run_all.py --mode synthetic --verify
```

Trường hợp spot–perpetual:

```bash
python run_all.py --mode synthetic --fixture-case spot_perp
```

Pipeline giả lập nhiều venue:

```bash
python run_all.py --mode synthetic --config config/pipeline.synthetic.example.yaml --fixture-case multi --data-dir outputs/synthetic/integrated/multi/data --output-dir outputs/synthetic/integrated/multi
```

Kết quả synthetic được lưu riêng tại:

```text
outputs/synthetic/
```

---

### 5.3. Kiểm tra chất lượng dữ liệu

OHLCV lịch sử:

```bash
python src/features/ohlcv_quality.py
```

Đầu ra:

```text
outputs/tables/T0_data_quality.csv
```

Coverage dữ liệu live:

```bash
python src/collectors/live/collect_live.py coverage --csv-report outputs/tables/T0b_live_coverage.csv
```

Đầu ra:

```text
outputs/tables/T0b_live_coverage.csv
```

Không cần thu thập lại dữ liệu live để tái lập T0b; sử dụng dữ liệu live đã được lưu trong nghiên cứu.

---

### 5.4. Historical foundation và D3/D4 handoff

Nếu chỉ muốn chạy historical foundation:

```bash
python run_all.py --mode historical --config config/config.yaml --foundation-only
```

Kiểm tra dữ liệu bàn giao:

```bash
python -m src.utils.check_handoff --input data/processed/panel_1m_wide.parquet --schema D3 --expected-kind historical --config config/config.yaml --manifest data/processed/split_manifest.json --report outputs/D3_D4_handoff.json
```

Thiết kế chia mẫu:

```text
Development : 60%
Validation  : 20%
Final test  : 20%
```

Final test được giữ riêng và không được sử dụng trong quá trình lựa chọn mô hình.

---

### 5.5. Tạo đặc trưng và market regime

Tạo đặc trưng 1 phút:

```bash
python scripts/build_features_1m.py
```

Xác định market regime:

```bash
python -m src.features.regime_flags
```

Đầu ra chính:

```text
data/processed/features_1m.parquet
data/processed/regime_flags.parquet
```

---

### 5.6. Price discovery

```bash
python -m src.price_discovery.run_price_discovery --data-dir data/processed --output-dir outputs --config config/config.yaml --workers 8
```

Các đầu ra chính:

```text
outputs/tables/T2_*.csv
outputs/tables/T3_*.csv
outputs/tables/T3b_sensitivity.csv
outputs/tables/T3c_spot_perp.csv
outputs/tables/T3d_ils.csv
outputs/tables/T3d_ils_summary.csv
outputs/tables/rolling_is.parquet
```

Các phân tích sử dụng development và validation theo thiết kế nghiên cứu, không sử dụng final test để lựa chọn mô hình.

---

### 5.7. Hồi quy thanh khoản và vai trò dẫn giá

Historical:

```bash
python notebooks/tv3/liquidity_regression.py
```

Đầu ra:

```text
outputs/tables/T4_leadership_determinants.csv
```

Đối với dữ liệu live, trước tiên tạo Information Share theo phiên:

```bash
python -m src.price_discovery.run_live_is
```

Sau đó chạy:

```bash
python notebooks/tv3/liquidity_regression.py --live-is data/processed/live_session_is.parquet
```

Do số phiên live có giới hạn, kết quả phần này được xem là bằng chứng thăm dò.

---

### 5.8. Information Share theo market regime

```bash
python -m src.evaluation.stress_regime
```

Đầu ra:

```text
outputs/tables/T6_stress.csv
```

---

### 5.9. Phân tích price gap

Historical:

```bash
python -m src.cost_model.run_gap_events --panel data/processed/panel_1m_wide.parquet
```

Đầu ra:

```text
outputs/tables/gap_events_historical.parquet
```

Live:

```bash
python -m src.cost_model.run_gap_events --quotes data/processed/live_quotes_long.parquet --config config/config.yaml
```

Đầu ra:

```text
outputs/tables/gap_events.parquet
```

> Khi tái lập kết quả nghiên cứu, không chạy `run_gap_events` mà không truyền `--panel` hoặc `--quotes`.

---

### 5.10. Backtest arbitrage

Backtest A – historical:

```bash
python -m src.backtest.run_backtest_a
```

Backtest A sử dụng development split. Runner chỉ cho phép `dev` hoặc `val`; final test không được đọc trong Backtest A.

Backtest B – live:

```bash
python -m src.backtest.run_backtest_b
```

Tổng hợp T5:

```bash
python -m src.backtest.make_t5
```

Các đầu ra chính:

```text
outputs/tables/backtest_a_dev_L{latency}_ledger.parquet
outputs/tables/backtest_a_dev_L{latency}_summary.csv
outputs/tables/backtest_a_dev_L{latency}_params.json
outputs/tables/half_spread_calibration.csv
outputs/tables/trades_ledger.parquet
outputs/tables/T5_arbitrage_net.csv
```

---

### 5.11. T5b – Final test

T5b đã sử dụng final test một lần theo thiết kế nghiên cứu.

Trạng thái hiện tại:

```text
test_consumed = true
```

Kết quả đã được lưu tại:

```text
outputs/tables/T5b_cost_sensitivity.csv
```

Không:

```text
xóa file test lock
chỉnh test_consumed về false
chạy lại T5b trên final test
```

chỉ để tái tạo output đã tồn tại.

---

### 5.12. Gap theo regime

```bash
python -m src.backtest.make_t6b
```

Đầu ra:

```text
outputs/tables/T6b_gap_by_regime.csv
```

---

### 5.13. Tạo figures

F1 và F2:

```bash
python -m notebooks.tv2.plot_c2_05
```

Đầu ra:

```text
outputs/figures/F1_synchronized_prices.png
outputs/figures/F2_price_dispersion.png
```

F3 – Rolling Information Share:

```bash
python scripts/make_figure_f3.py
```

Đầu ra:

```text
outputs/figures/F3_rolling_is_bounds.png
```

F4 và F5:

```bash
python -m src.backtest.make_figures
```

F6 – Lead–lag:

```bash
python scripts/make_figure_f6.py
```

Đầu ra:

```text
outputs/figures/F6_leadlag_xcorr.png
```

F7 – Spot và perpetual:

```bash
python scripts/make_figure_f7.py
```

Đầu ra:

```text
outputs/figures/F7_spot_perp_is_ils.png
```

---

### 5.14. Đối chiếu backtest

```bash
python -m src.backtest.reconcile
```

---

### 5.15. Đặc điểm venue – T1

```bash
python -m scripts.exchange_characteristics --ohlcv data/interim/ohlcv_1m_long.parquet --live data/processed/live_quotes_long.parquet --funding data/processed/funding_oi.parquet --manifest data/processed/split_manifest.json --fees config/fees.yaml --out outputs/tables/T1_exchange_characteristics.csv --sample dev_val
```

Đầu ra:

```text
outputs/tables/T1_exchange_characteristics.csv
```

T1 được tạo từ phiên bản dữ liệu cuối cùng của nghiên cứu, bao gồm D2 đủ 13 phiên live.

---

## 6. Các bảng kết quả chính

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

---

## 7. Các hình kết quả chính

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

---

## 8. Kiểm chứng Information Share và ILS

Các hàm Information Share và Information Leadership Share được kiểm chứng bằng dữ liệu giả lập có venue dẫn giá được xác định trước:

```bash
python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q
```

IS, CS và ILS phản ánh các khía cạnh khác nhau của quá trình price discovery. Không nên kết luận vai trò dẫn giá chỉ dựa trên một chỉ số duy nhất.

---

## 9. Dashboard

Dashboard được xây dựng bằng Streamlit tại:

```text
dashboard/app.py
```

Dashboard sử dụng các dữ liệu và outputs của dự án để trực quan hóa các nội dung chính như:

- báo giá và trạng thái dữ liệu;
- độ phân tán giá giữa các venue;
- rolling Information Share;
- vai trò dẫn giá;
- price gap giữa các sàn;
- kết quả arbitrage sau chi phí.

Dashboard là lớp trực quan hóa các kết quả của pipeline và không thay thế các bước phân tích nghiên cứu.

Để khởi chạy dashboard, sử dụng hướng dẫn **Chạy nhanh dự án tại Mục 1**.

---

## 10. Xử lý lỗi thường gặp

### 10.1. `python` hoặc `py` không được nhận diện

Trên Windows, thử:

```powershell
py --version
```

Trên macOS/Linux:

```bash
python3 --version
```

Đảm bảo Python 3.12 đã được cài đặt và có thể gọi từ terminal.

---

### 10.2. `No module named streamlit`

Lỗi này thường xảy ra khi dependencies chưa được cài bằng đúng Python environment.

Windows:

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m pip install -r requirements.txt
& ".\tmp\venv-req\Scripts\python.exe" -m streamlit --version
```

macOS/Linux:

```bash
./tmp/venv-req/bin/python -m pip install -r requirements.txt
./tmp/venv-req/bin/python -m streamlit --version
```

Sau đó chạy lại dashboard.

---

### 10.3. Không tìm thấy `dashboard/app.py` hoặc `requirements.txt`

Kiểm tra terminal đang đứng tại **thư mục gốc của repository**.

Thư mục hiện tại phải chứa tối thiểu:

```text
run_all.py
requirements.txt
dashboard/
data/
outputs/
```

---

### 10.4. PowerShell chặn `Activate.ps1`

README không yêu cầu activate virtual environment.

Thay vì:

```powershell
.\tmp\venv-req\Scripts\Activate.ps1
```

hãy gọi trực tiếp:

```powershell
& ".\tmp\venv-req\Scripts\python.exe"
```

Do đó không cần thay đổi PowerShell Execution Policy chỉ để chạy dự án.

---

### 10.5. Port 8501 đang được sử dụng

Có thể chạy Streamlit trên port khác.

Windows:

```powershell
& ".\tmp\venv-req\Scripts\python.exe" -m streamlit run dashboard/app.py --server.port 8502
```

macOS/Linux:

```bash
./tmp/venv-req/bin/python -m streamlit run dashboard/app.py --server.port 8502
```

Sau đó truy cập:

```text
http://localhost:8502
```

---

### 10.6. Dashboard mở nhưng thiếu dữ liệu hoặc kết quả

Kiểm tra repository được giải nén đầy đủ và các thư mục sau vẫn tồn tại:

```text
data/
outputs/
```

Nếu mục đích là xem phiên bản kết quả đã bàn giao, không cần xóa hoặc tạo lại các file trong hai thư mục này.

Nếu muốn tái lập toàn bộ historical pipeline từ raw OHLCV, sử dụng hướng dẫn tại **Mục 5.1**.

---

## 11. Lưu ý về khả năng tái lập

### 11.1. Dữ liệu lịch sử

D1 sử dụng trong nghiên cứu là phiên bản đã được kiểm tra và điều chỉnh đơn vị `volume_base` của OKX spot từ USDT sang BTC.

Khi tái lập kết quả báo cáo, cần sử dụng đúng phiên bản dữ liệu đã bàn giao.

### 11.2. Dữ liệu live

D2 được thu trong **13 phiên live** và phụ thuộc vào thời điểm thu thập thực tế.

Dữ liệu bid/ask, spread và các thông tin thị trường live không thể được tái tạo chính xác bằng cách chạy collector tại một thời điểm khác. Vì vậy, tái lập kết quả live phải sử dụng dữ liệu đã lưu trong nghiên cứu.

### 11.3. Funding rate và Open Interest

Funding rate và Open Interest phụ thuộc vào giới hạn lịch sử và khả năng cung cấp dữ liệu của từng sàn. Việc tải lại tại thời điểm khác có thể tạo ra khoảng dữ liệu khác với bộ dữ liệu được sử dụng trong nghiên cứu.

### 11.4. Final test

Final test đã được sử dụng một lần cho T5b.

Không xóa file lock, không chỉnh `test_consumed` về `false` và không chạy lại T5b chỉ để tái tạo output đã tồn tại.

### 11.5. Historical và live không hoàn toàn tương đương

Dữ liệu lịch sử có tần suất 1 phút, trong khi dữ liệu live sử dụng bid/ask thực tế với độ phân giải cao hơn.

Do đó:

- gap duration của historical và live không có cùng độ phân giải;
- historical backtest sử dụng proxy từ dữ liệu nến;
- live backtest sử dụng báo giá thực tế;
- spread lịch sử không được quan sát trực tiếp như dữ liệu live.

Các kết quả giữa hai nguồn dữ liệu cần được diễn giải theo đúng thiết kế này.

---

## 12. Quy ước chính

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

## 13. Luồng nghiên cứu

```text
Historical OHLCV
      │
      ├──► T0 – Data quality
      ▼
D1 – Historical data
      │
      ▼
Synchronization
      │
      ▼
D3 – panel_1m_wide
      │
      ├──► features_1m
      │       │
      │       └──► regime_flags
      │
      ▼
Price Discovery
      │
      ├──► T2
      ├──► T3 / T3b / T3c / T3d
      ├──► rolling_is
      │       ├──► T4
      │       ├──► T6
      │       ├──► F3
      │       └──► F7
      ├──► F1 / F2
      └──► F6

Raw live quotes
      │
      ├──► T0b
      ▼
D2 – live_quotes_long
      │
      ├──► live_session_is ──► T4 live
      ├──► Half-spread calibration ──► Backtest A
      ├──► Live gap
      └──► Backtest B
                │
                ├──► T5 / T5b
                └──► F5

Historical gap + Live gap ──► F4
Regime flags + Gap ─────────► T6b
```

---

## 14. Phạm vi sử dụng

Dự án được xây dựng cho mục đích nghiên cứu về:

- price discovery trên thị trường crypto phân mảnh;
- sự thay đổi vai trò dẫn giá theo thời gian;
- mối liên hệ giữa vai trò dẫn giá và thanh khoản;
- khác biệt giữa spot và perpetual;
- thời gian tồn tại của chênh lệch giá;
- khả năng thực hiện cross-exchange arbitrage sau chi phí.

Các kết quả backtest là kết quả nghiên cứu trên dữ liệu và giả định của mô hình, không phải khuyến nghị đầu tư hay cam kết về khả năng sinh lời trong giao dịch thực tế.
