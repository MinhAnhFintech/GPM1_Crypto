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

## Còn chờ để chạy nghiên cứu thật

| Stage / việc | Đầu vào còn thiếu |
| --- | --- |
| features — TV2 C2.04 | CLI/D3b và kiểm volume BTC đã sửa trong D1 A162; không lấy bản volume OKX cũ |
| regime_flags — TV1 C1.04/C1.06 | CLI/D5, ngưỡng fit development, nguồn sự kiện và xác nhận khoảng mẫu |
| price_discovery — TV5 | Review đặc tả, spec chọn trên development và nghiệm thu trên D3 thật; profile này chỉ kiểm tích hợp synthetic |
| cost_backtest — TV6 + TV3 | CLI backtest A/B và D7/D8, D2 live cùng coverage, half-spread proxy được hiệu chỉnh từ dữ liệu |
| evaluation — TV3/TV6 | CLI hồi quy/stress/sensitivity và đầu ra theo PCCV; D5/D9 khi mô hình cần |

D3/D4 thật A162 đã có local, không phải đầu vào đang chờ của TV4. Raw/code tái tạo D1 đã sửa và hai cờ Bybit spot vẫn cần Ny/TV3 đối chiếu riêng; không chặn tái lập nền từ D1 đã xác nhận hash. Không tự chạy mô hình trên final test hoặc thay dữ liệu thiếu bằng fixtures.
