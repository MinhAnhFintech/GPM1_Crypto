# Crypto Price Discovery & Cross-Exchange Arbitrage

Dự án nghiên cứu quá trình **price discovery** của Bitcoin trên nhiều sàn giao dịch và đánh giá khả năng **cross-exchange arbitrage** sau khi tính đến chi phí giao dịch. Phân tích tập trung vào BTC/USDT trên Binance, Bybit và OKX, bao gồm cả thị trường **spot** và **perpetual**.

Dự án kết hợp dữ liệu lịch sử tần suất 1 phút với dữ liệu bid/ask live để thực hiện kiểm định đồng liên kết, ước lượng VECM, Information Share (IS), Component Share (CS), Information Leadership Share (ILS), phân tích rolling, gap giữa các sàn và backtest arbitrage sau chi phí.

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
│   └── utils/
│
├── scripts/
├── dashboard/
├── notebooks/
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
- `src/`: mã nguồn chính cho thu thập, đồng bộ, phân tích price discovery, mô hình chi phí và backtest.
- `scripts/`: các script hỗ trợ tạo đặc trưng, bảng và hình.
- `dashboard/`: ứng dụng Streamlit dùng để trực quan hóa kết quả.
- `tests/`: các kiểm thử của dự án.
- `outputs/`: chứa bảng, hình và các kết quả được tạo từ pipeline.

---

## 2. Yêu cầu môi trường

Dự án sử dụng:

- Python 3.12
- pandas 3.0.1
- pyarrow 26.0.0
- numpy 2.3.5
- statsmodels 0.15.0

Các thư viện cần thiết được khai báo trong `requirements.txt`.

Từ thư mục gốc của repository, cài đặt bằng:

```bash
python -m pip install -r requirements.txt
```

Seed sử dụng cho các bước có yếu tố ngẫu nhiên là `20261017` và được đọc từ `config/config.yaml`.

---

## 3. Dữ liệu đầu vào

Ba bộ dữ liệu chính được sử dụng gồm:

| Dữ liệu | Đường dẫn |
|---|---|
| D1 – Nến 1 phút của 6 venue | `data/interim/ohlcv_1m_long.parquet` |
| D2 – Báo giá live của 13 phiên | `data/processed/live_quotes_long.parquet` |
| D9 – Funding rate và open interest | `data/processed/funding_oi.parquet` |

Dữ liệu thật không được đưa trực tiếp lên Git. Trước khi chạy pipeline, cần đặt các file dữ liệu vào đúng đường dẫn tương ứng.

---

## 4. Kiểm tra mã nguồn

Trước khi chạy các bước nghiên cứu, có thể kiểm tra toàn bộ test bằng:

```bash
python -m pytest -q
```

Các test được sử dụng để kiểm tra các bước như đồng bộ thời gian, chia mẫu, schema dữ liệu, điều phối pipeline và các hàm tính price discovery.

---

## 5. Chạy trên dữ liệu giả lập

Dữ liệu giả lập được sử dụng để kiểm tra pipeline trên trường hợp có venue dẫn giá được xác định trước.

Chạy kiểm chứng tổng quát:

```bash
python run_all.py --mode synthetic --verify
```

Chạy trường hợp spot–perpetual:

```bash
python run_all.py --mode synthetic --fixture-case spot_perp
```

Chạy pipeline tích hợp trên bộ dữ liệu giả lập nhiều venue:

```bash
python run_all.py --mode synthetic --config config/pipeline.synthetic.example.yaml --fixture-case multi --data-dir outputs/synthetic/integrated/multi/data --output-dir outputs/synthetic/integrated/multi
```

Các kết quả giả lập được lưu riêng trong:

```text
outputs/synthetic/
```

để tránh nhầm lẫn với kết quả nghiên cứu trên dữ liệu thật.

---

## 6. Chạy phần nền trên dữ liệu thật

Từ thư mục gốc của repository, chạy:

```bash
python run_all.py --mode historical --config config/config.yaml --foundation-only
```

Bước này thực hiện các xử lý nền từ D1, bao gồm xây dựng panel dữ liệu đồng bộ, chia mẫu, phân tích lead–lag và tạo các đầu ra liên quan.

Sau đó có thể kiểm tra dữ liệu bàn giao bằng:

```bash
python -m src.utils.check_handoff --input data/processed/panel_1m_wide.parquet --schema D3 --expected-kind historical --config config/config.yaml --manifest data/processed/split_manifest.json --report outputs/D3_D4_handoff.json
```

Nếu `split_manifest.json` đã tồn tại, chương trình sử dụng lại manifest sau khi kiểm tra fingerprint và ranh giới mẫu thay vì tự tạo lại.

---

## 7. Chạy các bước nghiên cứu

Sau khi phần nền hoàn tất, chạy các bước theo thứ tự sau.

### 7.1. Tạo đặc trưng 1 phút

```bash
python scripts/build_features_1m.py
```

Đầu ra:

```text
data/processed/features_1m.parquet
```

### 7.2. Xác định regime

```bash
python -m src.features.regime_flags
```

Đầu ra:

```text
data/processed/regime_flags.parquet
```

### 7.3. Price discovery

```bash
python -m src.price_discovery.run_price_discovery --data-dir data/processed --output-dir outputs --config config/config.yaml --workers 8
```

Bước này tạo các kết quả liên quan đến kiểm định, VECM, IS, CS, ILS, rolling và phân tích spot–perpetual.

Các đầu ra chính gồm:

```text
T2
T3
T3b
T3c
T3d
rolling_is.parquet
```

### 7.4. Xác định các sự kiện gap

```bash
python -m src.cost_model.run_gap_events
```

Đầu ra chính:

```text
gap_events.parquet
```

### 7.5. Backtest A

```bash
python -m src.backtest.run_backtest_a
```

Backtest A sử dụng proxy giá đóng cửa trên tập development.

### 7.6. Backtest B

```bash
python -m src.backtest.run_backtest_b
```

Backtest B sử dụng dữ liệu báo giá live.

Đầu ra chính:

```text
trades_ledger.parquet
```

### 7.7. Phân tích chi phí

```bash
python -m src.backtest.make_t5
```

Đầu ra:

```text
T5_arbitrage_net.csv
```

### 7.8. Phân tích độ nhạy chi phí

```bash
python -m src.backtest.run_t5b --split test
```

Đầu ra:

```text
T5b_cost_sensitivity.csv
```

**Lưu ý:** bước này sử dụng `final test` và chỉ được thực hiện một lần theo thiết kế của nghiên cứu. Sau khi `final test` đã được sử dụng, pipeline không tự mở lại tập dữ liệu này.

### 7.9. Phân tích gap theo regime

```bash
python -m src.backtest.make_t6b
```

Đầu ra:

```text
T6b_gap_by_regime.csv
```

### 7.10. Tạo hình và đối chiếu ledger

```bash
python -m src.backtest.make_figures
python -m src.backtest.reconcile
```

Các đầu ra chính gồm F4, F5 và T5c.

### 7.11. Đặc điểm venue

```bash
python -m scripts.exchange_characteristics
```

Đầu ra:

```text
T1_exchange_characteristics.csv
```

### 7.12. Lead–lag

```bash
python scripts/make_figure_f6.py
```

Đầu ra:

```text
F6_leadlag_xcorr.png
```

---

## 8. Cách đọc kết quả

Các kết quả nghiên cứu được lưu chủ yếu trong:

```text
outputs/
├── tables/
├── figures/
└── synthetic/
```

Trong đó:

- `outputs/tables/`: các bảng kết quả nghiên cứu, ký hiệu T1, T2, T3, T5,...
- `outputs/figures/`: các hình trực quan, ký hiệu F1, F4, F5, F6,...
- `outputs/synthetic/`: kết quả kiểm chứng bằng dữ liệu giả lập.

Các file dữ liệu trung gian và dữ liệu đã xử lý được lưu trong `data/interim/` và `data/processed/`.

Mỗi lần chạy, `run_all.py` ghi trạng thái pipeline vào:

```text
outputs/pipeline_report.json
```

File này cho biết trạng thái của phần nền và các bước nghiên cứu, tập dữ liệu được sử dụng và trạng thái sử dụng của `final test`.

---

## 9. Kiểm chứng IS và ILS

Các hàm Information Share (IS) và Information Leadership Share (ILS) được kiểm chứng bằng dữ liệu giả lập có venue dẫn giá được xác định trước.

Hai trường hợp chính gồm:

- `multi`: `binance_spot` được thiết lập là venue dẫn.
- `spot_perp`: `binance_perp` được thiết lập là venue dẫn.

IS và ILS xác định đúng venue dẫn trong 30/30 seed ở các trường hợp kiểm chứng. Component Share (CS) nhạy hơn với mức nhiễu tương đối giữa các venue, vì vậy nghiên cứu sử dụng đồng thời IS, CS và ILS thay vì dựa vào một chỉ số duy nhất.

Có thể chạy lại kiểm chứng bằng:

```bash
python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q
```

Tùy chọn `--require-tv5` yêu cầu test thất bại nếu thiếu bất kỳ hàm price discovery cần thiết nào, thay vì bỏ qua bước kiểm tra.

---

## 10. Dashboard

Dashboard của dự án được xây dựng bằng Streamlit tại:

```text
dashboard/app.py
```

Dashboard sử dụng các đầu ra của pipeline để trình bày báo giá, độ phân tán giá, venue dẫn giá theo rolling IS, gap giữa các sàn và kết quả arbitrage sau chi phí.

---

## 11. Lưu ý về khả năng tái lập

D1 sử dụng trong nghiên cứu là phiên bản đã điều chỉnh đơn vị khối lượng của OKX spot từ USDT sang BTC.

D2 phụ thuộc vào 13 phiên thu dữ liệu live nên không thể thu lại chính xác sau thời điểm thu. Vì vậy, quá trình tái lập cần sử dụng bộ dữ liệu live đã được lưu của nghiên cứu.

Funding rate và open interest phụ thuộc vào giới hạn dữ liệu lịch sử của từng sàn. Việc tải lại tại một thời điểm khác có thể tạo ra khoảng dữ liệu khác so với bộ dữ liệu sử dụng trong nghiên cứu.

`Final test` đã được sử dụng một lần cho phân tích độ nhạy chi phí T5b. Theo thiết kế của pipeline, các lần chạy sau không tự mở lại tập này.

---

## 12. Quy ước chính

- Biến, hàm và tên cột sử dụng `snake_case`.
- Lớp sử dụng `PascalCase`.
- Hằng số sử dụng `UPPER_SNAKE`.
- Cột theo venue có dạng `{chỉ_số}__{venue}`.
- Phí và chênh lệch giá được biểu diễn bằng bps.
- Giá được biểu diễn bằng USDT.
- Khối lượng được biểu diễn bằng BTC.
- Thời gian sử dụng UTC.
- Giá trị thiếu được giữ dưới dạng `NaN` và đi kèm cờ `missing__`; dữ liệu không được nội suy.
- Các cặp giao dịch luôn ghi venue mua trước và venue bán sau.
