# Quy trình đóng góp

Anh (TV4) phụ trách schema, thời gian, fixtures và tích hợp. Mỗi việc tạo nhánh `feat/tvX-ten-viec` từ dev và PR vào dev. Main chỉ nhận dev tại các mốc M2, M4, M6. Review trong 24 giờ sau khi có PR; không tự đánh dấu việc chưa bàn giao là Xong.

Trước khi mở PR:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python run_all.py --mode synthetic
```

Sau khi TV5 bàn giao ba module vecm/info_share/info_leadership, kiểm chứng bắt buộc:

```powershell
python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q
```

PR sửa schema phải cập nhật DATA_CONTRACT, validator và fixtures trong cùng PR. Từ chối PR dùng dữ liệu tương lai, nội suy giá thiếu, trộn synthetic/historical/live, mất hậu tố venue, ghép spot/perp để arbitrage hoặc dùng final test để chọn tham số.

TV1: config, regime flags và README. TV2: Binance Vision, features, dashboard và hình. TV3: CCXT/live/funding/OI, evaluation. TV4: synchronization/utils/leadlag, templates và orchestrator. TV5: cointegration và IS/CS/ILS. TV6: cost_model/backtest/sensitivity. Khi cần sửa liên quan, ghi rõ lý do tích hợp và thêm test tái hiện lỗi.

Không commit data/raw, data/interim, data/processed, môi trường Python hoặc thông tin truy cập. Fixtures nhỏ được commit. Dữ liệu thật bàn giao qua ổ chung kèm SHA-256. Mọi số liệu/hình trong báo cáo lấy từ outputs sau mốc đóng băng; outputs/synthetic chỉ là kiểm thử.

CI chạy pytest và demo synthetic. Hai ca TV5 có thể skip khi chưa bàn giao, nhưng skip không là bằng chứng IS/ILS đúng và không đủ để đóng C4.13. Không merge kết quả mô hình cuối nếu chưa chạy strict check ở trên.
