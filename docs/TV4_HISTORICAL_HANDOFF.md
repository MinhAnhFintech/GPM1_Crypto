# TV4 bàn giao D3/D4 và lead-lag — 21/10/2026

**Cập nhật trạng thái 26/10/2026:** khoảng mẫu đã được nhóm xác nhận; fingerprint và biên mẫu bên dưới vẫn đúng. Final test canonical hiện đã consumed và có khóa do T5b của TV6; các dòng test_consumed=false ghi lại trạng thái của lượt dựng ngày 21/10, không dùng để reset trạng thái hiện hành. Pipeline local từ D1 đã đủ 19 stage chạy đạt; các phụ thuộc từng liệt kê ở cuối tài liệu là lịch sử, xem docs/TV4_PENDING_HANDOFFS.md cho danh sách hiện tại. Chưa xác nhận tái tạo D1 mới nhất từ raw.

Đã thực hiện C4.08, C4.09 và C4.10 trên D1 thật của TV2, theo khoảng mẫu trong config/config.yaml. Đây là bàn giao kỹ thuật để TV5/TV6/TV2 dùng chung panel và split. Lượt dựng ngày 21/10 không sửa Excel, không đóng băng đặc tả mô hình và không dùng final test.

## Nguồn và kết quả

- Code hiện tại: dev f50b1f2; không sửa thuật toán trong lượt cập nhật dữ liệu C2.03 này.
- D1 mới nhất: data/interim/ohlcv_1m_long.parquet, 3.006.714 dòng; SHA-256 A162AFFAEAE0AE16BD4F52DD9B1ABD68C226555C8665D3BEF3AF73757F8209F3.
- Mẫu theo ts_available: [2025-10-18T00:01:00Z, 2026-10-01T00:00:00Z), đúng config và D1 Ny bàn giao.
- Sáu venue: Binance/OKX/Bybit, mỗi sàn spot và perp. Collector live 5 sàn/8 venue không thay đổi.
- D3: data/processed/panel_1m_wide.parquet, 501.119 dòng × 24 cột, lưới UTC một phút; close/log_close/volume_base/missing theo từng venue.
- Không có missing ở cả sáu venue. Đối chiếu từng timestamp, close và volume_base với D1 đều khớp, không cần carry-forward trên mẫu này. log_close khớp ln(close).
- D4: data/processed/split_manifest.json, chia 60/20/20 theo thời gian và giữ fingerprint. test_consumed=false, không có khóa .test.lock.

Panel fingerprint:

```text
9420d92ff9d5d0a334d8289b16bc56cb9b86720150d86316b03ff768325356a4
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

D1 hiện tại nhận trực tiếp từ link Drive người dùng cung cấp và đã khớp hash A162…09F3. Các bộ raw đã lưu, kể cả data/raw/ohlcv/_handoffs/pr9-db2cabd/, là bản lịch sử và chưa tái tạo được phần volume OKX spot đã sửa. Để dựng lại D1 từ raw cần Ny/TV3 bàn giao raw hoặc bản xử lý tương ứng; không dùng lệnh mode raw hoặc CLI gộp với raw cũ để ghi đè D1 mới. Các lệnh historical ở trên đọc trực tiếp D1 mới và vẫn tái lập được D3/D4. Receipt nguồn nằm cạnh D1. Khi dữ liệu/config thay đổi làm fingerprint khác D4 hiện tại, chương trình từ chối; không xóa manifest hoặc khóa để bỏ qua kiểm soát.

## Bản sửa C2.03 A162…09F3

Nguồn: https://drive.google.com/file/d/1a0mpUm46oyyC1zqJBi22KLY_khefWeyi/view. Đã kiểm SHA-256 toàn bộ 116.591.087 bytes, schema D1, UTC/lưới phút, khóa duy nhất, sáu venue và khoảng mẫu.

Chỉ volume_base của OKX spot đổi ở 501.119 dòng; bản cũ đã ghi volume_quote USDT vào cả cột volume_base. Bản mới dùng BTC, với volume_quote/volume_base nằm trong khoảng low–high ở toàn bộ các dòng OKX spot. Giá OHLC, timestamp, quote volume và các venue khác không đổi. Volume này được dùng trong D3, vì vậy đã dựng lại D3 và D4 với fingerprint mới ở trên; biên và số dòng ba tập không đổi. Bảng lead-lag và bytes hai hình F1/F6 không đổi vì chỉ dùng giá.

Bản D1 FEA…E09F, D3, D4 và các kết quả/biên nhận cũ được lưu nguyên tại data/processed/_revisions/d1-fea20184f835471e/. D4 cũ có test_consumed=false và không có khóa; trạng thái được lưu cùng bản cũ. Không xóa khóa hoặc reset một final test đã dùng. Bản hiện tại cũng có test_consumed=false.

Chi tiết so sánh nằm trong outputs/D1_revision_comparison.json. outputs/tables/T0_D1_quality.csv kiểm trực tiếp D1 mới và ghi hash nguồn; T0_data_quality.csv cũ vẫn là snapshot kiểm raw của PR #9. Kiểm thêm quan hệ volume_quote/volume_base phát hiện hai dòng Bybit spot có giá bình quân suy ra nằm ngoài low–high: 2025-12-19T07:50Z và 07:58Z, chênh tối đa 1,856848 bps. Hai dòng giữ nguyên từ bản trước; chưa kết luận nguyên nhân và không tự sửa số liệu. Cờ chi tiết: outputs/tables/T0_D1_volume_flags.csv, để Ny đối chiếu nguồn.

## Kiểm thử và phần còn chờ

Lượt dựng thật: 52 passed cho alignment, fixtures/splits, intake và strict TV5. Kiểm cuối toàn repo trước commit: 390 passed, 1 skipped (symlink Windows). Lượt dựng thật không sửa code thuật toán; có thêm kiểm schema, dữ liệu nguồn, NumPy xcorr và xem hình trên đầu ra thật.

Lượt cập nhật A162: 48 passed cho alignment, fixtures/splits và intake; D3/D4 mới qua kiểm bàn giao, đối chiếu từng giá trị với D1 mới và kiểm độc lập đủ 165 xcorr. Không chạy lại các stage mô hình hoặc final test.

- C4.08/C4.09/C4.10: đã có đầu ra thật và bằng chứng kỹ thuật theo config hiện tại.
- R4.01: đã bổ sung biên mẫu, số dòng và missing thực tế trong tài liệu TV4; TV1/nhóm còn xác nhận khoảng mẫu và đưa nội dung vào bản báo cáo chung.
- C4.11: tiếp tục review các PR mới.
- C4.12/R4.02: nền chạy được, chưa hoàn tất pipeline nghiên cứu; còn features (TV2), regime_flags (TV1), price_discovery trên mẫu thật (TV5), cost_backtest (TV6), evaluation (TV3/TV6). CLI TV5 đã nhận nhưng chưa được đăng ký/chạy như một stage của lượt foundation này.
- D2 live/coverage và D9 funding/OI của TV3 vẫn là đầu vào cho các nhánh sau; chúng không chặn D1 → D3/D4.
