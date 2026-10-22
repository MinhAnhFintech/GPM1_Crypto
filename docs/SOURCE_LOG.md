# SOURCE LOG

Tài liệu này ghi nhận các thư viện và repository bên ngoài được sử dụng hoặc tham khảo trong dự án, bao gồm nguồn, phiên bản hoặc commit tham khảo, giấy phép, ngày ghi nhận và phạm vi sử dụng.

## 1. CCXT
- Repository: https://github.com/ccxt/ccxt.git
- Phiên bản sử dụng: 4.5.88
- Giấy phép: MIT License
- Ngày ghi nhận: 2026-10-19

### Phạm vi sử dụng
CCXT được sử dụng như một dependency của dự án để hỗ trợ truy cập dữ liệu thị trường từ các sàn giao dịch thông qua giao diện thống nhất.
Trong dự án, CCXT được sử dụng cho các tác vụ:
- truy cập thông tin thị trường của các sàn;
- thu thập và kiểm tra dữ liệu OHLCV;
- truy cập bid, ask và order book phục vụ dữ liệu live;
- truy cập dữ liệu thị trường spot và perpetual;
- thu thập hoặc kiểm tra funding rate và open interest khi sàn hỗ trợ.
Nhóm sử dụng CCXT thông qua thư viện được cài đặt trong môi trường Python và không sao chép mã nguồn nội bộ của CCXT vào repository của dự án.

## 2. Crypto-Statistical-Arbitrage-Lab
- Repository: https://github.com/JoyNjoroge/Crypto-Statistical-Arbitrage-Lab.git
- Nhánh tham khảo: main
- Commit tham khảo: 74d9c1a6b76edfc516cf93251b1f11ff8695b6e2
- Giấy phép: MIT License
- Ngày ghi nhận: 2026-10-19

### Phạm vi tham khảo
Repository Crypto-Statistical-Arbitrage-Lab được sử dụng làm nguồn tham khảo về phương pháp và cách tổ chức quy trình nghiên cứu.
Nhóm tham khảo các phương pháp và nguyên tắc sau:
- ước lượng tham số trên tập huấn luyện và đóng băng tham số trước khi đánh giá out-of-sample nhằm hạn chế look-ahead bias;
- mô hình hóa kết quả từ gross sang net sau khi xét chi phí giao dịch;
- thực hiện phân tích độ nhạy đối với chi phí giao dịch;
- sử dụng checksum để kiểm tra tính toàn vẹn của dữ liệu tải về;
- tham khảo cách tổ chức kiểm thử và quy trình nghiên cứu có khả năng chạy lại;
- tham khảo các kiểm tra robustness như bootstrap và walk-forward.
Repository này chỉ được sử dụng để tham khảo phương pháp và quy trình nghiên cứu. Nhóm không sao chép trực tiếp logic statistical arbitrage một sàn vào dự án.
Các nội dung đặc thù của đề tài như đồng bộ dữ liệu giữa nhiều sàn, price discovery, VECM, Information Share, Component Share, phân tích spot–perpetual và cross-exchange arbitrage được triển khai theo thiết kế riêng của nhóm.

## 3. Nguyên tắc sử dụng nguồn bên ngoài

1. Ghi nhận nguồn, phiên bản hoặc commit tham khảo và ngày ghi nhận đối với các nguồn bên ngoài được sử dụng trong dự án.
2. Tuân thủ giấy phép của từng thư viện và repository.
3. Crypto-Statistical-Arbitrage-Lab chỉ được sử dụng để tham khảo phương pháp và quy trình nghiên cứu; không sao chép trực tiếp logic statistical arbitrage một sàn.
4. Các phần triển khai của dự án phải tuân theo Data Contract và quy tắc chống look-ahead của nhóm.
5. Khi phiên bản dependency hoặc nguồn tham khảo thay đổi, SOURCE_LOG phải được cập nhật tương ứng.
