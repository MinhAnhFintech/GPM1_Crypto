# C4.12 — tích hợp CLI đã nhận (21/10/2026)

Đã có profile riêng `config/pipeline.synthetic.example.yaml`: `run_all.py` dựng D1 → D3/D4 rồi gọi CLI thật `src.price_discovery.run_price_discovery` của TV5, kiểm đủ 11 đầu ra phải tạo lại và lưu SHA-256 trong pipeline_report. Spec T2_vecm_spec.json được tạo lần đầu và giữ khóa ở các lần sau, không khai báo là đầu ra phải ghi mới. Không dùng command giả để đánh dấu stage đạt. Profile chỉ dành cho fixtures 720 dòng, rolling 240/bước 60; không thay config nghiên cứu 1440/bước 60.

## Tái lập bằng một lệnh

Từ gốc repo, trong môi trường Python đúng requirements.txt:

```powershell
python run_all.py --mode synthetic --config config/pipeline.synthetic.example.yaml --fixture-case multi --data-dir outputs/synthetic/integrated/multi/data --output-dir outputs/synthetic/integrated/multi
```

Cặp spot/perp dùng cùng profile:

```powershell
python run_all.py --mode synthetic --config config/pipeline.synthetic.example.yaml --fixture-case spot_perp --data-dir outputs/synthetic/integrated/spot_perp/data --output-dir outputs/synthetic/integrated/spot_perp
```

D1/D2/D3 fixtures gốc được sinh bằng cùng seed/DGP như trước. Dữ liệu và kết quả demo nằm ở vùng synthetic đã ignore. Bảng T2/T3/D6 là kết quả mô hình trên fixtures, không thay số liệu báo cáo. D4 của từng bộ nằm trong data/ của bộ đó, test_consumed=false và không có khóa test cuối.

Profile xuất T2 kiểm định/spec, T3 IS/CS/diagnostics, T3d ILS, T3c spot/perp, T3b sensitivity và D6. Profile tổng hợp cần tách khỏi spec riêng đã khóa nếu thay dữ liệu/cấu hình; runner TV5 từ chối dùng spec có fingerprint khác. Không chép T2_vecm_spec.json giữa bộ multi và spot_perp.

Lượt thử tái lập đã phát hiện khai báo spec như output mới làm lần chạy thứ hai bị từ chối stale output: TV5 cố ý giữ nguyên spec đã khóa. Đã sửa khai báo profile, giữ nguyên cơ chế chặn file cũ của orchestrator và cơ chế khóa spec của TV5; không ghi lại/touch spec để né kiểm tra.

Kiểm thực tế: multi và spot_perp đều 720 dòng, sáu cửa sổ, zero failed. Chạy lặp multi khớp fingerprint, hash cả 11 output và spec. Ca --foundation-only không gọi TV5 và không tạo D6. Dashboard đọc được D6 do CLI TV5 sinh cho cả hai bộ. Hash D1/D3/D4 lịch sử, pipeline_report nghiên cứu và config nghiên cứu không thay đổi trước/sau; bằng chứng ở outputs/tv4_pipeline_integration_validation.json.

Biên nhận D3 hiện tại nằm trong current_historical_handoff của JSON trên. Bytes Parquet local đã khác biên nhận lần trước ngay từ trước lượt kiểm này; đối chiếu chính xác với bản ứng viên cũ xác nhận toàn bộ giá trị, dtype, index, attrs và fingerprint đều giữ nguyên. Giữ biên nhận cũ làm bằng chứng lịch sử; dùng checksum trong biên nhận hiện tại khi đối chiếu file local. Lượt này không ghi lại D3 hoặc thay manifest D4.

## Đọc trạng thái đúng phạm vi

Kết quả mong đợi: foundation_status=passed, stage price_discovery=passed; pipeline_status=pending_team_stages. Bốn stage còn thiếu phải vẫn xuất hiện: features, regime_flags, cost_backtest, evaluation. full_research_pipeline_complete luôn false khi chạy synthetic. Chạy thành công không đóng M3 hoặc toàn C4.12, không kiểm định kết quả nghiên cứu thật.

Khi chỉ cần dựng nền, thêm `--foundation-only`: CLI TV5 không được gọi, stage ghi not_run/foundation_only. Không dùng profile này với --mode historical/raw; sample=null là chặn thiếu cấu hình nghiên cứu có chủ đích.

## Pipeline nghiên cứu thật (26/10/2026)

`config/config.yaml` đăng ký 19 stage bằng CLI thật đã merge của các thành viên, theo thứ tự phụ thuộc. Năm tên bắt buộc là các bước chốt của từng nhóm:

| Nhóm | Stage (thứ tự chạy) | Đầu ra |
| --- | --- | --- |
| TV2 C2.04 | features | D3b `features_1m.parquet` |
| TV1 C1.06 | regime_flags | D5 `regime_flags.parquet` + receipt, ngưỡng high-vol fit trên dev |
| TV5 C5.05–C5.09 | price_discovery | T2 stationarity/Johansen, T3, T3c, T3d, D6 `rolling_is.parquet`, T3b; spec khóa trên dev |
| TV3 C3.07 (đầu vào B) | price_discovery_live | `live_session_is.parquet` |
| TV6 C6.03–C6.10 | cost_gap_events_live, cost_gap_events_historical, cost_backtest_a, cost_backtest_b, cost_backtest (T5), cost_reconcile (T5c), cost_regime (T6b), cost_figures (F4/F5) | D7 live + historical, D8, bảng chi phí, F4/F5 |
| TV3 C3.07/C3.08 | evaluation_liquidity (T4), evaluation (T6) | T4, T6 |
| TV1 C1.07 | exchange_characteristics | T1 (volume trên dev + val) |
| TV2 C2.05/C2.06 | figures_f1_f2, figures_f3, figures_f6, figures_f7 | F1, F2, F3, F6, F7 |

Đầu vào bàn giao mà pipeline không dựng lại giữ ở đường dẫn hợp đồng: D1 `data/interim/ohlcv_1m_long.parquet`, D2 và D9 trong `data/processed`. `stress_regime` chỉ nhận project root nên `{data_dir}` phải là `{output_dir}/../data/processed` (đúng với mặc định). Không đăng ký T5b: C6.07 đã dùng final test một lần (`test_consumed=true`); bảng T5b giữ nguyên bản đã nhập kèm hash.

Lệnh tái lập cô lập (không ghi đè outputs/ đã đóng băng):

```powershell
python run_all.py --mode historical --config config/config.yaml --data-dir tmp/c412-research/data/processed --output-dir tmp/c412-research/outputs
```

Kết quả ngày 26/10 (runtime đúng requirements.txt): foundation passed, fingerprint `9420d92f…56a4`, 19/19 stage passed, `full_research_pipeline_complete=true`, final test không bị đụng; khoảng 67 phút, trong đó price_discovery 64 phút với 6 worker. Đối chiếu với bàn giao:

- Trùng từng byte: D7 live/historical, ledger A/B, half-spread, T5, T5c, T6b, T4, F1–F5, F7, T2_leadlag_xcorr.
- Trùng trong sai số dấu phẩy động: D6 (IS/ILS ≤ 2,6e-10; CS sai số tương đối ≤ 8,8e-8), T6 (≤ 6,3e-12), các bảng T2/T3 TV5 giao qua ổ chung (tương đối ≤ 1,7e-7). Spec chọn lại trên dev trùng spec đã khóa.
- JSON params/sources chỉ khác đường dẫn máy và hash bytes Parquet.
- Khác: T1 bàn giao 23/10 dùng D2 cũ tới 22/10 S2 (thiếu hai phiên 23/10); F6 commit có chú thích cũ «Sample:», số liệu như nhau.

Bằng chứng: `outputs/tv4_research_pipeline_validation.json`. Bảng T2/T3/T3b/T3c/T3d chưa từng được commit vào `outputs/tables`; nhóm cần chốt có đưa bản tái lập (hoặc bản TV5 giao) vào outputs cho báo cáo. Raw/code tái tạo D1 đã sửa và hai cờ Bybit spot vẫn là việc riêng của Ny/TV3; không chặn pipeline từ D1 đã xác nhận hash.
