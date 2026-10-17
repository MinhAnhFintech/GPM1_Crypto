# Mẫu bàn giao của TV4

Từ gốc repo, chạy `python run_all.py --mode synthetic` để sinh fixtures và thử hàm vẽ, lead–lag và OLS-HAC. Các file minh họa nằm trong outputs/synthetic; không dùng các con số này làm kết quả nghiên cứu.

- `plot_template.py`: plot_prices cho F1, plot_leadlag cho F6. Đầu vào giữ lưới có NaN; spot/perp khác kiểu đường.
- `regression_template.py`: fit_ols_hac(frame, y_col, x_cols, maxlags). Thay tên cột và nguồn đầu vào bằng D6/features thật cho T4. Dữ liệu phải theo thời gian; demo chỉ hồi quy returns trên returns trễ và volume để kiểm khung. Không coi demo là T4 hoặc tự ghép panel nhiều venue cho HAC một chuỗi.
- `streamlit_template.py`: đủ bảy khu vực và nhãn provenance/market_type; phần IS và arbitrage chưa có nguồn sẽ ghi chờ. Chạy bằng `python -m streamlit run notebooks/templates/streamlit_template.py`.

Các mẫu không chứa số phí, IS, PnL hoặc tín hiệu arbitrage bịa để lấp chỗ trống. TV2 nối D2/D6/D7 thật khi nhận bàn giao; TV3 thay mô hình demo bằng đặc tả T4 đã chốt.
