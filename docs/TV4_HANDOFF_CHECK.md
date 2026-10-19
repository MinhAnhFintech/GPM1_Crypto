# Kiểm tra file bàn giao cho TV4 (C4.04/C4.11)

Chạy từ gốc repo, trong môi trường Python của dự án. Công cụ chỉ đọc D1/D2/D3 và manifest D4 được chỉ định; không tải dữ liệu, chuẩn hóa/sửa file, tạo panel hay mở/claim/reset final test. `--report` chỉ ghi JSON chẩn đoán, không phải kết quả nghiên cứu.

Kiểm fixtures D3 cùng manifest do demo đã tạo:

```powershell
python -m src.utils.check_handoff --input outputs/synthetic/multi/data/panel_1m_wide.parquet --schema D3 --expected-kind synthetic --manifest outputs/synthetic/multi/data/split_manifest.json --report tmp/review/fixtures_handoff.json
```

Khi TV2 giao D1 thật, dùng cấu hình nghiên cứu đã chốt. Có thể thêm `--sha256` với checksum người gửi bàn giao:

```powershell
python -m src.utils.check_handoff --input data/interim/ohlcv_1m_long.parquet --schema D1 --expected-kind historical --config config/config.yaml --report tmp/review/D1_handoff.json
```

Sau khi dựng nền, đối chiếu D3/D4 thật:

```powershell
python -m src.utils.check_handoff --input data/processed/panel_1m_wide.parquet --schema D3 --expected-kind historical --config config/config.yaml --manifest data/processed/split_manifest.json --report tmp/review/D3_D4_handoff.json
```

Kiểm D2 TV3 bàn giao:

```powershell
python -m src.utils.check_handoff --input data/processed/live_quotes_long.parquet --schema D2 --expected-kind live --report tmp/review/D2_handoff.json
```

`--config` là tùy chọn. Khi có, venues phải là danh sách ID duy nhất và khớp chính xác với dữ liệu; spot/perp được giữ riêng. Chỉ dùng config nghiên cứu để kiểm file đã chọn đúng các venue nghiên cứu. File live còn gồm venue ứng viên có thể kiểm không kèm config; kiểm này không xác nhận danh sách venue cuối cùng.

Nhóm đã chốt Binance/OKX/Bybit, mỗi sàn gồm spot và perp (6 venue nghiên cứu). Collector live vẫn thu thêm Kraken spot và Coinbase spot, tổng cộng 5 sàn/8 venue; đây là thu dư có chủ đích để giữ quan sát live. Lệnh kiểm D2 ở trên chấp nhận tập thu rộng mà không cắt dữ liệu. Khi cần đối chiếu với config/config.yaml, tạo bản nghiên cứu đã chọn đúng 6 venue ở đường dẫn riêng và giữ nguyên bản thu đầy đủ. Không dùng cấu hình nghiên cứu để thu hẹp collector hoặc ghi đè bản gốc. Các lệnh run_all raw/historical xử lý nến lịch sử, không khởi chạy hay lọc bộ thu live.

Với D1, báo cáo đếm dòng trong/ngoài `[sample.start, sample.end)` theo **ts_available**; không cắt file nguồn. D1 có dòng ngoài mẫu vẫn được chấp nhận nếu mọi venue đều có dữ liệu bên trong, vì run_all sẽ lọc sau. D3 phải ở trong khoảng mẫu và có lưới liên tục đúng timeframe. D2 dùng ts_recv_utc; sample lịch sử không lọc dữ liệu live. Độ phủ live theo phiên vẫn do công cụ coverage của TV3 kiểm tra.

Manifest D4 được đối chiếu fingerprint, các biên chia mẫu và metadata bổ sung nếu có. Tỷ lệ lấy từ config khi chỉ định; mặc định đúng PCCV 60/20/20. Công cụ báo test_consumed và sự hiện diện của khóa, giữ nguyên chúng. `--report` không được ghi đè input, config, manifest hoặc khóa final test tương ứng.

Phép đối chiếu D4 dùng chung `validate_panel_split` với pipeline và API chọn/claim split. Vì vậy manifest có fingerprint đúng nhưng biên/counts sai bị từ chối ngay cả khi không chạy CLI kiểm bàn giao riêng.

JSON có status `passed`, `failed` hoặc `missing`; mã thoát tương ứng 0, 1, 2. `missing` nghĩa chưa nhận được file đầu vào, không phải đã nghiệm thu. File sai schema/provenance/checksum hoặc manifest không khớp trả failed. Passed chỉ xác nhận phạm vi hợp đồng/bàn giao đã kiểm, không chứng minh đủ coverage, đúng mô hình hoặc hoàn tất M1/M2/M3. Các test dùng fixtures phải giữ nhãn synthetic; không đổi nhãn để vượt kiểm tra đầu vào historical/live.
