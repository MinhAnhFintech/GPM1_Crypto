# Bàn giao của Anh (TV4)

Ngày 17/10/2026. Phạm vi: 13 việc code C4.01–C4.13 và hai phần báo cáo R4.01/R4.02, cộng review tích hợp TV2/TV3 theo yêu cầu của Anh. Tiến độ dưới đây phản ánh bằng chứng trong repo, không thay thế việc cập nhật file phân công của nhóm.

## Chạy ngay

Python 3.12, từ gốc repo:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python run_all.py --mode synthetic
.venv\Scripts\python run_all.py --mode synthetic --fixture-case spot_perp
```

Trên máy hiện tại, môi trường `.venv` đã được cài và dùng để kiểm thử. Đây là môi trường phát triển có kế thừa một số thư viện runtime; chưa thay cho bước clone và kiểm tra trên máy sạch của TV1.

Fixtures D1/D2/D3 nằm ở data/fixtures/multi và data/fixtures/spot_perp, mỗi bộ có truth.json và SHA-256. Đầu ra demo: outputs/synthetic/{case}/tables, figures, data và pipeline_report.json. Hình/bảng synthetic chỉ phục vụ kiểm code. Lead–lag chạy trên development; final test chưa được tiêu thụ.

Sau khi TV1 chốt config/config.yaml với sample.start/end có timezone và TV2 bàn giao D1:

```powershell
.venv\Scripts\python run_all.py --mode historical --config config/config.yaml --foundation-only
```

Từ dữ liệu raw, đặt `venues: [binance_spot, okx_spot, ...]` đúng danh sách nhóm đã chốt và chạy `--mode raw`. Khoảng sample dùng ts_available, end loại trừ. Raw Binance được chuyển timestamp mở nến sang ts_available=open+1 phút; không dùng close_time sớm 1 ms.

Để chạy đủ nghiên cứu, từng thành viên đăng ký module đã bàn giao trong `pipeline_stages`, ví dụ:

```yaml
pipeline_stages:
  - name: features
    command: ['{python}', '-m', 'src.features.MODULE_DA_BAN_GIAO', '--data-dir', '{data_dir}']
    outputs: ['{data_dir}/features_1m.parquet']
```

Đây là mẫu đăng ký, không phải tên module đã tồn tại. Các bước cần đủ: features, regime_flags, price_discovery, cost_backtest, evaluation. Đầu ra phải tồn tại, mã thoát phải thành công. Khi chưa đủ, chạy production không có `--foundation-only` sẽ ghi report phần nền rồi trả lỗi. Không xuất IS hoặc PnL giả để đủ số bảng.

## Trạng thái từng việc

| Mã | Kết quả hiện có | Phần còn cần |
| --- | --- | --- |
| C4.01 | Có main/dev, requirements ghim phiên bản, cây thư mục, .gitignore, CONTRIBUTING, CI | CI/cloud và chia sẻ remote chỉ được xác nhận sau khi push/chạy thật |
| C4.02 | DATA_CONTRACT v1.0 và validator D1–D9/D4 JSON; đã xử lý chỗ không thống nhất venue và D9 trong tài liệu nguồn | Nhóm nhận bản bàn giao và tuân thủ khi gửi PR |
| C4.03 | Fixtures nhiều venue và cặp spot/perp, nhãn synthetic, truth và checksum | Kiểm chứng IS/ILS chờ C4.13 |
| C4.04 | UTC nghiêm ngặt, I/O Parquet/JSON nguyên tử, logging UTC, validator/fingerprint | Sử dụng chung trong các module mới của nhóm |
| C4.05 | Mẫu config, hàm vẽ F1/F6, Streamlit bảy khu vực, OLS-HAC chạy trên fixtures | TV1–TV3 nối dữ liệu/model thật vào mẫu |
| C4.06 | align_previous_tick đúng chữ ký, backward-only, max_age, không nội suy | Đã thử synthetic và mẫu thật, cần dữ liệu đầy đủ của nghiên cứu |
| C4.07 | Test bốn ca bắt buộc và ca mở rộng: biên max_age, tách venue, thêm tương lai không đổi quá khứ | Kiểm lại khi có thay đổi logic |
| C4.08 | Dựng D3, giữ UTC/attrs, đã thử mẫu thật ba venue | Chờ D1 đầy đủ từ TV2 để xuất panel M2 chính thức |
| C4.09 | Manifest 60/20/20, checksum, khóa truy cập test cuối, không reset khi chạy lại | Chia mẫu chính thức sau khi chốt khoảng dữ liệu; chưa mở final test |
| C4.10 | Cross-correlation có quy ước dấu, không xóa lỗ hổng trước khi shift; thu lại đúng lag trong cả hai fixtures | Chạy trên panel M2 theo mẫu phân tích đã chốt |
| C4.11 | Đã review hai nhánh hiện có; merge TV2 vào dev cục bộ; thêm các regression test và sửa lỗi tích hợp TV2/TV3 | Review PR thật mỗi ngày tới 24/10, xác nhận CI và merge remote theo quy trình |
| C4.12 | run_all.py chạy phần nền, demo bảng/hình/hồi quy; có cơ chế ghép các bước nhóm, kiểm đầu ra và báo thiếu | Chưa thể đóng pipeline nghiên cứu đầy đủ khi chưa có module TV1/TV2/TV3/TV5/TV6 |
| C4.13 | Hai ca gọi hàm thật TV5: nhiều venue và spot/perp, kiểm IS/CS và ILS từng cặp | Chờ vecm.py, info_share.py, info_leadership.py; chưa xác nhận model đúng |
| R4.01 | Bản thảo mục 4.1 và 4.10 trong TV4_REPORT.md | Cập nhật thông số chính thức từ cấu hình M2 |
| R4.02 | Bản thảo phụ lục kiến trúc/tái lập trong TV4_REPORT.md | Bổ sung kết quả strict IS/ILS và lần chạy đầy đủ trước bản nộp |

## Bằng chứng kiểm tra

Kết quả chi tiết ở TV4_VALIDATION.json. Bộ kiểm thử tự động bao gồm đồng bộ, schema/provenance, round-trip Parquet, chia mẫu/khóa test, fixtures/lead–lag, cache checksum TV2, depth/contractSize/coverage/ghi file TV3, OLS-HAC, pipeline tái lập và Streamlit. Chế độ `--require-tv5` bắt buộc thất bại khi thiếu module TV5; không coi skip là pass.

Đã thử đọc API thật bằng scripts/validate_real_sample.py: 120 nến đã đóng cho mỗi venue binance_spot, okx_spot, binance_perp, cùng một snapshot order book mỗi venue. D1/D2 qua schema và panel thật có 120 dòng lưới, ba venue. Snapshot OKX bị cắt độ sâu và được gắn cờ. Dữ liệu engineering smoke nằm trong tmp/real_review, tách khỏi mẫu nghiên cứu; không chứng minh đủ 90 ngày hoặc đủ coverage live 7–9 ngày.

## Bàn giao tiếp theo

TV1 chốt sample, cấu hình và nguồn phí; TV2 tải đủ dữ liệu, kiểm T0 và gửi D1; TV3 chạy collector đúng lịch, hoàn thiện C3.04/C3.06/C3.09 và xác nhận giới hạn API; TV5 giao ba module trước khi đóng C4.13; TV6 giao cost/backtest cùng các test phí và tách loại hợp đồng. Chi tiết review TV2/TV3 nằm ở TEAM_REVIEW.md.
