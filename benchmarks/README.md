# Đánh giá làn đường và pipeline TTC

## Thay đổi được so sánh

- **Baseline cũ — fixed_roi**: đa giác chuẩn hóa `[[.45,.55],[.57,.55],[.85,.98],[.16,.98]]` cố định cho mọi frame.
- **auto_lane**: màu vạch trắng/vàng → Hough khởi tạo → dò theo dải ảnh → khớp đường cong bậc hai, loại ngoại lai → kiểm tra hình học → làm mượt theo thời gian. Che bounding box xe để giảm việc nhận nhầm thân xe là vạch.

Đây là baseline computer vision cổ điển, **chưa dùng mạng học sâu để tìm làn**. YOLO11s phát hiện xe; ByteTrack gán ID. Biên làn được kéo dài về điểm tụ để xếp xe ở xa; xe trong làn, lấn hoặc cắt vào làn được xét TTC (xem README chính). Vạch phải bao quanh tâm camera; cần camera gắn gần giữa xe. Làn còn nhìn thấy bắt đầu khoảng 60% chiều cao ảnh, không suy diễn làn đến chân trời. Mất hai biên, không hợp hình học hoặc điểm chất lượng quá thấp: bộ tìm làn không xuất vùng. Giữ kết quả trước tối đa 0,3 giây, giảm điểm chất lượng; sau đó ứng dụng dùng làn vừa xác nhận tối đa 2 giây rồi hành lang mặc định (`--lane-fallback none` để ngừng TTC như trước).

Điểm chất lượng trong UI là tỷ lệ hỗ trợ/độ nhất quán của vạch, **không phải xác suất đúng đã được hiệu chuẩn**. Đường cong gắt, chuyển làn, vạch mờ, bóng mạnh, mưa/đêm, vật che khuất và camera lệch tâm vẫn là giới hạn.

## Metric nên báo cáo

| Thành phần | Metric | Quy tắc và ý nghĩa |
|---|---|---|
| Biên làn | Boundary MAE (pixel) ↓ | Trung bình `abs(x_pred(y)-x_gt(y))` trên các hàng và biên có dự đoán; báo cùng coverage để tránh che giấu frame thất bại |
| Vùng làn | Corridor IoU ↑ | Diện tích giao / hợp của hai vùng pixel; không tìm thấy vùng khi GT có vùng được tính **0** |
| Khả dụng | Frame coverage, sampled-row coverage ↑ | Tỷ lệ frame có vùng, tỷ lệ điểm biên trên lưới đánh giá có dự đoán. Coverage không chứng minh vùng đúng |
| Benchmark TuSimple | Accuracy ↑, FP ↓, FN ↓ | Theo quy tắc benchmark; xem bên dưới. Không phải mAP |
| Detection xe | mAP50–95, recall ↑ | Cần box/class GT trên tập validation chưa dùng để chỉnh mô hình; `YOLO.val()` hỗ trợ các metric này |
| Tracking | HOTA, IDF1 ↑; ID switches ↓ | Cần ID GT xuyên suốt clip, có thể dùng TrackEval |
| TTC | MAE/RMSE (giây) ↓ và coverage ↑ | Chỉ đo khi có TTC GT đồng bộ; báo lỗi theo từng khoảng TTC, không gán TTC=0 cho frame không ước lượng |
| Cảnh báo | Event precision/recall, false alarms/phút, lead time | Cần nhãn sự kiện nguy cơ. Gộp các cảnh báo liên tiếp cùng xe thành một sự kiện; chốt trước ngưỡng TTC và cửa sổ ghép sự kiện |
| Hiệu năng | FPS, latency p50/p95 ↓ | Cùng phần cứng, độ phân giải, batch, device và quy tắc đo. Lane-only ms khác FPS của toàn pipeline |

Dashboard hiện chỉ ghi FPS và độ khả dụng làn, **chưa có GT để công bố accuracy thực tế của detection/tracking/TTC**. Không lấy số cảnh báo hay điểm confidence của detector làm accuracy. FPS trong summary gồm YOLO, tracking, lane, TTC, vẽ và encode MP4; bỏ setup/đọc video, không gồm encode WebM phục vụ trình duyệt.

Nguồn: [Ultralytics validation](https://docs.ultralytics.com/modes/val/), [TrackEval](https://github.com/JonathonLuiten/TrackEval).

## Phép so sánh nhỏ đã chạy trong repo

```powershell
.\.venv-win\Scripts\python.exe tools/benchmark_lanes.py
```

Sinh 12 ảnh 960×540 có GT hình học biết trước: đường thẳng/cong, dịch trái/phải, vạch đứt, xe che vùng giữa. Seed cố định theo case. Hai phương pháp chạy cùng ảnh; mỗi ảnh reset estimator để tránh rò trạng thái giữa cảnh. Tập này đơn giản và dùng trong phát triển, **không phải validation độc lập hoặc kết quả TuSimple**. Không dùng YOLO, boxes che vật thể là boxes lý tưởng từ bộ sinh.

Kết quả một lần chạy trên máy demo:

| Metric | Đa giác cố định | Bám vạch tự động |
|---|---:|---:|
| Mean corridor IoU, tất cả frame | 0,7135 | 0,9261 |
| Boundary MAE, hàng có dự đoán | 24,60 px | 0,82 px |
| Frame coverage | 100% | 100% |
| Sampled-row coverage | 100% | 96,25% |

IoU dùng toàn ảnh với GT từ y=0,60H tới 0,94H; phần vùng dư ngoài GT cũng bị phạt. MAE đo hai biên tại 40 hàng từ 0,62H tới 0,92H. Frame không có vùng bị IoU=0 và coverage=0; MAE để null. Mean MAE trong summary là trung bình MAE mỗi frame có dự đoán, không phải gộp mọi pixel. Vì coverage hàng khác nhau, không được đọc MAE riêng lẻ như một phép so sánh đầy đủ. Đây là kiểm tra khả năng khớp hình học, không đo ổn định thời gian.

Kết quả tái tạo ở `outputs/benchmarks/synthetic/`: `summary.json`, `frames.csv`, `comparison.jpg` (đỏ: baseline, xanh: auto). Thời gian phụ thuộc máy và tải, không dùng con số này như throughput YOLO.

Smoke test video thực: `udacity-traffic.webm`, 12 giây, 960×540/15 FPS, YOLO11n CPU, imgsz=640, conf=0,25 trên Ryzen 7 5800H: xuất đủ 180 frame, 138 frame lane detected + 35 frame giữ ngắn hạn đủ chất lượng, 7 frame không khả dụng; khoảng 8,28 FPS xử lý. Đây là **độ khả dụng 96,1%, không phải accuracy**, vì video không có nhãn lane GT. Model đã được khởi động trong phiên trước; kết quả phụ thuộc tải máy. Không có cảnh báo TTC trong clip này.

## Benchmark công khai được chọn: TuSimple

[TuSimple Lane Detection Challenge](https://github.com/TuSimple/tusimple-benchmark/blob/master/doc/lane_detection/readme.md) có ảnh camera phía trước và nhãn lane dạng x theo `h_samples`, phù hợp kiểm tra đường cao tốc ban ngày. Dùng tập validation có nhãn, cố định danh sách clip trước khi so sánh; giữ riêng các clip đã dùng để chỉnh ngưỡng. Nếu chia dữ liệu, chia theo clip/chuỗi, không chia ngẫu nhiên frame liền nhau. Tải dữ liệu theo hướng dẫn/điều khoản của tác giả vào máy local; repo này không đóng gói dataset.

Chạy cùng tập cho hai baseline:

```powershell
.\.venv-win\Scripts\python.exe tools/evaluate_tusimple.py --root D:/datasets/tusimple --labels D:/datasets/tusimple/validation_labels.json --output outputs/benchmarks/tusimple
```

`--labels` nhận một hoặc nhiều file JSONL có `raw_file`, `h_samples`, `lanes`. Chương trình kiểm tra đủ ảnh, không âm thầm bỏ ảnh lỗi; chạy trên **frame có nhãn cuối clip**, không có temporal warm-up hoặc YOLO. Xuất `fixed_roi.jsonl`, `auto_lane.jsonl` theo định dạng submission và `summary.json`. `run_time` là ms tìm làn + lấy mẫu biên, không tính đọc ảnh và chấm điểm. Đây là protocol single-frame cố định để so sánh hai baseline; hiệu năng tracking/làm mượt cần đánh giá video riêng.

Quy tắc [evaluator chính thức](https://github.com/TuSimple/tusimple-benchmark/blob/master/evaluate/lane.py): kiểm tra điểm biên với ngưỡng 20 px được hiệu chỉnh theo góc GT; lane đạt ít nhất 85% điểm được xem là khớp. Trả về Accuracy, FP, FN; có quy tắc phạt runtime quá 200 ms và xử lý trường hợp nhiều hơn bốn GT lane. `collision_warning/lane_metrics.py` triển khai lại quy tắc bằng Python 3 và có kiểm thử; dùng evaluator của tác giả để xác nhận một báo cáo benchmark chính thức.

**Phạm vi so sánh:** hệ thống TTC chỉ dự đoán hai biên ego-lane, trong khi TuSimple có thêm làn lân cận. Script giữ **toàn bộ GT**, nên làn không được dự đoán bị phạt FN. Không lọc chỉ ego GT rồi so Accuracy với bảng xếp hạng toàn bộ lane. Chưa chạy trên bộ TuSimple thực nên **chưa có điểm benchmark công khai để báo cáo**. Khi có dữ liệu, lưu hash/danh sách split, phiên bản code, phần cứng và bản cấu hình cùng báo cáo.

Đối chứng học sâu cho bước tiếp theo: [Ultra-Fast Lane Detection v2 (repo tác giả)](https://github.com/cfzd/Ultra-Fast-Lane-Detection-v2). Chạy checkpoint và hướng dẫn TuSimple chính thức trên **cùng split, resize, phần cứng và evaluator**, ghi rõ toàn bộ lane so với ego-only. Không so thời gian CPU của demo với FPS GPU trong bài báo. Bản này chưa tải hoặc tích hợp checkpoint UFLDv2.

## Dữ liệu cần để đo TTC thực tế

TuSimple chỉ chấm lane, không có TTC GT hay nhãn va chạm. Muốn đánh giá TTC, dùng simulator có depth/vận tốc tương đối hoặc cảm biến đồng bộ. Với chuyển động dọc lý tưởng `TTC_gt = range / positive_closing_speed`; loại/đánh dấu trường hợp không tiến gần, không ép vô cực thành số hữu hạn. Chốt cách xác định nguy cơ và ngưỡng trước khi chấm cảnh báo. Các clip scale bus hiện có chỉ kiểm chứng pipeline với TTC lý tưởng; không đại diện các vụ va chạm thực.
