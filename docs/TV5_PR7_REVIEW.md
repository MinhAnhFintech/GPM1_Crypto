# Review TV5 — PR #7, ngày 19/10/2026

PR: https://github.com/MinhAnhFintech/GPM1_Crypto/pull/7. Head được review: fef42fd387fc86aa53226b2f1155d0fa691ea5e7; dev: 59df287ed80da91b17c42b542da0eed4dcc002a7. Đối chiếu GPM1_CRYPTO.xlsx hàng 45–53 (C5.01–C5.09), hàng 44 (C4.13) và PDF mục 2 về chữ ký hàm, mục 3.3 về nghiệm thu. PR ban đầu trỏ main; đã chuyển base sang dev, main giữ nguyên. Bản kết hợp và sửa được kiểm tra riêng trong tmp, không dùng dữ liệu thật hoặc claim final test.

## Phạm vi đã nhận

| Mã | Kết luận về code |
| --- | --- |
| C5.01 | Có ADF/BIC, KPSS, giả thuyết gốc và số quan sát. Sửa bảng T2 để luôn có cả level/diff1, kể cả khi level đã dừng; ghi cả độ trễ/bandwidth KPSS |
| C5.02 | Có lựa chọn số trễ và Johansen trace/max-eigen kèm critical values, không gọi critical values là p-value |
| C5.03 | Có VECM, whiteness/stability; rank nghiên cứu lấy từ Johansen, không tự ép n−1 khi không được ủng hộ |
| C5.04 | Có IS qua mọi thứ tự (hoặc mẫu với seed/số lượng), CS có dấu, không clip vào [0,1] |
| C5.05 | Có ILS theo từng cặp và bảng IS/CS/ILS cạnh nhau; mean ILS đa venue là tổng hợp mô tả, không coi là một vector share cộng bằng 1 |
| C5.06 | Có chọn/giữ đặc tả trên dev và tái dùng cho val. Chưa nghiệm thu chạy/khóa đặc tả trên mẫu thật vì chưa có D1/D3 thật |
| C5.07 | Có refit rolling và status/reason. Sửa available_at, giữ khoảng thời gian khi có missing; thêm public API rolling_price_discovery theo PDF |
| C5.08 | Có sensitivity quanh baseline trên dev+val, không mở final test; kết quả nghiên cứu thật chưa có |
| C5.09 | Có RQ3 theo cặp spot/perp cùng sàn, cùng mẫu, bounds chồng nhau không kết luận leader; thêm public API spot_perp_discovery theo PDF |

## Findings và sửa tích hợp

1. CI gốc: 239 passed, 2 failed trên PR. Hai lỗi ở test C4.13 của TV4: buộc CS chọn leader trên fixtures nhiễu rất thấp. Công thức CS của TV5 khớp trực giao alpha; CS âm hoặc trên 1 là hợp lệ. Trên fixtures gốc, IS và ILS đều thu lại leader. Test nay giữ hai ca gốc, kiểm CS hữu hạn/tổng bằng 1, và thêm hai benchmark noise-follower = 0.6 × innovation_sigma để kiểm cả CS thu lại leader. Không sửa fixtures/config gốc, không ép kết quả share hoặc bỏ qua các hàm thật.
2. Bỏ dòng thiếu bên trong mẫu làm bước 2 phút thành một độ trễ quan sát. Sửa log_prices chỉ cho trim mép thiếu trong ngưỡng đã chọn; khoảng trống bên trong bị từ chối, rolling ghi cửa sổ failed với lý do. Không nội suy hoặc nối thời gian qua gap. Trước mẫu thật, nếu nhiều khoảng trống cần nhóm chọn quy trình xử lý thống kê rõ ràng; không âm thầm bỏ dòng.
3. Đặc tả được chọn bằng cả development nhưng rolling development ghi available_at trước thời điểm chọn. Sửa lưu selected_available_at và đặt available_at = max(window_end, selected_available_at). Cửa sổ development là ước lượng nhìn lại, không giả làm tín hiệu khả dụng sớm hơn. Định dạng spec tăng v3; spec cũ cần thư mục đầu ra mới, không xóa lịch sử khóa.
4. Runner độc lập chỉ chặn synthetic ghi đúng outputs/, còn bỏ lọt thư mục con nghiên cứu. Sửa dùng chung kiểm tra đường dẫn của TV4 trước khi tạo bảng; không thay collector hoặc danh sách sàn.
5. Bảng stationarity dừng sớm ở I(0) bỏ mất kiểm định diff1; đã bổ sung. Hai public API PDF còn thiếu được bổ sung dưới dạng adapter dùng cùng hàm thực, rolling yêu cầu spec đã chọn trên dev.

## Đối chiếu tài liệu gốc và giới hạn

Đã đọc accepted manuscript của Putniņš (2013), trang 14–15, phương trình (4), (7), (8), (9): CS từ trực giao alpha; IS qua Cholesky và trung bình hai thứ tự cho cặp; IL dùng tỷ số IS và CS, ILS chuẩn hóa theo tổng IL. Công thức hiện có khớp các biểu thức này. ILS là phép đo bivariate; dùng IS midpoint có giới hạn khi sai số tương quan, không được gọi là thước đo chính xác cho mọi cơ chế giá.

Nguồn: https://opus.lib.uts.edu.au/bitstream/10453/27707/3/Price%20discovery%20metric%20simulations%2020130506%20FINAL.pdf (đối chiếu 19/10/2026); DOI 10.1016/j.jempfin.2013.05.004. Tham khảo VECM: https://www.statsmodels.org/stable/generated/statsmodels.tsa.vector_ar.vecm.VECM.html.

## Bằng chứng kiểm thử

- Các test riêng của PR TV5 trước sửa: 58 passed. Cả PR: 239 passed, 2 failed tại CS trong C4.13.
- Bản kết hợp với dev và sửa: `python -m pytest -q --require-tv5`: **330 passed, 1 skipped** (symlink trên Windows). Không còn skip TV5.
- Strict C4.13 gồm original/identified_cs × multi/spot_perp; gọi fit_vecm, hasbrouck_is, component_share và information_leadership thật.
- Demo nền multi và runner TV5 trên fixtures gốc: 720 dòng; 6 cửa sổ rolling, 0 failed; xuất T2/T3/T3c/T3d/D6/T3b trong outputs/synthetic/multi. Spot_perp cũng dựng 720 dòng và chạy 8 cửa sổ rolling, 0 failed, trong outputs/synthetic/spot_perp.

Các số liệu này chỉ là bằng chứng kỹ thuật synthetic. Không đóng C5.06–C5.09 trên mẫu thật, không sửa trạng thái Excel. C4.12 vẫn chờ các stage khác của nhóm; collector live vẫn thu 5 sàn/8 venue theo quyết định nhóm.
