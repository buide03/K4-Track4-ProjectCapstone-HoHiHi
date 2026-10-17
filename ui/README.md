# Camera TTC UI — dashboard local

Giao diện từ upstream `976a7ba`, đã nối trực tiếp với FastAPI/YOLO11s/ByteTrack trong repo. Đây là UI chính được phục vụ tại http://127.0.0.1:8765/.

Chạy từ thư mục gốc (sau khi cài `requirements.txt`):

```powershell
.\.venv-win\Scripts\python.exe -m collision_warning.web --open-browser
```

Linux: `python -m collision_warning.web` trong virtualenv đã cài dependencies. Không chạy bằng static HTTP server vì thiếu API Python.

## Chức năng

- Dashcam có sẵn, upload video tối đa 200 MB vào máy local, preview và nguồn gốc video.
- Làn tự động theo frame, xem trước frame đầu, hoặc vùng thủ công với lưu/hủy/bỏ đỉnh và kiểm tra đa giác. Preview vùng tự động không được vẽ đè lên video nguồn đang phát; chạy phân tích để xem vùng thực theo frame.
- Phân tích có tiến độ, frame thật, TTC, box/ID trên video và danh sách xe. Không hiển thị số mô phỏng khi chưa có kết quả.
- Dừng phiên, khôi phục phiên đang chạy sau reload, báo lỗi, lưu lịch sử, phát lại WebM, tua theo chart/sự kiện. Danh sách xe trên playback lấy snapshot theo timestamp; phiên cũ thiếu snapshot có thông báo.
- Tải MP4, CSV, summary JSON, vùng thủ công hoặc lịch sử làn, SVG. File lỗi hoặc phiên hủy không được cung cấp như kết quả hoàn tất.
- Báo cáo metric: Corridor IoU, Boundary MAE và coverage từ synthetic benchmark local; tài liệu/nguồn TuSimple. Không tạo điểm benchmark công khai khi chưa đo.
- Cấu hình cảnh báo cho phiên tiếp theo; responsive, navigation và thông báo lỗi.

API cùng origin, bind loopback. Upload gửi tới Python local để phân tích; không dùng cloud. Server giữ bộ kết quả và lịch sử ở `outputs/web/`. Xem [README chính](../README.md) và [metric/benchmark](../benchmarks/README.md) để biết giả định, giới hạn và protocol đo.
