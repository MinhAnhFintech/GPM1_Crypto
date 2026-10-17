# Review tech lead: phần đã làm của TV2 và TV3

Người thực hiện: Anh (TV4), ngày 17/10/2026. Kiểm tra lịch sử và nội dung hai nhánh đang có trong checkout; không suy ra đã hoàn thành chỉ từ tên commit. Các kết quả C3.01 cũ vẫn giữ nguyên để truy vết, không thay bằng dữ liệu fixture hoặc tự ghi ngày xác minh mới.

## TV2 – Ny

Nhánh `feat/tv2-binance-vision`, commit `c615f80`: thêm src/collectors/historical/binance_vision.py, ứng với C2.01. Code tải ZIP tháng từ Binance Vision, đối chiếu checksum file mới, đọc CSV có/không header, xử lý timestamp mili/micro giây và ghi Parquet. Commit tự ghi đã thử tháng 2026-09; còn thiếu tải đủ khoảng mẫu, đọc config và C2.02. Trong repo chưa có bằng chứng D1 đầy đủ, features, dashboard hoặc hình của TV2.

Nhánh này chưa vào dev khi bắt đầu review. Dev đã có commit mới của TV3, nên hai nhánh không fast-forward được. Đã merge cục bộ bằng commit `40fbbf9`, giữ lịch sử cả hai. Merge đó không bao gồm các thay đổi tiếp theo của TV4 cho tới khi TV4 commit riêng.

| Phát hiện | Ảnh hưởng | Sửa trong phần TV4 |
| --- | --- | --- |
| ZIP cache được tin ngay, không kiểm lại checksum | Cache hỏng có thể đi vào dữ liệu hoặc gây lỗi không rõ | Lưu checksum sidecar, tính lại SHA-256 mỗi lần dùng cache; test sửa byte cache phải bị chặn |
| Start/end bắt buộc CLI, không đọc config | Không cùng khoảng mẫu của nhóm | Cho CLI override hoặc lấy sample.start/end từ config; end loại trừ |
| Khoảng tháng đảo ngược và tháng chưa đóng chưa kiểm tra | Concat rỗng hoặc đòi archive chưa có | Kiểm YYYY-MM có thứ tự; chỉ nhận tháng đã đóng theo UTC |
| Ghi Parquet trực tiếp | Có thể để lại file đích dang dở | Ghi file tạm rồi thay nguyên tử |
| Raw có open_time và close_time của Binance | Nguy cơ downstream dùng close_time/open_time sai khả dụng | Thêm adapter TV4 tạo ts_available=open_time+1 phút; có regression test |

Raw vẫn là raw; không gắn nhãn rằng C2.02/C2.03 đã hoàn thành cho toàn mẫu. TV2 tiếp tục kiểm nến thiếu, trùng, giá bất thường và bàn giao D1 đầy đủ.

## TV3 – Ly

Hai commit `8a09335` và `f0e5c8b`, nhánh `feat/tv3-collector-live`; đã có trên dev lúc bắt đầu. Có script C3.01 cùng MD/CSV/JSON kết quả, collector live theo phiên, cấu hình spot/perp, retry/backoff và thống kê coverage. Chưa có module tải lịch sử CCXT, funding/OI, chuẩn hóa D2 toàn mẫu, hồi quy/stress hoặc dữ liệu live trong checkout để xác nhận các việc sau.

| Phát hiện | Ảnh hưởng | Sửa trong phần TV4 |
| --- | --- | --- |
| Raw perpetual ghi symbol CCXT có `:USDT` | Không khớp symbol của hợp đồng D1/D2 | Giữ symbol kết nối trong config, dữ liệu ghi BTC/USDT |
| Mới lấy mức đầu, chưa có mid/spread/depth | Thiếu dữ liệu C3.03 và đầu vào D2 | Tính mid, spread, depth ±10bps và cờ depth_censored; không đảm bảo đủ độ sâu chỉ từ limit |
| CCXT có thể trả size perpetual theo số hợp đồng | Size/depth bị sai đơn vị, đặc biệt OKX | Dùng market.contractSize cho hợp đồng tuyến tính để đổi về BTC; inverse bị từ chối |
| Một limit chung quá nhỏ; Coinbase dùng client khác C3.01 | Thiếu depth hoặc nối khác API đã kiểm | Limit theo venue; Coinbase dùng coinbaseexchange, tên dữ liệu vẫn coinbase |
| Số file part lấy bằng len thay vì max số thứ tự | Có thể va chạm khi part cũ bị thiếu | Tiếp tục từ max part number |
| Flush lấy hàng khỏi buffer trước khi ghi thành công | Lỗi đĩa có thể làm mất buffer | Trả các hàng về buffer nếu ghi lỗi; test mô phỏng disk full |
| Ctrl+C không phát tín hiệu dừng worker | Worker có thể tiếp tục ghi sau lần flush cuối | Stop event, join worker, flush cuối; đóng client |
| Request chậm có thể chạy bù tick với nhãn quá khứ | Nhãn lưới không phản ánh nhịp lấy thực tế | Bỏ tick đã lỡ, lưu ts_recv làm thời điểm thật biết dữ liệu |
| Coverage chỉ tính first-to-last và đếm cả dòng trùng | Che mất đầu/cuối phiên, che venue không có dữ liệu | Ghi biên phiên, tính theo phần lịch đã trôi, chỉ đếm tick duy nhất đúng lưới; ghi 0 cho phiên/venue hoàn toàn mất |
| Probe phân trang có thể pass dù chưa đủ ba trang | Kết luận khả thi quá sớm | Bắt đủ PAGINATION_PAGES, có test chỉ trả một trang |
| Điều kiện perpetual chưa chặn quote khác USDT hoặc trạng thái lỗi | Có thể chọn venue không đồng nhất | Bổ sung điều kiện quote/trạng thái/geo, có test không cần mạng |

Các mức depth là snapshot và có thể thay đổi liên tục. Limit cấu hình không thay thế việc kiểm depth_censored. API công khai được kiểm theo tài liệu [Binance order book](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints), [OKX market data](https://www.okx.com/docs-v5/en/), [Bybit order book](https://bybit-exchange.github.io/docs/v5/market/orderbook); kiểm thực tế mới là bằng chứng cho snapshot cụ thể.

## Kết luận giám sát và việc còn thiếu

C3.01 hiện chỉ là bằng chứng probe tại một lần chạy. Việc funding history đủ sâu không chứng minh OI cũng đủ sâu, và probe vài nến gần mốc không chứng minh đã tải đầy đủ 90 ngày. Không chốt danh sách venue cuối chỉ từ bảng cũ; TV3 cần xác minh giới hạn chính thức và nhóm chốt sau khi xem dữ liệu.

Mẫu API thật của TV4 ngày 17/10/2026 kiểm được ba venue và được lưu riêng trong tmp/real_review. Bộ unit/integration tests kiểm các sửa lỗi; CI GitHub, độ bền collector qua nhiều phiên và toàn bộ dữ liệu nghiên cứu chưa được xác nhận. Không khởi chạy collector dài hạn trong phiên review này và không gửi tin nhắn thay người dùng cho các thành viên.

Đề nghị nhận bàn giao tiếp theo: TV2 gửi D1 cùng T0 và checksum; TV3 gửi D2, coverage theo lịch, lỗi/độ trễ, funding/OI có giới hạn lịch sử rõ. TV4 dùng các file đó để xuất panel M2 chính thức rồi kiểm lại schema trước khi tích hợp mô hình.
