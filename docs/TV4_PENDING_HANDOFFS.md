# TV4 — trạng thái và phụ thuộc hiện tại, 27/10/2026

Các bảng trong phần «Nhật ký 21/10» bên dưới ghi lịch sử, không dùng để xác định đầu vào còn chờ hiện tại. Không cập nhật trạng thái Excel hoặc xuất bản báo cáo chung từ tài liệu này.

## Phần TV4 đã có

| Mã | Trạng thái hiện tại |
| --- | --- |
| C4.01–C4.07 | Repo, hợp đồng D1–D9, D2/D9 mở rộng v1.1, hai bộ fixtures, UTC/I/O/schema, templates, previous-tick và kiểm thử thời gian. Validator chung chặn nhãn cột/metadata mâu thuẫn. |
| C4.08–C4.10 | D3 thật 501.119 × 24, sáu venue, không missing; D4 300.671/100.223/100.225, fingerprint 9420d92f…56a4; đủ 165 xcorr đã kiểm NumPy. Khoảng mẫu đã xác nhận trong config, không còn chờ chốt sample. |
| C4.11 | Đã merge Ly #33 (09b9aff) và Thanh #35 (4e5b138, dashboard C2.08 kèm sửa kiểm D2; RQ4 có nguồn D7 live 3.209 sự kiện, trung vị 5 giây). Ny #32 đã đóng không merge: các sửa ở commit 9632246 của Ny đã có trên dev. Huỳnh #34 đã merge (b461047, head 76b1ec4): README dùng lệnh một bước `run_all.py`, T0b chỉ liệt kê là bảng có sẵn, không còn lệnh cần raw live. |
| C4.12 | config.yaml đăng ký 19 stage CLI thật; một lệnh từ D1 đến bảng/hình đã đạt 19/19. Đã đối chiếu hash 41 output và toàn bộ nguồn/config. Bộ TV5 của Châu (T2/T3/T3b/T3d, 12/12 hash khớp SHA256SUMS) đã đưa vào outputs/tables; bản TV4 tái lập từ D1 cho cùng số dòng/cột, cùng leader/status/kết luận, sai khác số tối đa 4e-4 trên thống kê kiểm định. Đã nghiệm thu raw → D1 (26/10): sáu file raw Ny đưa lên Drive crypto/raw_ohlcv_D1_A162, chạy `src/features/ohlcv_long.py` trên dev 26e60b9 cho D1 3.006.714 dòng trùng byte A162…09F3; hàm đọc raw của `run_all --mode raw` cho cùng giá trị. Hash từng file raw trong outputs/tv4_raw_d1_rebuild.json. |
| C4.13 | Strict TV5 đạt multi/spot_perp, original/identified_cs; không còn chờ hàm TV5. |
| R4.01/R4.02 | Nội dung phương pháp và phụ lục đã có trong docs/TV4_REPORT.md; còn xác nhận đã vào báo cáo chung. |

Final test canonical đã được T5b dùng một lần: manifest `test_consumed=true`, có `.test.lock`. Lượt tái lập không chạy T5b, không reset manifest/khóa. Các receipt cũ ghi false chỉ là lịch sử; tương tự `raw_rebuild_verified=false` trong các receipt ngày 21/10 được thay bằng outputs/tv4_raw_d1_rebuild.json.

Hai cờ volume Bybit spot (19/12/2025 07:50 và 07:58 UTC, lệch dưới 2 bps, có từ trước bản sửa) giữ nguyên trong T0_D1_volume_flags.csv và chỉ chú thích trong báo cáo; không còn yêu cầu đối chiếu nguồn. `run_all --mode raw` luôn ghi D1 vào data/interim kể cả khi có --data-dir, nên khi kiểm raw dùng `ohlcv_long.py --out` ra file tạm.

Phần TV4 và cấu hình 19 stage được rebase lên dev sau PR #35 (không trùng file với dashboard). Bản sửa tích hợp #32 cũ ở working tree đã được #35 thay thế, cất trong git stash, không đưa vào commit. T3c_spot_perp.csv, rolling_is.parquet và T2_vecm_spec.json giữ bản đã có trên dev: D6 và spec trùng byte bộ Châu; T3c trên dev thêm cột data_kind, số lệch tối đa 3e-11, kết luận không đổi.

## Thành viên cần bàn giao/xác nhận

| Người / mã việc | Cần nhận cụ thể | Phần bị chặn |
| --- | --- | --- |
| Huỳnh — C1.07 | Đã đồng bộ T1 bằng D2 cuối 168.178 báo giá/13 phiên tới 23/10; số dòng từng venue và dev+val đã kiểm. | Không còn chờ T1. |
| Huỳnh — C1.05 | Đã tích hợp bản dictionary từ nhánh 4b84343, cập nhật D2 đủ 13 phiên, D4 test_consumed=true và đầy đủ ý nghĩa D9 in_live_window v1.1. | Không còn chờ dictionary. |
| Huỳnh — biên tập R4.01/R4.02 | Xác nhận đã đưa mục 4.1, 4.10 và phụ lục TV4 vào báo cáo chung, dùng trạng thái test đã khóa và mẫu đã xác nhận. | Đóng bàn giao báo cáo TV4. |

D1 mới, D2 cuối 168.178 dòng/13 phiên, D9 historical, D3/D4, D5/D6 và các CLI đã có bằng chứng nhận/chạy; không cần xin lại dữ liệu live. D3b và D5 bàn giao đã được kiểm hash/giá trị rồi đặt tại data/processed; live_session_is được khôi phục từ lượt pipeline đã kiểm. outputs/pipeline_report.json hiện là snapshot xác minh 19 stage với test_consumed=true; không phải lần chạy lại mô hình. Giữ phạm vi nghiên cứu sáu venue Binance/OKX/Bybit spot + perp, collector năm sàn/tám venue gồm Kraken/Coinbase không đổi.

Nguồn bằng chứng: docs/TV4_PIPELINE_INTEGRATION.md, docs/TV4_HISTORICAL_HANDOFF.md và outputs/tv4_research_pipeline_validation.json.

## Nhật ký 21/10/2026 — đã được cập nhật bằng các bảng hiện trạng ở trên

Đối chiếu GPM1_CRYPTO.xlsx (C4.01–C4.13, R4.01/R4.02, Mốc và Phụ thuộc) và PDF mục 2, 3.3, outline báo cáo. Trạng thái dưới đây tổng hợp các bàn giao đã kiểm và lượt dựng D3/D4 thật ngày 21/10. Không cập nhật trạng thái Excel từ tài liệu này.

## Ưu tiên trước M2

| TV4 cần làm | Chờ ai / mã việc | Cần nhận cụ thể | Mốc gốc |
| --- | --- | --- | --- |
| C4.08 dựng D3, C4.09 đóng D4 trên dữ liệu thật | Đã thực hiện và kiểm kỹ thuật trên D1/T0 của TV2 qua PR #9 | D3 501.119 × 24, đủ 6 venue, không missing; D4 300.671/100.223/100.225, fingerprint/biên hợp lệ, final test chưa dùng. Không còn chờ TV2 hoặc code TV4 cho bước nền này | Mốc gốc: TV2 trước **12:00 ngày 20/10**; TV4 M2 cuối 20/10 |
| C4.08/C4.09 giữ đúng mẫu nghiên cứu | TV1 C1.02 cùng kết quả khả thi TV3 C3.01 | Xác nhận sample.start/end theo ts_available; config hiện đã có nhưng còn chú thích cần nhóm xác nhận khoảng mẫu. Danh sách sàn đã được nhóm chốt, không còn là đầu vào đang chờ | Trước khi đóng M2 |

Đã nhận và kiểm D1 thật của TV2 qua PR #9, chi tiết trong docs/TV2_PR9_REVIEW.md. Ngày 21/10 đã dựng và kiểm D3/D4 thật theo sáu venue trong config; các artifact nằm đúng đường dẫn data/processed, giữ local và được ignore. C4.08/C4.09/C4.10 đã có bằng chứng kỹ thuật trên mẫu hiện tại; xem docs/TV4_HISTORICAL_HANDOFF.md. Không còn chờ TV2 giao D1; M2 vẫn cần nhóm xác nhận TODO về khoảng mẫu trong config. Chưa gửi tin nhắn hoặc xuất bản bản báo cáo chung cho các thành viên.

Cập nhật nguồn C2.03: D1 mới nhất có hash A162…09F3, sửa volume_base OKX spot từ USDT về BTC. Đã cập nhật D3 và fingerprint D4; số dòng/biên chia mẫu và lead-lag không đổi. Bản FEA…E09F cùng D3/D4 cũ đã lưu nguyên trong data/processed/_revisions/d1-fea20184f835471e/. Các raw cũ, gồm bản pr9-db2cabd, vẫn giữ nguyên; chưa dùng chúng để tái tạo D1 đã sửa. Cần Ny/TV3 cung cấp raw hoặc code chuẩn hóa tương ứng nếu muốn tái tạo D1 mới từ đầu; việc này không chặn D1 mới → D3/D4. Có hai cờ quote/base volume Bybit spot tồn tại từ trước cần Ny đối chiếu, chi tiết trong docs/TV4_HISTORICAL_HANDOFF.md. D2 v1.1 và kiểm tương thích đã có; vẫn chờ D2 live thật và coverage theo phiên để nghiệm thu nhánh live.

Nhóm đã chốt nghiên cứu Binance, OKX và Bybit, mỗi sàn gồm spot và perp. Thu live rộng hơn vẫn hợp lệ: config/live_sessions.yaml còn Kraken spot và Coinbase spot để giữ dữ liệu tại thời điểm thu. Không xóa dữ liệu thu dư hoặc thu hẹp collector; kiểm D2 thu rộng không truyền config nghiên cứu, còn bàn giao cho phân tích 6 venue dùng bản đã chọn riêng. sample lịch sử không áp vào D2 live.

M2 và lead-lag từ D3 không phụ thuộc mô hình IS/ILS. D2 live và funding/OI phục vụ các nhánh phân tích khác, không là đầu vào bắt buộc của D1→D3/D4.

## Các nhánh công việc còn lại

| Việc TV4 | Người/đầu vào còn chờ | Tình trạng và bước TV4 sẽ làm khi nhận |
| --- | --- | --- |
| C4.10 số liệu lead-lag/F6 thật | Đã chạy trên development của D3 thật | 165 giá trị cho 15 cặp × 11 lag; kiểm độc lập NumPy khớp. Cả 15 đỉnh ở lag 0; không gán leader IS/ILS từ tương quan này |
| Tích hợp/kiểm đồng bộ D2 thật, hỗ trợ C4.11/C4.12 | Đã nhận bản cuối của TV3 (25/10) | D2 13 phiên, 168.178 dòng, SHA-256 50104EDC…6F64 qua validator D2 v1.1 (UTC ns, depth_truncated bool, khóa không trùng); trùng hash đã khóa trong frozen_t5b.yaml. D9 qua validator, một nhãn historical. Chi tiết trong docs/TV3_COLLECTOR_HANDOFF.md |
| C4.13 kiểm chứng IS/CS/ILS trên fixtures | Đã hoàn thành kiểm chứng và merge PR TV5 #7 vào dev d318a36 ngày 20/10 | Strict 4/4 trên original/identified_cs × multi/spot_perp; không còn chờ hàm TV5. Xem docs/TV5_PR7_REVIEW.md. Kết quả synthetic không thay nghiệm thu mẫu thật |
| C4.12 pipeline nghiên cứu đầy đủ | Không còn chờ CLI | 26/10: config.yaml đăng ký 19 stage CLI thật; một lệnh chạy D1 → D3/D4 → mọi bảng/hình, 19/19 passed, full_research_pipeline_complete=true, final test không đụng. Kết quả khớp bàn giao (phần lớn trùng byte). Chạy ở thư mục cô lập, outputs/ không đổi. Xem docs/TV4_PIPELINE_INTEGRATION.md |
| C4.11 review hằng ngày | PR/commit tiếp theo của các thành viên | Đã xử lý tới PR #35 và #34. Dashboard C2.08 vào dev qua #35 (4e5b138) với kiểm D2 và nguồn nhất quán; #32 đóng không merge vì #35 đã bao phần sửa; README C1.08 vào dev qua #34 (b461047). Trang Sàn dẫn giá mở đầu RQ1 bằng xếp hạng mô tả gộp 6.658 cửa sổ (IS điểm giữa, tỷ lệ đứng đầu, CS, ILS) kèm kết luận chưa xác định sàn dẫn giá ở tần suất 1 phút |
| R4.01 phương pháp 4.1/4.10 | Nhóm xác nhận khoảng mẫu; TV1 đưa nội dung vào báo cáo chung | Đã viết và bổ sung số dòng/biên/missing thật; không còn chờ dữ liệu để viết phần này |
| R4.02 kiến trúc và tái lập | TV1 đưa vào báo cáo chung | Đã bổ sung phụ lục toàn pipeline nghiên cứu trong docs/TV4_REPORT.md: bảng stage, lệnh tái lập một lệnh, thời gian chạy và kết quả đối chiếu |

## Bàn giao cần để đăng ký năm stage C4.12

Cập nhật 26/10: các CLI dưới đây đều đã merge và được đăng ký trong config.yaml (19 stage). Bảng giữ lại làm lịch sử phụ thuộc.

| Stage | Chủ sở hữu | Đầu ra/CLI cần nhận |
| --- | --- | --- |
| features | TV2 C2.04 | CLI tạo D3b features_1m từ D3; giữ từng venue và dispersion riêng spot/perp |
| regime_flags | TV1 C1.04/C1.06 | CLI tạo D5 từ mẫu đúng split; ngưỡng high-vol fit trên development và events có nguồn |
| price_discovery | TV5 | CLI từ PR #7 đã nối qua orchestrator bằng profile synthetic, cả multi/spot_perp đều có sáu cửa sổ, zero failed. Cần review đặc tả và chạy trên D3 thật trước đăng ký stage nghiên cứu. available_at chờ cả window_end và thời điểm chọn spec |
| cost_backtest | TV6, đầu vào D2 từ TV3 | CLI backtest A/B, D7/D8 và bảng chi phí; half-spread proxy cần mẫu live được hiệu chỉnh, không tự đặt số thay dữ liệu |
| evaluation | TV3 + TV6 | CLI hồi quy/stress/sensitivity và các bảng T4/T6/T5b/T6b phù hợp; không dùng final test để chọn lại cấu hình |

Funding/OI D9 của TV3 và D5 regime của TV1 là phụ thuộc của các stage nghiên cứu sau M2. Dashboard TV2 là sản phẩm riêng; TV4 không cần chờ dashboard để bàn giao D3/D4.

## Phần TV4 đã tự xử lý trong lượt sửa này

- Chặn synthetic ghi vào vùng nghiên cứu qua data_dir/output_dir, kể cả đường dẫn đã resolve; kiểm các outputs khai báo của stage trước khi tạo dữ liệu. Command của thành viên vẫn cần review, vì kiểm đường dẫn khai báo không phải sandbox cho mọi hành vi của command.
- Dùng chung validate_panel_split trong các bước tạo/đọc manifest, chọn dev/val, claim test và công cụ intake. Kiểm fingerprint, biên và metadata chia mẫu trước khi dùng, giữ cờ/khóa test hiện có.
- Kiểm config venues hợp lệ và đủ/đúng trong sample ở cả raw/historical; không ghi dữ liệu khi thiếu/thừa venue.
- Các phần nền/schema/fixtures/templates/đồng bộ/lead-lag và bản nháp phương pháp có thể tiếp tục bảo trì mà không chờ mô hình thành viên khác.

Chưa thể đánh dấu toàn bộ TV4 hoặc M2/M3 hoàn tất bằng fixtures. Phần đang chờ có đầu vào và chủ sở hữu cụ thể như trên; không tự viết thay mô hình TV5/TV6 để đóng checklist.
