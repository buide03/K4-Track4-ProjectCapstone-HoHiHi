"use strict";
const $ = id => document.getElementById(id);
const state = {assets: [], asset: null, laneMode: "auto", previewToken: 0, previewLoading: false, zone: [], backupZone: [], editing: false, job: null, busy: false, starting: false, timer: null, history: [], playbackTime: null, mode: "source"};
const activeStatuses = new Set(["queued", "running", "encoding", "cancelling"]);
const clock = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = "Không kết nối được với ứng dụng. Hãy kiểm tra server local.";
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : body.detail?.map(e => e.msg).join("; ") || message;
    } catch { /* non-JSON response */ }
    throw new Error(message);
  }
  return response.json();
}
function error(message) { $("error-message").textContent = message || ""; $("error-message").hidden = !message; }
function tagFor(asset) { return asset.kind === "synthetic" ? "GIẢ LẬP KIỂM CHỨNG" : asset.kind === "upload" ? "VIDEO CỦA BẠN" : "DASHCAM THỰC TẾ"; }
function setBusy(busy) {
  state.busy = busy;
  ["lane-mode", "source-select", "upload-button", "warning-ttc", "critical-ttc", "max-seconds", "confidence", "edit-zone", "reset-zone"].forEach(id => $(id).disabled = busy);
  $("start-button").disabled = busy || !state.asset || state.editing || state.previewLoading || (state.laneMode === "manual" && state.zone.length < 3);
  $("stop-button").hidden = !busy;
  $("stop-button").disabled = state.starting || !state.job || state.job.status === "cancelling";
  $("connection-status").textContent = busy ? "Đang phân tích" : "Sẵn sàng";
}
function populateSources() {
  const select = $("source-select"); select.replaceChildren();
  for (const [kind, label] of [["dashcam", "DASHCAM THỰC TẾ"], ["synthetic", "GIẢ LẬP CÓ THAM CHIẾU"], ["upload", "VIDEO CỦA BẠN"]]) {
    const group = document.createElement("optgroup"); group.label = label;
    state.assets.filter(a => a.kind === kind).forEach(asset => {
      const option = document.createElement("option"); option.value = asset.id;
      option.textContent = `${asset.title}${asset.available ? ` · ${asset.duration_s.toFixed(1)}s` : " · chưa tải"}`;
      option.disabled = !asset.available; group.append(option);
    });
    if (group.children.length) select.append(group);
  }
  if (state.asset) select.value = state.asset.id;
}
function sourcePlayer() {
  if (!state.asset) return;
  state.mode = "source";
  const video = $("video-player"); video.pause(); video.dataset.job = ""; video.src = state.asset.video_url; video.hidden = false;
  $("live-frame").hidden = true; $("frame-fallback").hidden = true;
  $("zone-canvas").hidden = false; $("player-label").className = "player-label"; $("player-label").innerHTML = "<span></span> VIDEO NGUỒN";
}
function chooseAsset(id, keepJob = false) {
  const asset = state.assets.find(a => a.id === id && a.available); if (!asset) return;
  state.asset = asset; state.laneMode = asset.default_lane_mode || "manual"; $("lane-mode").value = state.laneMode; state.editing = false; state.playbackTime = null;
  state.zone = asset.zone.map(([x, y]) => [Math.round(x * (asset.width - 1)), Math.round(y * (asset.height - 1))]);
  $("source-select").value = id;
  $("video-title").textContent = asset.title; $("source-kind").textContent = tagFor(asset);
  $("source-kind").className = `tag ${asset.kind === "synthetic" ? "synthetic" : ""}`;
  $("resolution-label").textContent = `${asset.width} × ${asset.height} · ${asset.fps.toFixed(0)} FPS`;
  $("source-description").textContent = asset.description;
  const credit = $("source-credit"); credit.textContent = `${asset.attribution}${asset.source_url ? " ↗" : ""}`;
  credit.href = asset.source_url || "#"; credit.hidden = !asset.source_url;
  $("player-wrap").style.aspectRatio = `${asset.width}/${asset.height}`;
  $("frame-fallback").src = asset.frame_url;
  $("player-empty").hidden = true;
  const canvas = $("zone-canvas"); canvas.width = asset.width; canvas.height = asset.height;
  sourcePlayer(); finishEditing(false);
  if (!keepJob) {
    clearTimeout(state.timer); state.job = null; $("results-card").hidden = true;
    $("progress-region").hidden = true; updateStats(null); drawEvents([]); drawChart();
  }
  setBusy(false);
  if (!keepJob) previewLane();
}
async function previewLane() {
  const token = ++state.previewToken; state.previewLoading = false;
  if (state.laneMode === "manual") { finishEditing(false); return; }
  state.zone = []; drawZone(); state.previewLoading = true; setBusy(state.busy);
  $("zone-description").textContent = "Đang tìm hai biên làn trên frame đầu…";
  try {
    const lane = await api(`/api/assets/${state.asset.id}/lane`);
    if (token !== state.previewToken) return;
    state.zone = lane.polygon; drawZone(); laneDescription(lane, true);
  } catch(e) { if (token === state.previewToken) { error(e.message); state.zone = []; } }
  finally { if (token === state.previewToken) { state.previewLoading = false; setBusy(state.busy); } }
}
function laneDescription(lane, preview = false) {
  if (!lane) return;
  const label = {detected: `Bám vạch · điểm chất lượng ${(lane.confidence * 100).toFixed(0)}%`, held: "Giữ làn ngắn hạn · tín hiệu đang giảm", unavailable: "Mất làn · tạm ngừng TTC", manual: "Vùng thủ công · không tự bám vạch"}[lane.status];
  $("zone-description").textContent = `${label || lane.status}${preview ? " · xem trước frame đầu" : ""}`;
}
$("lane-mode").onchange = () => {
  state.laneMode = $("lane-mode").value; state.editing = false; sourcePlayer();
  if (state.laneMode === "manual") state.zone = state.asset.zone.map(([x,y]) => [Math.round(x*(state.asset.width-1)),Math.round(y*(state.asset.height-1))]);
  finishEditing(false); previewLane();
};
function drawZone() {
  const canvas = $("zone-canvas"), ctx = canvas.getContext("2d"); ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!state.zone.length) return;
  const scale = canvas.width / Math.max(canvas.clientWidth, 1);
  ctx.beginPath(); state.zone.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  if (state.zone.length >= 3) ctx.closePath();
  ctx.fillStyle = "rgba(67,154,245,0.13)"; ctx.fill(); ctx.strokeStyle = "#63b6ff"; ctx.lineWidth = 2 * scale; ctx.stroke();
  if (state.laneMode === "manual") state.zone.forEach(([x, y], i) => {
    ctx.beginPath(); ctx.arc(x, y, 4 * scale, 0, Math.PI * 2); ctx.fillStyle = "#d9efff"; ctx.fill();
    if (state.editing) { ctx.font = `${12 * scale}px Segoe UI`; ctx.fillStyle = "white"; ctx.fillText(i + 1, x + 9 * scale, y - 7 * scale); }
  });
}
function finishEditing(confirm = true) {
  if (confirm && state.zone.length < 3) { error("Hãy chọn ít nhất 3 đỉnh cho vùng đường."); return; }
  state.editing = false; $("zone-canvas").classList.remove("editing");
  $("finish-zone").hidden = $("undo-zone").hidden = true; $("edit-zone").hidden = false;
  $("zone-description").textContent = `Vùng đường · ${state.zone.length} đỉnh`;
  drawZone(); setBusy(state.busy);
}
$("edit-zone").onclick = () => {
  if (!state.asset || state.busy) return;
  ++state.previewToken; state.previewLoading = false; state.laneMode = "manual"; $("lane-mode").value = "manual";
  error(""); sourcePlayer(); $("video-player").currentTime = 0;
  state.backupZone = state.zone.map(p => [...p]); state.zone = []; state.editing = true;
  $("zone-canvas").classList.add("editing"); $("zone-canvas").focus();
  $("finish-zone").hidden = $("undo-zone").hidden = false; $("edit-zone").hidden = true;
  $("zone-description").textContent = "Click các đỉnh quanh vùng đường; tối thiểu 3 điểm";
  setBusy(false); drawZone();
};
$("zone-canvas").onclick = event => {
  if (!state.editing) return;
  const rect = event.currentTarget.getBoundingClientRect();
  state.zone.push([Math.max(0, Math.min(state.asset.width - 1, Math.round((event.clientX - rect.left) * state.asset.width / rect.width))), Math.max(0, Math.min(state.asset.height - 1, Math.round((event.clientY - rect.top) * state.asset.height / rect.height)))]);
  $("zone-description").textContent = `${state.zone.length} đỉnh đã chọn · nhấn Hoàn tất vùng`;
  drawZone();
};
$("finish-zone").onclick = () => finishEditing();
$("undo-zone").onclick = () => { state.zone.pop(); drawZone(); $("zone-description").textContent = `${state.zone.length} đỉnh đã chọn`; };
$("reset-zone").onclick = () => { if (state.asset && !state.busy) chooseAsset(state.asset.id); };
$("zone-canvas").onkeydown = event => {
  if (!state.editing) return;
  if (event.key === "Enter") { event.preventDefault(); finishEditing(); }
  if (event.key === "Escape") { state.zone = state.backupZone; finishEditing(false); }
  if (event.key === "Backspace") { event.preventDefault(); $("undo-zone").click(); }
};
$("source-select").onchange = event => chooseAsset(event.target.value);
$("video-player").onerror = () => { if (state.mode === "result") { error("Trình duyệt chưa phát được video kết quả. Bạn có thể tải MP4 bên dưới."); return; } $("frame-fallback").hidden = false; $("video-player").hidden = true; };
$("video-player").onplay = () => { if (state.editing) $("video-player").pause(); else if (state.mode === "source" && state.laneMode === "auto") { $("zone-canvas").hidden = true; $("zone-description").textContent = "Video nguồn · bấm phân tích để xem làn cập nhật theo frame"; } };
$("video-player").ontimeupdate = () => {
  if (state.job?.status !== "completed" || state.mode !== "result") return;
  state.playbackTime = $("video-player").currentTime;
  const sample = [...state.job.series].reverse().find(p => p.time_s <= state.playbackTime);
  if (sample) updateStats(sample, state.job.processing_fps);
  drawChart();
};
function thresholds() {
  let warning = Number($("warning-ttc").value), critical = Number($("critical-ttc").value);
  if (critical >= warning) { critical = Math.max(.5, warning - .1); $("critical-ttc").value = critical; }
  $("warning-value").value = `${warning.toFixed(1)} s`; $("critical-value").value = `${critical.toFixed(1)} s`;
  drawChart();
}
$("warning-ttc").oninput = thresholds; $("critical-ttc").oninput = thresholds;
function updateStats(sample, fps = 0) {
  const ttc = sample?.ttc_s, target = sample?.target_id;
  $("metric-ttc").innerHTML = `${ttc == null ? "—" : ttc.toFixed(1)}<small> giây</small>`;
  $("metric-target").textContent = target != null ? `Xe mục tiêu #${target}` : "Chưa có TTC đủ tin cậy";
  const status = sample?.status || "READY";
  if (sample?.lane) laneDescription(sample.lane);
  const labels = {LANE_UNAVAILABLE: ["Mất làn", "Tạm ngừng TTC · chưa xác định vùng xe chạy", "warning"], CRITICAL: ["Khẩn cấp", "TTC dưới ngưỡng khẩn cấp", "critical"], WARNING: ["Cảnh báo", "TTC dưới ngưỡng cảnh báo", "warning"], MONITORING: ["Theo dõi", "Chưa ghi nhận cảnh báo", "clear"], READY: ["Sẵn sàng", "Chạy phân tích để xem nguy cơ", ""]};
  const [label, note, theme] = labels[status] || labels.MONITORING;
  $("metric-risk").textContent = label; $("metric-risk-note").textContent = note; $("risk-card").className = `metric ${theme}`;
  $("metric-tracks").innerHTML = `${sample?.tracks_count ?? sample?.tracks?.length ?? 0}<small> xe</small>`;
  $("metric-fps").textContent = fps ? `${fps.toFixed(1)} FPS xử lý trung bình` : "YOLO11n + ByteTrack";
}
function drawEvents(events) {
  $("event-count").textContent = events.length;
  const list = $("event-list"); list.replaceChildren();
  if (!events.length) { list.innerHTML = `<div class="empty-events">${icon("clock")}<p>Chưa có sự kiện</p><small>Không có cảnh báo không đồng nghĩa chắc chắn an toàn.</small></div>`; return; }
  [...events].reverse().forEach(event => {
    const button = document.createElement("button"); button.className = `event-row ${event.status === "CRITICAL" ? "critical" : ""}`;
    button.innerHTML = `<span>!</span><span>${event.status === "CRITICAL" ? "Nguy cơ khẩn cấp" : "Cảnh báo tiếp cận"}<small>Xe #${event.track_id} · TTC ${event.estimated_ttc_s.toFixed(1)}s</small></span><time>${clock(event.time_s)}</time>`;
    button.title = "Xem lại thời điểm này sau khi video hoàn tất";
    button.onclick = () => { if (state.job?.status === "completed") { $("video-player").currentTime = Math.max(0, event.time_s - .25); $("video-player").play().catch(() => {}); } };
    list.append(button);
  });
}
function drawChart() {
  const canvas = $("ttc-chart"), rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  const dpr = window.devicePixelRatio || 1; canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height, l = 30, r = w - 8, t = 13, b = h - 23;
  const series = state.job?.series || [], duration = Math.max(1, series.at(-1)?.time_s || Math.min(state.asset?.duration_s || 12, Number($("max-seconds").value)));
  const warning = state.job?.config.warning_ttc || Number($("warning-ttc").value), critical = state.job?.config.critical_ttc || Number($("critical-ttc").value), upper = Math.max(6, warning * 2);
  const x = v => l + (r - l) * v / duration, y = v => b - (b - t) * Math.min(upper, v) / upper;
  ctx.font = "9px Segoe UI"; ctx.textBaseline = "middle";
  for (let i = 0; i <= 3; i++) { const value = upper * i / 3; ctx.strokeStyle = "#edf2f6"; ctx.beginPath(); ctx.moveTo(l, y(value)); ctx.lineTo(r, y(value)); ctx.stroke(); ctx.fillStyle = "#a1afba"; ctx.fillText(`${value.toFixed(0)}s`, 1, y(value)); }
  for (let i = 0; i <= 4; i++) { const value = duration * i / 4; ctx.fillStyle = "#a1afba"; ctx.fillText(`${value.toFixed(0)}s`, x(value) - 5, h - 6); }
  for (const [value, color] of [[warning, "#e8ab51"], [critical, "#e58383"]]) { ctx.setLineDash([4, 5]); ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(l, y(value)); ctx.lineTo(r, y(value)); ctx.stroke(); }
  ctx.setLineDash([]); ctx.strokeStyle = "#4a92f0"; ctx.lineWidth = 2; ctx.beginPath();
  let previous = null;
  series.forEach(p => { if (p.ttc_s == null) { previous = null; return; } if (!previous || previous.target_id !== p.target_id) ctx.moveTo(x(p.time_s), y(p.ttc_s)); else ctx.lineTo(x(p.time_s), y(p.ttc_s)); previous = p; }); ctx.stroke();
  if (state.playbackTime != null) { ctx.strokeStyle = "#142c47aa"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x(state.playbackTime), t); ctx.lineTo(x(state.playbackTime), b); ctx.stroke(); }
  $("chart-empty").hidden = series.some(p => p.ttc_s != null);
  $("chart-empty").textContent = state.job?.status === "completed" ? "Không có TTC khả dụng: xe ngoài vùng, không tiếp cận hoặc tín hiệu chưa ổn định." : "Đường TTC sẽ xuất hiện khi có đủ lịch sử chuyển động.";
  $("chart-duration").textContent = clock(duration);
  canvas.onclick = e => { if (state.job?.status === "completed") $("video-player").currentTime = Math.max(0, Math.min(duration, (e.offsetX - l) / (r - l) * duration)); };
}
window.addEventListener("resize", () => { drawChart(); drawZone(); });
function showCompleted(job, autoplay = false) {
  state.mode = "result";
  const video = $("video-player"); if (video.dataset.job !== job.id) { video.dataset.job = job.id; video.src = job.video_url; video.load(); }
  video.hidden = false; $("frame-fallback").hidden = $("live-frame").hidden = $("zone-canvas").hidden = true;
  $("player-label").className = "player-label result"; $("player-label").innerHTML = "<span></span> KẾT QUẢ PHÂN TÍCH";
  $("results-card").hidden = false; const links = $("download-links"); links.replaceChildren();
  const summary = job.summary;
  $("lane-coverage").textContent = summary?.lane_mode === "auto" ? `Có làn khả dụng ${summary.lane_available_frames}/${summary.frames} frame (${(100*summary.lane_available_frames/Math.max(1,summary.frames)).toFixed(1)}%). Tỷ lệ này không đo độ chính xác; clip chưa có nhãn làn tham chiếu.` : "Vùng thủ công cố định; chưa đo độ chính xác làn trên video này.";
  [["mp4", "Video MP4"], ["csv", "Dữ liệu CSV"], ["summary", "Báo cáo JSON"], ["zone", "Vùng thủ công"], ["lanes", "Làn theo frame"], ["chart", "Biểu đồ SVG"]].filter(([kind]) => kind !== "lanes" || summary?.lane_mode).forEach(([kind, label]) => { const a = document.createElement("a"); a.href = `/api/jobs/${job.id}/files/${kind}`; a.innerHTML = icon("download") + label; if (kind === "chart") a.target = "_blank"; links.append(a); });
  if (autoplay) video.play().catch(() => {});
}
function updateJob(job) {
  state.job = job; const busy = activeStatuses.has(job.status); setBusy(busy);
  $("progress-region").hidden = false;
  const pct = job.status === "completed" ? 100 : Math.min(100, 100 * job.frames / Math.max(1, job.total_frames));
  $("progress-percent").textContent = `${Math.round(pct)}%`; $("progress-fill").style.width = `${pct}%`;
  const labels = {queued: "Đang chuẩn bị mô hình…", running: "Đang phát hiện, theo dõi và ước lượng TTC", encoding: "Đang chuẩn bị video phát lại…", cancelling: "Đang dừng phiên phân tích…", completed: "Phân tích hoàn tất", cancelled: "Đã dừng phân tích", failed: "Phân tích không hoàn tất"};
  $("progress-label").textContent = labels[job.status];
  $("progress-detail").textContent = `${job.frames} / ${job.total_frames} frame · ${job.processing_fps.toFixed(1)} FPS${job.status === "running" && job.processing_fps ? ` · còn khoảng ${Math.ceil((job.total_frames - job.frames) / job.processing_fps)} giây` : ""}`;
  if (busy && job.frames) { const image = $("live-frame"); image.hidden = false; $("video-player").pause(); $("zone-canvas").hidden = true; image.src = `/api/jobs/${job.id}/frame?t=${job.frames}`; $("player-label").className = "player-label result"; $("player-label").innerHTML = "<span></span> ĐANG PHÂN TÍCH"; }
  const sample = job.series.at(-1); if (sample) updateStats(sample, job.processing_fps);
  drawEvents(job.events); drawChart();
  if (job.status === "completed") showCompleted(job);
  if (job.status === "failed") { error(job.error || "Không xử lý được video."); sourcePlayer(); drawZone(); }
  if (job.status === "cancelled") { sourcePlayer(); drawZone(); }
}
async function pollJob(id) {
  try { const job = await api(`/api/jobs/${id}`); updateJob(job); if (activeStatuses.has(job.status)) state.timer = setTimeout(() => pollJob(id), 600); else await loadHistory(); }
  catch (e) { error(e.message); setBusy(false); $("connection-status").textContent = "Mất kết nối"; }
}
$("start-button").onclick = async () => {
  try {
    error(""); if (!state.asset || (state.laneMode === "manual" && state.zone.length < 3)) throw new Error("Hãy chọn video và vùng đường trước.");
    state.starting = true; state.job = null; setBusy(true); $("results-card").hidden = true; state.playbackTime = null; $("video-player").dataset.job = "";
    const job = await api("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({asset_id: state.asset.id, zone: state.laneMode === "manual" ? state.zone : [], lane_mode: state.laneMode, warning_ttc: Number($("warning-ttc").value), critical_ttc: Number($("critical-ttc").value), max_seconds: Number($("max-seconds").value), confidence: Number($("confidence").value)})});
    state.starting = false; updateJob(job); await pollJob(job.id);
  } catch (e) { state.starting = false; error(e.message); setBusy(false); }
};
$("stop-button").onclick = async () => { try { if (state.job) updateJob(await api(`/api/jobs/${state.job.id}/cancel`, {method: "POST"})); } catch (e) { error(e.message); } };
$("upload-button").onclick = () => $("file-input").click();
$("file-input").onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  try { error(""); if (file.size > 200 * 1024 * 1024) throw new Error("Video vượt quá 200 MB."); $("upload-button").disabled = true; $("source-description").textContent = "Đang tải video lên máy local…"; const form = new FormData(); form.append("file", file); const asset = await api("/api/uploads", {method: "POST", body: form}); state.assets.push(asset); populateSources(); chooseAsset(asset.id); }
  catch (e) { error(e.message); } finally { $("upload-button").disabled = state.busy; event.target.value = ""; }
};
async function loadHistory() {
  state.history = await api("/api/jobs"); $("history-count").textContent = state.history.length;
  const list = $("history-list"); list.replaceChildren();
  if (!state.history.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "Chưa có phiên phân tích. Hãy bắt đầu với một video mẫu."; list.append(p); }
  state.history.forEach(job => {
    const button = document.createElement("button"); button.className = "history-item"; button.disabled = state.busy;
    const title = document.createElement("span"); title.textContent = job.title; const detail = document.createElement("small"); detail.textContent = `${new Date(job.created_at * 1000).toLocaleString("vi-VN")} · ${job.frames} frame · ${job.events.length} sự kiện`; title.append(detail);
    const tag = document.createElement("span"); tag.className = "tag"; tag.textContent = ({completed: "HOÀN TẤT", failed: "LỖI", cancelled: "ĐÃ DỪNG"})[job.status] || "ĐANG CHẠY";
    button.append(title, tag); button.onclick = () => { chooseAsset(job.asset_id, true); ++state.previewToken; state.previewLoading = false; state.zone = job.config.zone; state.laneMode = job.config.lane_mode || "manual"; $("lane-mode").value = state.laneMode; $("warning-ttc").value = job.config.warning_ttc; $("critical-ttc").value = job.config.critical_ttc; thresholds(); updateJob(job); showAnalysis(); if (activeStatuses.has(job.status)) pollJob(job.id); }; list.append(button);
  });
}
function showAnalysis() { $("history-panel").hidden = true; $("analysis-panel").hidden = false; $("nav-analysis").classList.add("active"); $("nav-history").classList.remove("active"); drawZone(); drawChart(); }
$("nav-analysis").onclick = showAnalysis; $("close-history").onclick = showAnalysis;
$("nav-history").onclick = async () => { try { await loadHistory(); $("history-panel").hidden = false; $("analysis-panel").hidden = true; $("nav-analysis").classList.remove("active"); $("nav-history").classList.add("active"); } catch (e) { error(e.message); } };
$("guide-button").onclick = () => $("guide-dialog").showModal();
$("close-guide").onclick = $("understood-guide").onclick = () => $("guide-dialog").close();
async function init() {
  try {
    const [health, assets] = await Promise.all([api("/api/health"), api("/api/assets")]); state.assets = assets; populateSources();
    const initial = assets.find(a => a.available && a.id === "udacity-traffic") || assets.find(a => a.available && a.kind === "dashcam") || assets.find(a => a.available);
    if (initial) chooseAsset(initial.id); else { error("Chưa có video. Tải video của bạn hoặc chạy python tools/prepare_dashcam.py để lấy dashcam mẫu."); $("upload-button").disabled = false; }
    $("connection-status").textContent = "Sẵn sàng"; await loadHistory();
    if (health.active_job_id) { const job = await api(`/api/jobs/${health.active_job_id}`); chooseAsset(job.asset_id, true); ++state.previewToken; state.previewLoading = false; state.zone = job.config.zone; state.laneMode = job.config.lane_mode || "manual"; $("lane-mode").value = state.laneMode; updateJob(job); pollJob(job.id); }
    thresholds();
  } catch (e) { error(e.message); $("connection-status").textContent = "Chưa kết nối"; }
}
init();
