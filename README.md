# Camera TTC — Cảnh báo nguy cơ va chạm phía trước

Ứng dụng Python: **video camera phía trước → YOLO11n → ByteTrack → ước lượng TTC → cảnh báo**.
Phát hiện ô tô, xe máy, xe buýt và xe tải. Vùng đường xe đang đi được chọn bằng
đa giác; không cần huấn luyện mô hình mới.

## Chạy ngay trong workspace

Môi trường Windows và trọng số YOLO đã có sẵn:

```powershell
.\.venv-win\Scripts\python.exe tools/run_demo.py
```

Lệnh này chuẩn bị dữ liệu mẫu, chạy ba kịch bản **tiến gần / giữ kích thước / đi xa dần**,
đọc lại video đầu ra và kiểm tra cảnh báo. Kết quả:

- `outputs/ttc-sample/`: ba video đầu vào, vùng đường và dữ liệu tham chiếu.
- `outputs/ttc-demo/`: video đã chú thích, CSV, biểu đồ và thống kê cho từng kịch bản.
- `outputs/ttc-demo/validation.json`: báo cáo PASS/FAIL của cả ba kịch bản.

Các clip mẫu giả lập độ lớn ảnh từ [ảnh bus mẫu Ultralytics](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/assets/bus.jpg).
YOLO–ByteTrack vẫn chạy thật; đây là kiểm chứng pipeline, chưa phải đánh giá trên đường.
Mỗi clip dài 5 giây, 75 frame, 960×720; TTC lý tưởng của clip tiến gần là `6 - t` giây.
Lần đầu cần mạng để tải ảnh mẫu/trọng số nếu chưa có; những lần sau dùng lại file đã tải.

## Dùng video của bạn

```powershell
.\.venv-win\Scripts\python.exe -m collision_warning --source dashcam.mp4 --output outputs/ttc.mp4 --preview
```

Click ít nhất ba đỉnh bao quanh vùng đường phía trước trên khung hình đầu tiên.
**Enter** xác nhận, **R** chọn lại, **Q/Esc** hủy. Không lặp đỉnh đầu hoặc vẽ đa giác tự cắt.
Trong cửa sổ preview, **Q/Esc** dừng và xuất phần đã xử lý. Bỏ `--preview` nếu chỉ cần xuất file.
Ứng dụng xử lý từng frame theo thứ tự, không bỏ frame để cố chạy theo thời gian thực.

Máy không có GUI: truyền file vùng và không bật preview:

```bash
python -m collision_warning --source dashcam.mp4 --zone zone.json --output outputs/ttc.mp4
```

File JSON chứa tọa độ pixel của video gốc, ví dụ cho ảnh 960×720:

```json
[[400, 260], [560, 260], [900, 700], [60, 700]]
```

Chọn lại tọa độ cho video thực tế. Chỉ xe có điểm giữa cạnh dưới bounding box nằm
trong đa giác mới được xét TTC. Camera có thể chuyển động cùng xe, nhưng cần gắn
cố định; ưu tiên đường thẳng, không zoom, không rung mạnh. MP4 cần kích thước chẵn.

## Cài mới

Python 3.10 trở lên. Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/run_demo.py
```

WSL/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python tools/run_demo.py
```

Nếu dùng GPU, cài bản PyTorch phù hợp GPU trước rồi cài requirements; thêm `--device 0`.
`--model /path/to/model.pt` cho phép dùng trọng số cục bộ; model phải có class tên
`car`, `motorcycle`, `bus` hoặc `truck`. GUI cần OpenCV HighGUI và display;
WSL không có display phải dùng `--zone` và bỏ `--preview`.

## Phương pháp TTC và các trạng thái

Với camera pinhole, `w = f·W/Z`. Nếu kích thước/hướng xe ổn định và vận tốc tiếp cận
không đổi, `q = 1/w` tuyến tính theo thời gian. Hồi quy khoảng một giây lịch sử để tính:

```text
q(t) = a + b·t
TTC = -q_hiện_tại / b       khi b < 0 và độ giãn ảnh đủ rõ
```

Đây là dạng tương đương của `TTC ≈ w / (dw/dt)`, dùng hồi quy để giảm nhiễu.
TTC là ước lượng thời gian tới tiếp xúc theo trục nhìn nếu chuyển động tiếp tục;
không đo khoảng cách mét hay vận tốc tuyệt đối. Vùng 2D chưa thay thế lane detection
hoặc dự báo đường đi khi đánh lái.

| Trạng thái | Ý nghĩa |
|---|---|
| `WARMUP` | Chưa đủ ít nhất ba quan sát và một giây lịch sử |
| `OUTSIDE_ZONE` | Xe ngoài vùng đường đã chọn |
| `NOT_CLOSING` | Không có tín hiệu giãn ảnh dương đủ rõ |
| `UNRELIABLE` | Box bị cắt, đổi góc/kích thước đột ngột hoặc hồi quy nhiễu |
| `CLEAR` | TTC lớn hơn ngưỡng cảnh báo; không đồng nghĩa chắc chắn an toàn |
| `PENDING` | Đang xác nhận cảnh báo qua nhiều frame |
| `WARNING` | TTC ≤ 3 giây, xác nhận đủ frame; màu cam |
| `CRITICAL` | TTC ≤ 1,5 giây, xác nhận đủ frame; màu đỏ |
| `UNTRACKED` | Có detection nhưng chưa có ID và TTC |

Ngưỡng 3/1,5 giây là cấu hình demo. Cảnh báo cần ba frame liên tiếp;
giữ `WARNING` trong lúc xác nhận tăng lên `CRITICAL`. Khi mất track hoặc chất lượng
không đạt, xóa cảnh báo; track xuất hiện lại cần tích lũy lịch sử lại.
Dashboard đánh dấu `*` ở xe có TTC khả dụng nhỏ nhất trong vùng.

Tham số chính: `--warning-ttc 3`, `--critical-ttc 1.5`, `--history 1`,
`--confirm-frames 3`, `--min-expansion 0.02`, `--imgsz 640`, `--conf 0.1`.
Dùng `--fps 25` chỉ khi FPS metadata sai/thiếu. Tốc độ giãn ảnh tối thiểu mặc định
2%/giây; box đổi rộng trên 35% giữa hai frame làm reset lịch sử. Tỷ lệ rộng/cao đổi
trên 25% trong cửa sổ hoặc sai số hồi quy tương đối RMS trên 4% làm ngừng xuất TTC.

## Kết quả và kiểm chứng

Mỗi lần chạy xuất một bộ kết quả cùng tên:

| File | Nội dung |
|---|---|
| `.mp4` | Box, ID, loại xe, TTC và cảnh báo |
| `.csv` | Quan sát từng frame, chất lượng, trạng thái và lý do |
| `.summary.json` | Cấu hình, số frame, FPS xử lý và mốc cảnh báo |
| `.zone.json` | Vùng đường để dùng lại với một tên output khác |
| `.ttc.svg` | Biểu đồ TTC; ngắt khi mất dữ liệu hoặc đổi ID |

TTC rỗng nghĩa là không có ước lượng, không phải 0 giây. Biểu đồ giới hạn hiển thị
ở tối thiểu 6 giây; CSV giữ nguyên giá trị. FPS gồm inference, xử lý, vẽ và encode,
không gồm setup/đọc video. Kết quả được ghi tạm và chỉ công bố sau khi xử lý hoàn tất;
lỗi inference hoặc hủy bằng Ctrl+C giữ bộ kết quả hoàn chỉnh của lần chạy trước.

```powershell
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-win\Scripts\python.exe -m pip check
```

Kiểm thử gồm TTC tham chiếu, xe ngoài vùng, giữ khoảng cách/đi xa dần, nhiễu,
đổi góc, cắt mép ảnh, mất track, xác nhận cảnh báo, các FPS khác nhau, biểu đồ và
bảo toàn kết quả khi một lần chạy bị lỗi. `tools/run_demo.py` kiểm tra cả ba clip
qua YOLO–ByteTrack và đọc lại đủ frame video đầu ra.

Đánh giá thực tế cần video dashcam tiến gần xe trước, giữ khoảng cách, đi xa dần
và xe làn bên. Chỉ công bố sai số TTC khi có dữ liệu tham chiếu đồng bộ từ cảm biến
hoặc mô phỏng có ground truth. Rẽ, đường dốc, camera rung/zoom, che khuất và thay đổi
hướng xe có thể làm sai TTC. Bản hiện tại chưa có radar/depth fusion hay điều khiển phanh.
