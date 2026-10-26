# Dữ liệu đi kèm repository

Thư mục `data/` chứa đủ dữ liệu để mở dashboard ngay và tái lập toàn bộ kết quả từ dữ liệu thô, không cần tải thêm hay chạy collector. Mọi lệnh chạy từ thư mục gốc repository, Python 3.12, sau khi cài `python -m pip install -r requirements.txt` (nên dùng môi trường ảo riêng).

## Các file

| Mã | File | Dòng | SHA-256 |
| --- | --- | ---: | --- |
| Raw | data/raw/ohlcv/binance_perp_BTCUSDT_1m.parquet | 501.119 | 92046757ab9ecb6cafdf96d742ac5910199e83b7352eead3abbcead8a2ddc837 |
| Raw | data/raw/ohlcv/binance_spot_BTCUSDT_1m.parquet | 501.119 | 60a3b27d0a780a2e8799ca0261e31007a1f5d3e4a66f1e8baa60d625688affab |
| Raw | data/raw/ohlcv/bybit_perp_BTCUSDT_1m.parquet | 501.119 | be944388cbb75dce54110d28c42d75b767d8a955234af6af4ee0f4871630a0d1 |
| Raw | data/raw/ohlcv/bybit_spot_BTCUSDT_1m.parquet | 501.119 | 84ac666793db22ba909a012d31f52a17534055d5484e551240aac58c20437ff7 |
| Raw | data/raw/ohlcv/okx_perp_BTCUSDT_1m.parquet | 501.119 | 2ce1fbbfe73c34a95846a24dd6c3de5ead11025bea5a671b2b07c13106c4686c |
| Raw | data/raw/ohlcv/okx_spot_BTCUSDT_1m.parquet | 501.119 | 29d42f08b4500ebe12215cab82bc15fc40937969a08e4ed6abf2ddfcb87a4ea5 |
| D2 | data/processed/live_quotes_long.parquet (13 phiên live 17–23/10/2026) | 168.178 | 50104edc92bf1d46ce7eb91bba45344f7d287978c5bf70c59b7da3bac59e6f64 |
| D9 | data/processed/funding_oi.parquet | 4.412 | dae72d54ab4c7923abc58313f422019893c568997d2f96b52adeb87763815c04 |
| D3 | data/processed/panel_1m_wide.parquet | 501.119 | f3d22fbbb3129a72333e71f0ccc9b5e26e2fd08d88eb4dc13c98f89cccc72262 |
| D4 | data/processed/split_manifest.json (fingerprint 9420d92f…56a4, `test_consumed=true`) | | fce0c3cf3b1283ffb28e86d63bb1d3a8480219b0bd2220c6dd181ba0a663d841 |

`split_manifest.json.test.lock` đi kèm D4: final test đã được T5b dùng một lần, không xóa khóa và không đặt lại `test_consumed`.

D1 `data/interim/ohlcv_1m_long.parquet` (112 MB) vượt giới hạn file của GitHub nên không kèm theo; dựng lại từ sáu file raw ở bước 2 cho đúng từng byte bản dùng trong nghiên cứu. Dữ liệu live là bản đã thu, không thể thu lại giống hệt; raw live theo phiên không kèm theo vì D2 ở trên là bản cuối dùng cho mọi kết quả.

## 1. Xem dashboard ngay

```bash
python -m streamlit run dashboard/app.py
```

Dashboard đọc D3, D2, D4 ở trên và các bảng/hình đã có trong `outputs/`.

## 2. Tái lập D1 từ raw

```bash
python src/features/ohlcv_long.py
```

Kết quả: 3.006.714 dòng, SHA-256 của `data/interim/ohlcv_1m_long.parquet` là `a162affaeae0ae16bd4f52dd9b1abd68c226555c8665d3bef3af73757f8209f3`. Biên nhận kiểm chứng: `outputs/tv4_raw_d1_rebuild.json`.

## 3. Tái lập D3/D4 và toàn bộ bảng, hình

```bash
python run_all.py --mode historical --config config/config.yaml --foundation-only
python run_all.py --mode historical --config config/config.yaml
```

Lệnh thứ nhất dựng lại D3/D4 từ D1 và kiểm fingerprint với D4 đã khóa; D3 dựng lại có cùng nội dung và fingerprint 9420d92f…56a4, còn SHA-256 của file có thể khác vì metadata Parquet ghi phiên bản pandas/pyarrow. Lệnh thứ hai chạy cả 19 bước nghiên cứu đăng ký trong `config/config.yaml` và ghi lại `outputs/`; bước price discovery mất khoảng một giờ. T5b không chạy lại vì final test đã khóa; `outputs/tables/T5b_cost_sensitivity.csv` là bản đã nộp.
