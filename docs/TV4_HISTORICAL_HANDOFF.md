# TV4 bàn giao D3/D4 và lead-lag — 21/10/2026

Đã thực hiện C4.08, C4.09 và C4.10 trên D1 thật của TV2, theo khoảng mẫu hiện có trong config/config.yaml. Đây là bàn giao kỹ thuật để TV5/TV6/TV2 dùng chung panel và split; nhóm vẫn cần xác nhận TODO về khoảng mẫu trước khi chốt M2. Không sửa Excel, không đóng băng đặc tả mô hình và không dùng final test.

## Nguồn và kết quả

- Code nền: dev 2abc1b9, sau tích hợp PR TV2 #9.
- D1: data/interim/ohlcv_1m_long.parquet, 3.006.714 dòng; SHA-256 FEA20184F835471EBDD48D43F7D697D36195BAB74F1B7DF3B790D20F9B23E09F.
- Mẫu theo ts_available: [2025-10-18T00:01:00Z, 2026-10-01T00:00:00Z), đúng config và D1 Ny bàn giao.
- Sáu venue: Binance/OKX/Bybit, mỗi sàn spot và perp. Collector live 5 sàn/8 venue không thay đổi.
- D3: data/processed/panel_1m_wide.parquet, 501.119 dòng × 24 cột, lưới UTC một phút; close/log_close/volume_base/missing theo từng venue.
- Không có missing ở cả sáu venue. Đối chiếu từng timestamp, close và volume_base với D1 đều khớp, không cần carry-forward trên mẫu này. log_close khớp ln(close).
- D4: data/processed/split_manifest.json, chia 60/20/20 theo thời gian và giữ fingerprint. test_consumed=false, không có khóa .test.lock.

Panel fingerprint:

```text
e3fa598191fdcf0632bf07cb12967ff9cdf282e844c96eada90ef0bf36bb88be
```

## Biên chia mẫu — UTC, bao gồm hai đầu mỗi tập

| Tập | Số dòng | Bắt đầu | Kết thúc |
| --- | ---: | --- | --- |
| Development | 300.671 | 2025-10-18 00:01 | 2026-05-14 19:11 |
| Validation | 100.223 | 2026-05-14 19:12 | 2026-07-23 09:34 |
| Final test | 100.225 | 2026-07-23 09:35 | 2026-09-30 23:59 |

Development/validation lấy floor của 60%/20% tổng số dòng; test nhận phần còn lại. Không shuffle, không nén thời gian. Phép kiểm schema/QC/fingerprint sử dụng panel để kiểm tính toàn vẹn, không phải phân tích mô hình trên final test. Các bảng/hình phân tích của lượt này chỉ dùng development; final test chưa được claim hoặc đánh giá.

## C4.10 — kết quả development

outputs/tables/T2_leadlag_xcorr.csv có 165 dòng: 15 cặp venue × 11 lag từ -5 đến +5 phút, gồm cả các cặp spot/perp. Đây là bảng kỹ thuật nguồn của F6, không phải T2 kiểm định dừng/đồng tích hợp của TV5.

Quy ước: corr(ret_A(t), ret_B(t+lag)); lag dương nghĩa A dẫn B. Ở lag 0 có 300.670 cặp returns hợp lệ. Cả 15 cặp đều có tương quan lớn nhất tại lag 0, khoảng 0,992089–0,996787. Chưa thấy độ trễ dẫn giá khác 0 ở độ phân giải một phút trên development; không diễn giải thành leader IS/ILS hoặc cơ hội arbitrage có lời.

Đã đối chiếu độc lập tất cả 165 giá trị bằng NumPy trên development; sai khác tuyệt đối lớn nhất 1,11e-16. Hình F1_synchronized_prices.png và F6_leadlag_xcorr.png trong outputs/figures đã kiểm trực quan; F1 cũng chỉ vẽ development. Tên historical là provenance của nguồn, không có nghĩa hình dùng toàn bộ ba tập.

## Lệnh tái lập và kiểm bàn giao

```powershell
python run_all.py --mode historical --config config/config.yaml --foundation-only
python -m src.utils.check_handoff --input data/processed/panel_1m_wide.parquet --schema D3 --expected-kind historical --config config/config.yaml --manifest data/processed/split_manifest.json --report outputs/D3_D4_handoff.json
```

D1/D3/D4 là dữ liệu local, được ignore; không thêm vào Git. Các bảng, hình và chứng cứ kiểm nghiệm nằm trong outputs/. outputs/pipeline_report.json ghi foundation_status=passed, analysis_split=dev, test_consumed=false. outputs/tv4_historical_validation.json lưu checksum từng artifact và kết quả đối chiếu. Các số này là kết quả hiện tại trước đóng băng, cần tái lập theo bản cấu hình cuối khi viết báo cáo nộp.

Nếu dựng lại D1, dùng raw đúng phiên bản data/raw/ohlcv/_handoffs/pr9-db2cabd/. Bộ raw cũ vẫn ở thư mục gốc để đối chiếu; không dùng nhầm bộ cũ thiếu quote volume rồi ghi đè D1 Ny đã xác nhận. Receipt nguồn nằm cạnh D1. Khi dữ liệu/config thay đổi làm fingerprint khác D4 hiện tại, chương trình từ chối; không xóa manifest hoặc khóa để bỏ qua kiểm soát.

## Kiểm thử và phần còn chờ

Lượt dựng thật: 52 passed cho alignment, fixtures/splits, intake và strict TV5. Kiểm cuối toàn repo trước commit: 390 passed, 1 skipped (symlink Windows). Lượt dựng thật không sửa code thuật toán; có thêm kiểm schema, dữ liệu nguồn, NumPy xcorr và xem hình trên đầu ra thật.

- C4.08/C4.09/C4.10: đã có đầu ra thật và bằng chứng kỹ thuật theo config hiện tại.
- R4.01: đã bổ sung biên mẫu, số dòng và missing thực tế trong tài liệu TV4; TV1/nhóm còn xác nhận khoảng mẫu và đưa nội dung vào bản báo cáo chung.
- C4.11: tiếp tục review các PR mới.
- C4.12/R4.02: nền chạy được, chưa hoàn tất pipeline nghiên cứu; còn features (TV2), regime_flags (TV1), price_discovery trên mẫu thật (TV5), cost_backtest (TV6), evaluation (TV3/TV6). CLI TV5 đã nhận nhưng chưa được đăng ký/chạy như một stage của lượt foundation này.
- D2 live/coverage và D9 funding/OI của TV3 vẫn là đầu vào cho các nhánh sau; chúng không chặn D1 → D3/D4.
