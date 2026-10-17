# Camera TTC — Cảnh báo nguy cơ va chạm phía trước

Ứng dụng Python: **video camera phía trước → làn tự động → YOLO11s → ByteTrack → quan hệ với làn → TTC → cảnh báo**.
Phát hiện ô tô, xe máy, xe buýt và xe tải. Làn xe đang chạy được bám tự động theo vạch trắng/vàng, có lựa chọn đa giác thủ công; không cần huấn luyện mô hình mới.

## Tình huống được cảnh báo

| Tình huống | Cách nhận biết | Mức cảnh báo |
|---|---|---|
| Xe phía trước trong làn tiến gần | Optical TTC của **xe mục tiêu** (xe gần nhất trong làn) | `WARNING` ≤ 3 s, `CRITICAL` ≤ 1,5 s |
| Xe phía trước **phanh gấp** | TTC giảm nhanh hơn thời gian thực (> 1,3 s mỗi giây) → `BRAKING`, TTC tính lại theo gia tốc không đổi | Cảnh báo sớm hơn TTC vận tốc không đổi |
| Xe làn bên **có dấu hiệu sang làn** | Mép xe phía làn mình trôi dần vào trong (≥ 0,08 bề rộng làn/giây, dự báo vào làn trong 3 s) → `DRIFTING` | `WARNING` khi xe không quá xa |
| Xe làn bên **cắt / tạt đầu** | Đã lấn ≥ 15% làn hoặc dự báo vào làn trong 1 s khi đang di chuyển ngang → `CUT_IN`; xe này lập tức thành mục tiêu và được tính TTC | `CRITICAL` khi xe gần, `WARNING` khi xa hơn |

Xe song song giữ làn bên (`ADJACENT`) không gây cảnh báo. Xe bị cắt ở mép ảnh (đang vượt lên)
chỉ được tính là cắt làn khi đã thực sự lấn vào làn, vì box của nó lớn dần vào trong khi xe hiện ra.

Kiểm chứng sáu kịch bản nhiều xe bằng một lệnh (YOLO–ByteTrack thật, làn tự động từ vạch sơn):

```powershell
.\.venv-win\Scripts\python.exe tools/run_scene_demo.py
```

| Kịch bản | Kết quả trên máy demo |
|---|---|
| `cut_in` — xe làn bên cắt vào giữa mình và xe trước | `CUT_IN` sớm 0,53 s trước khi tâm xe vào làn; mục tiêu chuyển sang xe mới |
| `parallel_cut_in` — xe sát bên (bị cắt mép ảnh) tạt vào | `CUT_IN CRITICAL` sớm 0,13 s; không cảnh báo khi xe chỉ chạy song song |
| `braking` — xe trước phanh gấp | Cảnh báo lúc 2,53 s; TTC vận tốc không đổi chỉ cảnh báo lúc 2,80 s (`CRITICAL`: 2,53 s so với 3,47 s) |
| `lane_drift` — xe làn bên trôi dần sang | `DRIFTING` sớm 3,8 s trước khi tâm xe vào làn, sau đó `CUT_IN` |
| `sudden_cut_in` — xe gần tạt vào rồi phanh | `CUT_IN CRITICAL` sớm 0,13 s, có TTC cho xe vừa tạt vào |
| `parallel_hold` — xe song song giữ làn | Không cảnh báo |

Đây là clip tổng hợp (ảnh xe buýt dán theo phối cảnh), không phải đánh giá trên đường; kết quả lưu ở
`outputs/scene-demo/validation.json`. Thêm `--lane manual` để dùng vùng vẽ sẵn thay cho làn tự động.

## Chạy ngay trong workspace

Mở dashboard hoàn chỉnh trên trình duyệt (môi trường Windows và trọng số đã có sẵn):

```powershell
.\.venv-win\Scripts\python.exe -m collision_warning.web --open-browser
```

Địa chỉ: **http://127.0.0.1:8765**. Giữ tiến trình server đang chạy trong lúc demo.
Dashboard chỉ lắng nghe trên máy local, không cần tài khoản hoặc gửi video lên dịch vụ bên ngoài.

1. Chọn video dashcam có sẵn hoặc **Tải video của bạn** (tối đa 200 MB).
2. Giữ **Tự động · bám vạch làn** cho dashcam. Kiểm tra vùng xem trước frame đầu; khi phân tích, vùng được cập nhật theo từng frame. Có thể chọn **Thủ công** hoặc **Vẽ vùng thủ công** nếu cảnh không có vạch.
3. Điều chỉnh ngưỡng TTC, chọn độ dài đoạn (5–60 giây), nhấn **Phân tích video**.
4. Theo dõi frame đang xử lý, biểu đồ TTC, các xe và mốc cảnh báo. Có thể dừng giữa chừng.
5. Khi hoàn tất, phát lại kết quả ngay trong trình duyệt, click biểu đồ/sự kiện để tua,
   hoặc tải MP4, CSV, JSON, lịch sử làn và SVG. **Báo cáo & đánh giá** mở lại kết quả đã lưu.

Video nguồn tải lên được lưu ở `data/uploads/`; mỗi phiên lưu ở `outputs/web/<job-id>/`.
Video phát lại trên web dùng VP8/WebM; MP4 gốc vẫn có thể tải về. Lỗi và phiên bị dừng
được hiển thị rõ, không cung cấp các file chưa hoàn tất như một kết quả thành công.

## Video dashcam thực tế

```powershell
.\.venv-win\Scripts\python.exe tools/prepare_dashcam.py
```

Nguồn: repo chính thức [Udacity CarND Vehicle Detection](https://github.com/udacity/CarND-Vehicle-Detection).
Giấy phép upstream và nguồn gốc được giữ ở `data/`; video nằm ở `data/videos/`, không đưa lên Git.

| Video | Nội dung |
|---|---|
| `udacity-traffic.webm` | Đoạn dashcam 28–40 giây, 12 giây, 960×540/15 FPS; chọn mặc định vì thấy rõ xe |
| `udacity-highway.mp4` | Video cao tốc nguyên bản 50,4 giây, 1280×720/25 FPS |
| `udacity-test.mp4` | Clip thử nhanh 1,52 giây, 1280×720/25 FPS |

Đoạn 12 giây chỉ được trích và đổi kích thước/FPS, giữ tốc độ thời gian thực; không phải giả lập.
Các clip dashcam không có TTC ground truth và có thể không tạo cảnh báo. Dùng chúng để quan sát
detection, tracking, lọc vùng đường và hành vi TTC thực tế; không suy ra độ chính xác TTC từ số cảnh báo.

## Kiểm chứng bằng clip giả lập

```powershell
.\.venv-win\Scripts\python.exe tools/run_demo.py
```

Lệnh này chuẩn bị và kiểm tra ba kịch bản **tiến gần / giữ kích thước / đi xa dần**. Kết quả:

- `outputs/ttc-sample/`: ba video đầu vào, vùng đường và dữ liệu tham chiếu.
- `outputs/ttc-demo/`: video đã chú thích, CSV, biểu đồ và thống kê cho từng kịch bản.
- `outputs/ttc-demo/validation.json`: báo cáo PASS/FAIL của cả ba kịch bản.

Các clip mẫu giả lập độ lớn ảnh từ [ảnh bus mẫu Ultralytics](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/assets/bus.jpg).
YOLO–ByteTrack vẫn chạy thật; đây là kiểm chứng pipeline, chưa phải đánh giá trên đường.
Mỗi clip dài 5 giây, 75 frame, 960×720; TTC lý tưởng của clip tiến gần là `6 - t` giây.
Lần đầu cần mạng để tải ảnh mẫu/trọng số nếu chưa có; những lần sau dùng lại file đã tải.

## Cải thiện làn đường và đánh giá

Mặc định (cả dòng lệnh khi không truyền `--zone` lẫn dashboard) tìm hai biên làn theo từng frame, khớp đường cong và làm mượt. Biên làn được kéo dài thẳng về phía điểm tụ để xếp được xe phía trước ở xa vào làn. Khi vạch bị che (thường đúng lúc có xe cắt vào), dùng lại làn vừa xác nhận tối đa `--lane-memory 2` giây, sau đó dùng hành lang mặc định trước camera để TTC không bị ngắt; `--lane-fallback none` trở về cách cũ (mất làn thì ngừng TTC). Không cần cài thêm model lane.

```powershell
.\.venv-win\Scripts\python.exe -m collision_warning --source data/videos/udacity-traffic.webm --lane-mode auto --output outputs/auto.mp4
.\.venv-win\Scripts\python.exe tools/benchmark_lanes.py
```

[Metric, kết quả so sánh baseline và cách chạy benchmark TuSimple](benchmarks/README.md).
Phép thử tổng hợp đã chạy không phải độ chính xác trên đường; bộ TuSimple thực chưa được tải/chấm. Cảnh đêm/mưa, chuyển làn và camera lệch tâm cần dữ liệu và mô hình tốt hơn.

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

Chọn lại tọa độ cho video thực tế. Xe được xét TTC khi nằm trong làn (điểm giữa cạnh dưới box
trong làn đã kéo dài), đang lấn hoặc đang cắt vào làn. Camera có thể chuyển động cùng xe, nhưng cần gắn
cố định; ưu tiên đường thẳng, không zoom, không rung mạnh. MP4 cần kích thước chẵn.

## Cài mới

Python 3.10 trở lên. Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/prepare_dashcam.py
.\.venv\Scripts\python.exe -m collision_warning.web --open-browser
```

WSL/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python tools/prepare_dashcam.py
python -m collision_warning.web --open-browser
```

**GPU NVIDIA.** Mặc định `--device auto` tự dùng GPU CUDA đầu tiên khi PyTorch có CUDA, nếu không thì dùng CPU;
dashboard cũng tự chọn và hiển thị thiết bị. Cài PyTorch CUDA khớp driver (driver ≥ 580 dùng `cu130`), ví dụ:

```powershell
.\.venv-win\Scripts\python.exe -m pip install --no-deps --ignore-installed "torch==2.14.1+cu130" "torchvision==0.29.1+cu130" --index-url https://download.pytorch.org/whl/cu130
```

Nếu pip báo lỗi SSL khi tải, tải file `.whl` bằng trình duyệt/curl rồi `pip install` từ file.
Trên máy demo (RTX 3050 Laptop 4 GB, dashcam 1280×720, YOLO11s): khoảng 9 FPS trên GPU so với 2,6 FPS trên CPU.
YOLO11s + ByteTrack mất khoảng 37 ms/frame trên GPU; tìm làn chạy trên CPU ở ảnh thu nhỏ còn
`--lane-width 960` px (khoảng 50 ms/frame; `640` nhanh hơn nhưng mất làn nhiều frame hơn). FP16 không nhanh hơn trên GPU này.
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
không đo khoảng cách mét hay vận tốc tuyệt đối. Bộ tìm làn dùng CV cổ điển ban ngày, chưa dự báo đường đi khi đánh lái hoặc chứng minh vận hành an toàn trên xe.

| Trạng thái | Ý nghĩa |
|---|---|
| `WARMUP` | Chưa đủ ít nhất ba quan sát và một giây lịch sử |
| `LANE_UNAVAILABLE` | Chỉ với `--lane-fallback none`: không xác định được làn tự động; ngừng TTC và xóa lịch sử |
| `OUTSIDE_ZONE` | Xe ngoài vùng đường đã chọn |
| `NOT_CLOSING` | Không có tín hiệu giãn ảnh dương đủ rõ |
| `UNRELIABLE` | Box bị cắt, đổi góc/kích thước đột ngột hoặc hồi quy nhiễu |
| `CLEAR` | TTC lớn hơn ngưỡng cảnh báo; không đồng nghĩa chắc chắn an toàn |
| `PENDING` | Đang xác nhận cảnh báo qua nhiều frame |
| `WARNING` | TTC ≤ 3 giây, xác nhận đủ frame; màu cam |
| `CRITICAL` | TTC ≤ 1,5 giây, xác nhận đủ frame; màu đỏ |
| `UNTRACKED` | Có detection nhưng chưa có ID và TTC |

Ngưỡng 3/1,5 giây là cấu hình demo. Cảnh báo cần ba frame liên tiếp;
giữ `WARNING` trong lúc xác nhận tăng lên `CRITICAL`. Track mất detection ngắn
(≤ `--lost-grace 0.3` giây) giữ lịch sử TTC và làn; mất lâu hơn thì xóa và tích lũy lại.
Video và dashboard đánh dấu `*` ở **xe mục tiêu**: xe gần nhất (đáy box thấp nhất) đang
trong làn, lấn làn hoặc cắt vào làn; xe phải tồn tại ít nhất ba frame để tránh detection ma.

Quan hệ của mỗi xe với làn (cột `lane_relation` trong CSV):

| Quan hệ | Ý nghĩa |
|---|---|
| `IN_LANE` | Điểm giữa cạnh dưới box nằm trong làn |
| `CUT_IN` | Đang vào làn: đã lấn ≥ 15% bề rộng làn khi di chuyển ngang, hoặc dự báo vào trong `--entry-horizon 1` giây |
| `ENCROACHING` | Đè lên làn ≥ 30% nhưng không di chuyển ngang |
| `DRIFTING` | Ở làn bên, trôi dần vào làn (dấu hiệu chuyển làn), dự báo vào trong 3 giây |
| `ADJACENT` | Ở làn bên, không có dấu hiệu sang làn |
| `FAR` | Quá xa phía trước, ngoài phần làn kéo dài |

Chuyển sang quan hệ nguy hiểm hơn cần hai frame xác nhận, trở về mức an toàn cần ba frame.
`--near-ratio 0.55` quyết định khi nào một xe cắt làn là "gần" (`CRITICAL`): 0 ở điểm tụ, 1 ở đáy làn.
CSV có thêm `lane_penetration`, `lateral_speed`, `time_to_entry_s`, `braking`, `effective_ttc_s`,
`alert`, `alert_reason`; summary có `scene_events` (`CUT_IN`, `DRIFTING`, `BRAKING`, `TARGET_SWITCH`).

Tham số chính: `--warning-ttc 3`, `--critical-ttc 1.5`, `--history 1`,
`--confirm-frames 3`, `--min-expansion 0.02`, `--imgsz 640`, `--conf 0.1`,
`--model yolo11s.pt` (dùng `yolo11n.pt` khi cần nhanh hơn trên CPU),
`--tracker collision_warning/trackers/bytetrack.yaml` (ByteTrack giữ ID 60 frame khi bị che),
`--lost-grace 0.3`, `--lane-memory 2`, `--lane-width 960`, `--entry-horizon 1`, `--near-ratio 0.55`,
`--device auto`.
Detection dùng NMS không phân lớp để một xe không bị nhận đồng thời là `bus` và `truck`.
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
| `.zone.json` | Vùng thủ công để dùng lại; `[]` khi dùng auto |
| `.lanes.json` | Biên/vùng làn, trạng thái và điểm chất lượng mỗi frame auto |
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

## Giao diện Camera TTC mới

Đã đồng bộ giao diện tối màu từ upstream commit `976a7ba`, giữ thiết kế ba màn hình **Giám sát hành trình / Báo cáo & đánh giá / Cấu hình hệ thống**, và nối vào pipeline Python thật. Mã UI chính ở [`ui/`](ui/README.md), được FastAPI phục vụ cùng origin với API:

```powershell
.\.venv-win\Scripts\python.exe -m collision_warning.web --open-browser
```

Mở **http://127.0.0.1:8765/**. Không dùng `python -m http.server --directory ui` để chạy demo, vì static server không có API YOLO/TTC. Video tải lên được gửi tới server local trên máy này, không tới dịch vụ bên ngoài.

- **Giám sát:** chọn dashcam/video tải lên, bám làn tự động hoặc vẽ đa giác, bắt đầu/dừng phiên, xem tiến độ và frame đang xử lý. Sau khi hoàn tất có thể phát/tạm dừng/tua video, đồng bộ TTC và danh sách xe theo frame; click biểu đồ hoặc sự kiện để tua.
- **Báo cáo & đánh giá:** mở lịch sử phiên, tải video/CSV/JSON/biểu đồ và lịch sử làn, xem coverage có chú thích, bảng so sánh synthetic và tài liệu TuSimple. Bảng lấy từ kết quả thật của `tools/benchmark_lanes.py`; chưa chạy thì báo chưa có dữ liệu.
- **Cấu hình:** ngưỡng TTC và confidence áp dụng cho phiên mới; không thay đổi phiên đang chạy hoặc số liệu phiên cũ.

Không còn số TTC/box/đồ thị mô phỏng cứng trong giao diện. Clip giả lập vẫn là nguồn đầu vào kiểm chứng riêng, chạy YOLO thật và được đánh dấu rõ. Lịch sử cũ thiếu danh sách xe theo frame được ghi chú; video kết quả vẫn xem lại được.
