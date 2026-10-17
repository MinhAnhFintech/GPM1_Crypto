# Quy trình đóng góp

Anh (TV4) phụ trách schema, thời gian, fixtures và tích hợp. `dev` là nhánh tích hợp trong quá trình làm; `main` để trống làm nhánh phát hành cho đến khi dự án hoàn tất. Mỗi thành viên làm trên nhánh riêng tạo từ `dev`, rồi mở pull request (PR) vào `dev`. Anh review và merge PR sau khi kiểm tra. Chỉ Anh mở PR phát hành từ `dev` vào `main` khi dự án đã hoàn tất. Không push trực tiếp lên `dev` hoặc `main`, và không force-push các nhánh dùng chung.

Trước khi bắt đầu một nhiệm vụ, đồng bộ `dev` rồi tạo nhánh riêng có tên gồm vai trò và mã việc, ví dụ `feat/tv2-c2-02-binance-vision`:

```powershell
git switch dev
git pull --ff-only origin dev
git switch -c feat/tv2-c2-02-binance-vision
```

Trước khi mở PR vào `dev`, chạy các kiểm tra và push nhánh nhiệm vụ của mình:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python run_all.py --mode synthetic
git push -u origin feat/tv2-c2-02-binance-vision
```

Trên GitHub, đặt nhánh đích (base) của PR là `dev`. Nếu PR cần sửa schema, phải cập nhật DATA_CONTRACT, validator và fixtures trong cùng PR.

Sau khi TV5 bàn giao ba module vecm/info_share/info_leadership, kiểm chứng bắt buộc:

```powershell
python -m pytest tests/test_info_share_synthetic.py --require-tv5 -q
```

Không nghiệm thu thay đổi dùng dữ liệu tương lai, nội suy giá thiếu, trộn synthetic/historical/live, mất hậu tố venue, ghép spot/perp để arbitrage hoặc dùng final test để chọn tham số.

TV1: config, regime flags và README. TV2: Binance Vision, features, dashboard và hình. TV3: CCXT/live/funding/OI, evaluation. TV4: synchronization/utils/leadlag, templates và orchestrator. TV5: cointegration và IS/CS/ILS. TV6: cost_model/backtest/sensitivity. Khi cần sửa liên quan, ghi rõ lý do tích hợp và thêm test tái hiện lỗi.

Không commit data/raw, data/interim, data/processed, môi trường Python hoặc thông tin truy cập. Fixtures nhỏ được commit. Dữ liệu thật bàn giao qua ổ chung kèm SHA-256. Mọi số liệu/hình trong báo cáo lấy từ outputs sau mốc đóng băng; outputs/synthetic chỉ là kiểm thử.

CI chạy pytest và demo synthetic. Hai ca TV5 có thể skip khi chưa bàn giao, nhưng skip không là bằng chứng IS/ILS đúng và không đủ để đóng C4.13. Không merge kết quả mô hình cuối nếu chưa chạy strict check ở trên.
