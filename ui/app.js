"use strict";
const $ = id => document.getElementById(id);
const video = $("video");
const active = new Set(["queued", "running", "encoding", "cancelling"]);
const state = {assets: [], asset: null, job: null, busy: false, starting: false, uploading: false, connected: false,
  zone: [], backupZone: [], editing: false, laneMode: "auto", previewToken: 0, previewLoading: false,
  playerMode: "source", playbackTime: null, pollTimer: null, pollToken: 0, config: {warning_ttc: 3, critical_ttc: 1.5, confidence: .25}};
const fmt = seconds => `${String(Math.floor(Math.max(0,seconds)/60)).padStart(2,"0")}:${String(Math.floor(Math.max(0,seconds)%60)).padStart(2,"0")}`;
const relationLabel = value => ({IN_LANE:"Trong làn",CUT_IN:"Đang cắt vào làn",ENCROACHING:"Đang lấn làn",ADJACENT:"Làn bên",DRIFTING:"Có dấu hiệu sang làn",FAR:"Ở xa",OUTSIDE:"Ngoài làn",UNKNOWN:"Chưa rõ làn"})[value] || value;
const reasonLabel = value => ({"vehicle cutting in":"Xe đang cắt vào làn", "close vehicle cutting in":"Xe gần đang cắt vào làn", "close vehicle encroaching on lane":"Xe gần đang lấn làn", "target braking hard":"Xe mục tiêu giảm tốc mạnh", "vehicle drifting toward lane":"Xe làn bên có dấu hiệu sang làn", "optical TTC":"TTC dưới ngưỡng"})[value] || relationLabel(value);
const colors = {CRITICAL: "#ff776d", WARNING: "#ffb052", LANE_UNAVAILABLE: "#ffb052", MONITORING: "#58ded0", READY: "#8494a5"};
function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
function toast(message) { $("toast").textContent = message; $("toast").style.display = "block"; clearTimeout(toast.timer); toast.timer = setTimeout(() => $("toast").style.display = "none", 3500); }
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Yêu cầu không thành công (${response.status}).`;
    try { const body = await response.json(); detail = typeof body.detail === "string" ? body.detail : body.detail?.map(x => x.msg).join("; ") || detail; } catch {}
    throw new Error(detail);
  }
  return response.json();
}
function go(page) {
  document.querySelectorAll(".page").forEach(el => el.hidden = el.id !== page);
  document.querySelectorAll(".nav").forEach(el => el.classList.toggle("active", el.dataset.page === page));
  const titles = {monitor: ["Giám sát hành trình", "Mỗi giây đều quan trọng.", "Quan sát chuyển động. Nhận biết nguy cơ. Chủ động hành trình."],
    reports: ["Báo cáo & đánh giá", "Dữ liệu cho mỗi quyết định.", "Xem kết quả thật, so sánh baseline và kiểm tra giới hạn của hệ thống."],
    settings: ["Cấu hình hệ thống", "Điều chỉnh theo hành trình.", "Thiết lập ngưỡng cho phiên phân tích tiếp theo."]};
  [$("breadcrumb").textContent, $("pageTitle").textContent, $("pageDesc").textContent] = titles[page];
  if (page === "monitor") drawChart();
  if (page === "reports") Promise.all([loadHistory(), loadEvaluation()]).catch(e => error(e.message));
}
function setBusy(busy) {
  state.busy = busy;
  ["sourceSelect", "laneMode", "maxSeconds", "editZone", "warning", "critical", "confidence", "saveSettings"].forEach(id => $(id).disabled = busy || state.uploading);
  $("upload").disabled = busy || state.uploading || !state.connected;
  $("start").disabled = busy || state.uploading || !state.connected || !state.asset || state.editing || state.previewLoading || (state.laneMode === "manual" && state.zone.length < 3);
  $("stop").hidden = !busy; $("stop").disabled = state.starting || !state.job || state.job.status === "cancelling";
  $("engineStatus").textContent = busy ? "Pipeline đang xử lý" : state.connected ? "Pipeline sẵn sàng" : "Chưa kết nối Python";
  $("connectionStatus").textContent = busy ? `Đang phân tích · ${state.device || "CPU"}` : state.connected ? `Python đã kết nối · ${state.device || "Local"}` : "Chưa kết nối Python";
  document.querySelectorAll(".history-item").forEach(el => el.disabled = busy || state.uploading);
  $("play").disabled = $("seek").disabled = $("reset").disabled = busy || !state.asset;
}
function populateSources() {
  const select = $("sourceSelect"); select.replaceChildren();
  for (const [kind,label] of [["dashcam","DASHCAM THỰC TẾ"],["synthetic","GIẢ LẬP KIỂM CHỨNG"],["upload","VIDEO CỦA BẠN"]]) {
    const group = document.createElement("optgroup"); group.label = label;
    for (const asset of state.assets.filter(a => a.kind === kind)) {
      const option = document.createElement("option"); option.value = asset.id; option.disabled = !asset.available;
      option.textContent = `${asset.title} · ${asset.available ? `${asset.duration_s.toFixed(1)}s` : "chưa tải"}`; group.append(option);
    }
    if (group.children.length) select.append(group);
  }
  if (state.asset) select.value = state.asset.id;
}
function defaultZone() { return state.asset.zone.map(([x,y]) => [Math.round(x*(state.asset.width-1)),Math.round(y*(state.asset.height-1))]); }
function sourcePlayer() {
  state.playerMode = "source"; video.pause(); video.dataset.job = ""; video.src = state.asset.video_url; video.hidden = false;
  $("liveFrame").hidden = $("preview").hidden = true; $("zone").hidden = false;
  $("preview").src = state.asset.frame_url; $("modeLabel").textContent = "VIDEO NGUỒN";
  $("viewport").style.aspectRatio = `${state.asset.width}/${state.asset.height}`;
}
function chooseAsset(id, keepJob = false) {
  const asset = state.assets.find(a => a.id === id && a.available); if (!asset) return;
  ++state.previewToken; state.previewLoading = false; state.asset = asset; state.playbackTime = null; state.editing = false;
  state.laneMode = asset.default_lane_mode || "manual"; state.zone = defaultZone();
  $("laneMode").value = state.laneMode; $("sourceSelect").value = id;
  $("filename").textContent = asset.title; $("sourceDescription").textContent = asset.description;
  $("sourceCredit").href = asset.source_url || "#"; $("sourceCredit").hidden = !asset.source_url;
  $("sourceCredit").textContent = `${asset.attribution || "Nguồn"} ↗`;
  $("sourceLabel").textContent = `${asset.kind === "synthetic" ? "GIẢ LẬP" : asset.kind === "upload" ? "VIDEO CỦA BẠN" : "DASHCAM"} · ${asset.width} × ${asset.height}`;
  $("zone").setAttribute("viewBox", `0 0 ${asset.width} ${asset.height}`);
  sourcePlayer(); editingUI(); renderZone();
  if (!keepJob) { ++state.pollToken; clearTimeout(state.pollTimer); state.job = null; $("resultCard").hidden = true; $("jobProgress").hidden = $("runDetail").hidden = true; updateStats(null); drawEvents([]); progressDisplay(null); drawChart(); previewLane(); }
  setBusy(false);
}
function renderZone() {
  $("zone").querySelector("polygon").setAttribute("points",state.zone.map(p => p.join(",")).join(" "));
  $("zone").querySelectorAll("circle").forEach(el => el.remove());
  $("zone").classList.toggle("editing",state.editing);
  if (state.editing) for (const [x,y] of state.zone) {
    const circle = document.createElementNS("http://www.w3.org/2000/svg","circle");
    circle.setAttribute("cx",x); circle.setAttribute("cy",y); circle.setAttribute("r",state.asset.width*.006); circle.setAttribute("fill","#58ded0"); $("zone").append(circle);
  }
}
function laneDescription(lane, preview = false) {
  if (!lane) return;
  const text = {detected: `Bám vạch · chất lượng ${(100*lane.confidence).toFixed(0)}%`,held:"Giữ làn ngắn hạn · tín hiệu giảm",memory:"Vạch bị che · dùng làn vừa nhận",fallback:"Không thấy vạch · hành lang mặc định",unavailable:"Mất làn · tạm ngừng TTC",manual:"Vùng thủ công cố định"}[lane.status] || "Chưa có vùng làn";
  $("laneLabel").textContent = text + (preview ? " · frame đầu" : "");
}
async function previewLane() {
  const token = ++state.previewToken;
  if (!state.asset || state.laneMode === "manual") { state.previewLoading = false; laneDescription({status:"manual"}); setBusy(state.busy); return; }
  state.zone = []; renderZone(); state.previewLoading = true; setBusy(state.busy); $("laneLabel").textContent = "Đang tìm hai biên làn…";
  try {
    const lane = await api(`/api/assets/${state.asset.id}/lane`); if (token !== state.previewToken) return;
    state.zone = lane.polygon; renderZone(); laneDescription(lane,true);
  } catch(e) { if (token === state.previewToken) { error(e.message); $("laneLabel").textContent = "Chưa lấy được vùng xem trước"; } }
  finally { if (token === state.previewToken) { state.previewLoading = false; setBusy(state.busy); } }
}
function editingUI() {
  $("editHint").hidden = $("undoZone").hidden = $("cancelZone").hidden = !state.editing;
  $("editZone").textContent = state.editing ? "✓ Lưu vùng" : "⌖ Vẽ vùng thủ công";
}
function validPolygon(points) {
  if (points.length < 3) return false;
  const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
  const on=(p,a,b)=>Math.abs(cross(a,b,p))<1e-6&&p[0]>=Math.min(a[0],b[0])&&p[0]<=Math.max(a[0],b[0])&&p[1]>=Math.min(a[1],b[1])&&p[1]<=Math.max(a[1],b[1]);
  const intersects=(a,b,c,d)=>on(a,c,d)||on(b,c,d)||on(c,a,b)||on(d,a,b)||(cross(a,b,c)*cross(a,b,d)<0&&cross(c,d,a)*cross(c,d,b)<0);
  let area=0;
  for(let i=0;i<points.length;i++) {
    const a=points[i],b=points[(i+1)%points.length]; if(a[0]===b[0]&&a[1]===b[1]) return false;
    area+=a[0]*b[1]-b[0]*a[1];
    for(let j=i+1;j<points.length;j++) { if(j===i+1||(i===0&&j===points.length-1)) continue;
      if(intersects(a,b,points[j],points[(j+1)%points.length])) return false;
    }
  }
  return Math.abs(area)>1;
}
function finishZone(cancel = false) {
  if (!cancel && !validPolygon(state.zone)) { error("Vùng cần ít nhất 3 đỉnh, có diện tích và không tự cắt. Bỏ điểm hoặc chọn lại."); return; }
  if (cancel) state.zone=state.backupZone.map(p=>[...p]);
  state.editing=false; editingUI(); renderZone(); setBusy(state.busy); laneDescription({status:"manual"}); error();
}
$("editZone").onclick=()=>{
  if(state.busy||!state.asset) return;
  if(state.editing) { finishZone(); return; }
  ++state.previewToken; state.previewLoading=false; state.laneMode="manual"; $("laneMode").value="manual";
  state.backupZone=state.zone.map(p=>[...p]); state.zone=[]; state.editing=true; sourcePlayer(); video.currentTime=0;
  editingUI(); renderZone(); setBusy(false); $("zone").focus(); error();
};
$("zone").onclick=event=>{
  if(!state.editing) return;
  const rect=event.currentTarget.getBoundingClientRect(),a=state.asset;
  state.zone.push([Math.max(0,Math.min(a.width-1,Math.round((event.clientX-rect.left)/rect.width*a.width))),Math.max(0,Math.min(a.height-1,Math.round((event.clientY-rect.top)/rect.height*a.height)))]); renderZone();
};
$("undoZone").onclick=()=>{state.zone.pop();renderZone();}; $("cancelZone").onclick=()=>finishZone(true);
$("zone").onkeydown=event=>{if(!state.editing)return;if(["Enter","Escape","Backspace"].includes(event.key))event.preventDefault();if(event.key==="Enter")finishZone();if(event.key==="Escape")finishZone(true);if(event.key==="Backspace")$("undoZone").click();};
$("laneMode").onchange=()=>{state.laneMode=$("laneMode").value;state.editing=false;editingUI();sourcePlayer();state.zone=state.laneMode==="manual"?defaultZone():[];renderZone();previewLane();};
$("sourceSelect").onchange=event=>chooseAsset(event.target.value);
function transport() {
  const duration=Number.isFinite(video.duration)?video.duration:state.asset?.duration_s||0, time=video.currentTime||0;
  $("seek").max=duration||1; $("seek").value=time; $("clock").textContent=$("timeLabel").textContent=fmt(time);$("duration").textContent=fmt(duration);
  $("play").textContent=video.paused?"▶":"Ⅱ";
}
$("play").onclick=async()=>{if(state.editing)return;try {if(video.paused)await video.play();else video.pause();}catch {error("Không phát được video này. Hãy thử MP4 H.264 hoặc tải video kết quả về.");}};
$("seek").oninput=event=>{video.currentTime=Number(event.target.value);};$("reset").onclick=()=>{video.currentTime=0;};
video.onloadedmetadata=transport;video.onpause=transport;video.onended=transport;
video.onplay=()=>{if(state.editing){video.pause();return;}if(state.playerMode==="source"&&state.laneMode==="auto"){$("zone").hidden=true;$("laneLabel").textContent="Video nguồn · phân tích để xem làn cập nhật theo frame";}transport();};
video.ontimeupdate=()=>{
  transport();if(state.playerMode!=="result"||!state.job)return;
  state.playbackTime=video.currentTime;const sample=[...state.job.series].reverse().find(p=>p.time_s<=state.playbackTime);if(sample)updateStats(sample);drawChart();
};
video.onerror=()=>{if(!state.asset)return;if(state.playerMode==="source"){$("preview").hidden=false;video.hidden=true;toast("Trình duyệt chưa phát được nguồn; vẫn có thể phân tích và xem WebM kết quả.");}else error("Không phát được video kết quả. Bạn có thể tải MP4 trong báo cáo.");};
function updateStats(sample) {
  const alerts = new Set((sample?.tracks || []).map(t => t.alert || t.status));
  const status = sample?.lane?.status === "unavailable" ? "LANE_UNAVAILABLE" : alerts.has("CRITICAL") ? "CRITICAL" : alerts.has("WARNING") ? "WARNING" : sample?.status || "READY", color=colors[status]||colors.MONITORING;
  const labels={READY:["Sẵn sàng","Chạy phân tích để xem nguy cơ"],MONITORING:["Theo dõi","Chưa ghi nhận cảnh báo"],WARNING:["Cảnh báo","TTC dưới ngưỡng cảnh báo"],CRITICAL:["Nguy cấp","TTC dưới ngưỡng nguy cấp"],LANE_UNAVAILABLE:["Mất làn","Tạm ngừng TTC; vùng xe chạy chưa rõ"]};
  [$("state").textContent,$("stateNote").textContent]=labels[status]||labels.MONITORING;
  $("state").style.color=$("riskPill").style.color=color;$("riskDot").style.background=color;$("riskPill").textContent=$("state").textContent.toUpperCase();
  $("ttc").innerHTML=`${sample?.ttc_s==null?"—":sample.ttc_s.toFixed(2)} <sub>giây</sub>`;
  $("ttcNote").textContent=sample?.target_id!=null?`Ước lượng cho xe #${sample.target_id}`:"Chưa có ước lượng khả dụng";
  const tracks=sample?.tracks;
  const total=tracks?.length??sample?.tracks_count;
  $("count").innerHTML=`${tracks?tracks.filter(t=>t.in_path).length:"—"} <sub>/ ${total??"—"} xe</sub>`;
  $("trackNote").textContent=state.job?.processing_fps?`${state.job.processing_fps.toFixed(1)} FPS xử lý trung bình`:"YOLO11n + ByteTrack";
  $("targetId").textContent=sample?.target_id!=null?`Xe #${sample.target_id}`:"Chưa có";
  $("targetNote").textContent=sample?.target_id!=null?(reasonLabel(sample.target_reason)||"Xe mục tiêu trong hoặc đang vào làn"):"Chưa có xe mục tiêu đủ điều kiện";
  const cause = (sample?.tracks || []).find(t => ["WARNING","CRITICAL"].includes(t.alert));
  if (cause?.alert_reason) $("stateNote").textContent = reasonLabel(cause.alert_reason);
  $("riskMarker").hidden=sample?.ttc_s==null;
  if(sample?.ttc_s!=null)$("riskMarker").style.left=`${Math.max(0,Math.min(100,100*(1-sample.ttc_s/Math.max(6,state.job?.config.warning_ttc*2||6))))}%`;
  $("targetCount").textContent=total??0;const list=$("targets");list.replaceChildren();
  if(!tracks?.length){const note=document.createElement("div");note.className="info";note.textContent=sample&&!tracks?"Phiên cũ chưa lưu danh sách xe theo frame; xem box trên video.":"Chưa có phương tiện đang được theo dõi.";list.append(note);}
  for(const track of tracks||[]) {
    const row=document.createElement("div");row.className="target";
    const vehicle=document.createElement("span");vehicle.className="vehicle";vehicle.textContent="▱";
    const detail=document.createElement("div"),title=document.createElement("b"),sub=document.createElement("small"),value=document.createElement("span");
    title.textContent=`Xe #${track.id}`;sub.textContent=`${({car:"Ô tô",truck:"Xe tải",bus:"Xe buýt",motorcycle:"Xe máy"})[track.class_name]||track.class_name} · ${track.lane_relation ? relationLabel(track.lane_relation) + (track.braking ? " · Giảm tốc mạnh" : "") : track.status==="LANE_UNAVAILABLE"?"Chưa rõ làn":track.in_path?"Trong vùng":"Ngoài vùng"}`;
    detail.append(title,sub);value.textContent=track.ttc_s==null?"—":`${track.ttc_s.toFixed(2)}s`;value.style.color=colors[track.alert || track.status]||"#8494a5";row.append(vehicle,detail,value);list.append(row);
  }
  if(sample?.lane)laneDescription(sample.lane);
}
function drawEvents(events) {
  $("eventCount").textContent=`${events.length} SỰ KIỆN`;const list=$("events");list.replaceChildren();
  if(!events.length){const note=document.createElement("div");note.className="info";note.textContent="Chưa có cảnh báo được ghi nhận.";list.append(note);}
  for(const event of [...events].reverse()) {
    const row=document.createElement("button");row.className="event event-button";
    const dot=document.createElement("i");dot.style.background=colors[event.status];const detail=document.createElement("div"),title=document.createElement("b"),note=document.createElement("p"),time=document.createElement("small");
    title.textContent=`Xe #${event.track_id} · ${event.status==="CRITICAL"?"Nguy cấp":"Cảnh báo"}`;note.textContent=Number.isFinite(event.estimated_ttc_s)?`TTC ${event.estimated_ttc_s.toFixed(2)}s · ${reasonLabel(event.reason) || "đã xác nhận"}`:`${reasonLabel(event.reason) || relationLabel(event.lane_relation) || "Cảnh báo hình học"} · TTC chưa khả dụng`;time.textContent=fmt(event.time_s);detail.append(title,note,time);row.append(dot,detail);
    row.disabled=state.job?.status!=="completed";row.onclick=()=>{video.currentTime=Math.max(0,event.time_s-.25);video.play().catch(()=>{});};list.append(row);
  }
}
function drawChart() {
  const canvas=$("chart"),rect=canvas.getBoundingClientRect();if(!rect.width)return;
  const dpr=devicePixelRatio||1;canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;const ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);
  const w=rect.width,h=rect.height,l=35,r=w-15,t=15,b=h-25,series=state.job?.series||[];
  const duration=Math.max(1,series.at(-1)?.time_s||Math.min(state.asset?.duration_s||12,Number($("maxSeconds").value)));
  const config=state.job?.config||state.config,upper=Math.max(6,config.warning_ttc*2),x=v=>l+(r-l)*v/duration,y=v=>b-(b-t)*Math.min(v,upper)/upper;
  ctx.font="9px monospace";ctx.fillStyle="#8494a5";ctx.lineWidth=1;
  for(let i=0;i<=3;i++){const v=upper*i/3;ctx.strokeStyle="#24303b";ctx.setLineDash([3,5]);ctx.beginPath();ctx.moveTo(l,y(v));ctx.lineTo(r,y(v));ctx.stroke();ctx.fillText(`${v.toFixed(0)}s`,4,y(v)+3);}
  for(let i=0;i<=4;i++){const v=duration*i/4;ctx.fillText(fmt(v),x(v)-12,h-6);}
  for(const [value,color] of [[config.warning_ttc,"#be8c43"],[config.critical_ttc,"#a34e51"]]){ctx.strokeStyle=color;ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(l,y(value));ctx.lineTo(r,y(value));ctx.stroke();}
  ctx.setLineDash([]);ctx.strokeStyle="#58ded0";ctx.lineWidth=2;ctx.beginPath();let previous=null;
  for(const p of series){if(p.ttc_s==null){previous=null;continue;}if(!previous||previous.target_id!==p.target_id)ctx.moveTo(x(p.time_s),y(p.ttc_s));else ctx.lineTo(x(p.time_s),y(p.ttc_s));previous=p;}ctx.stroke();
  if(state.playbackTime!=null){ctx.lineWidth=1;ctx.strokeStyle="#9baebd";ctx.beginPath();ctx.moveTo(x(state.playbackTime),t);ctx.lineTo(x(state.playbackTime),b);ctx.stroke();}
  $("chartEmpty").hidden=series.some(p=>p.ttc_s!=null);$("chartEmpty").textContent=state.job?.status==="completed"?"Chưa có TTC khả dụng: xe ngoài vùng, không tiến gần hoặc tín hiệu chưa ổn định.":"Đường TTC xuất hiện khi có đủ lịch sử chuyển động.";
  canvas.onclick=event=>{if(state.job?.status==="completed")video.currentTime=Math.max(0,Math.min(duration,(event.offsetX-l)/(r-l)*duration));};
}
window.addEventListener("resize",drawChart);
function progressDisplay(job) {
  const percent=job?.status==="completed"?100:job?Math.min(100,100*job.frames/Math.max(1,job.total_frames)):0;
  $("progress").innerHTML=`${Math.round(percent)} <sub>%</sub>`;$("progressFill").style.width=`${percent}%`;
  const labels={queued:"Đang chuẩn bị mô hình…",running:"Đang phát hiện xe, bám làn và ước lượng TTC",encoding:"Đang chuẩn bị video phát lại…",cancelling:"Đang dừng phiên…",cancelled:"Đã dừng phiên",failed:"Phân tích không hoàn tất",completed:"Phân tích hoàn tất"};
  $("progressNote").textContent=job?`${job.frames}/${job.total_frames} frame · ${job.processing_fps.toFixed(1)} FPS`:"Chưa có phiên chạy";
  $("runDetail").textContent=job?`${labels[job.status]} · ${job.frames}/${job.total_frames} frame`:"";
}
function showCompleted(job) {
  state.playerMode="result";
  if(video.dataset.job!==job.id){video.pause();video.dataset.job=job.id;video.src=job.video_url;video.load();state.playbackTime=0;}
  video.hidden=false;$("liveFrame").hidden=$("preview").hidden=$("zone").hidden=true;$("modeLabel").textContent="KẾT QUẢ PHÂN TÍCH";
  $("resultCard").hidden=false;$("reportTitle").textContent=job.title;
  const summary=job.summary;$("resultSummary").textContent=`${job.frames} frame · ${job.processing_fps.toFixed(1)} FPS · ${job.events.length} sự kiện. `+(summary?.lane_mode==="auto"?`Có làn khả dụng ${summary.lane_available_frames}/${summary.frames} frame (${(100*summary.lane_available_frames/Math.max(1,summary.frames)).toFixed(1)}%); đây không phải độ chính xác.`:"Vùng đường thủ công cố định.");
  const list=$("downloads");list.replaceChildren();
  for(const [kind,label] of [["mp4","↓ Video MP4"],["csv","↓ Dữ liệu CSV"],["summary","↓ Báo cáo JSON"],["zone","↓ Vùng thủ công"],["lanes","↓ Làn theo frame"],["chart","↗ Biểu đồ SVG"]]){
    if(kind==="lanes"&&!summary?.lane_mode)continue;if(kind==="zone"&&summary?.lane_mode==="auto")continue;
    const link=document.createElement("a");link.className="button";link.href=`/api/jobs/${job.id}/files/${kind}`;link.textContent=label;if(kind==="chart")link.target="_blank";list.append(link);
  }
}
function updateJob(job) {
  state.job=job;setBusy(active.has(job.status));$("jobProgress").hidden=$("runDetail").hidden=false;progressDisplay(job);
  const sample=job.series.at(-1);if(sample)updateStats(sample);drawEvents(job.events);drawChart();
  if(state.busy&&job.frames){video.pause();$("liveFrame").hidden=false;$("liveFrame").src=`/api/jobs/${job.id}/frame?t=${job.frames}`;$("zone").hidden=true;$("modeLabel").textContent="ĐANG PHÂN TÍCH";$("clock").textContent=fmt(job.time_s);}
  if(job.status==="completed")showCompleted(job);
  if(job.status==="failed"||job.status==="cancelled"){sourcePlayer();renderZone();if(job.status==="failed")error(job.error||"Không xử lý được video.");}
}
async function pollJob(id, token) {
  try {const job=await api(`/api/jobs/${id}`);if(token!==state.pollToken)return;updateJob(job);
    if(active.has(job.status))state.pollTimer=setTimeout(()=>pollJob(id,token),650);else{await loadHistory();if(job.status==="completed")toast("Đã lưu video và báo cáo. Chọn Báo cáo & đánh giá để tải kết quả.");}
  }catch(e){if(token!==state.pollToken)return;error(`Mất kết nối: ${e.message} Đang thử kết nối lại…`);state.pollTimer=setTimeout(()=>pollJob(id,token),2000);}
}
$("start").onclick=async()=>{
  try {
    error();if(!state.asset||state.editing||(state.laneMode==="manual"&&!validPolygon(state.zone)))throw new Error("Hãy chọn video và vùng đường hợp lệ.");
    state.starting=true;state.job=null;state.playbackTime=null;$("resultCard").hidden=true;updateStats(null);drawEvents([]);drawChart();setBusy(true);
    const job=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...state.config,asset_id:state.asset.id,lane_mode:state.laneMode,zone:state.laneMode==="manual"?state.zone:[],max_seconds:Number($("maxSeconds").value)})});
    state.starting=false;updateJob(job);const token=++state.pollToken;clearTimeout(state.pollTimer);pollJob(job.id,token);
  }catch(e){state.starting=false;setBusy(false);error(e.message);}
};
$("stop").onclick=async()=>{try{if(state.job)updateJob(await api(`/api/jobs/${state.job.id}/cancel`,{method:"POST"}));}catch(e){error(e.message);}};
$("upload").onclick=()=>$("file").click();
$("file").onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  try{error();if(file.size>200*1024*1024)throw new Error("Video vượt giới hạn 200 MB.");state.uploading=true;setBusy(state.busy);toast("Đang tải video lên máy local…");const form=new FormData();form.append("file",file);const asset=await api("/api/uploads",{method:"POST",body:form});state.assets.push(asset);populateSources();chooseAsset(asset.id);go("monitor");toast("Đã mở video. Chọn phân tích để chạy YOLO và TTC.");}
  catch(e){error(e.message);}finally{state.uploading=false;setBusy(state.busy);event.target.value="";}
};
function restoreJob(job) {
  error();chooseAsset(job.asset_id,true);state.zone=job.config.zone;state.laneMode=job.config.lane_mode||"manual";$("laneMode").value=state.laneMode;
  $("maxSeconds").value=String(job.config.max_seconds);updateJob(job);go("monitor");
  if(active.has(job.status)){const token=++state.pollToken;clearTimeout(state.pollTimer);pollJob(job.id,token);}
}
async function loadHistory() {
  const history=await api("/api/jobs"),list=$("history");list.replaceChildren();
  if(!history.length){const p=document.createElement("p");p.className="empty-history";p.textContent="Chưa có phiên chạy. Bắt đầu với video dashcam có sẵn.";list.append(p);}
  const labels={completed:"HOÀN TẤT",failed:"LỖI",cancelled:"ĐÃ DỪNG",running:"ĐANG CHẠY",encoding:"XUẤT VIDEO",queued:"CHỜ",cancelling:"ĐANG DỪNG"};
  for(const job of history){const button=document.createElement("button");button.className="history-item";button.disabled=state.busy||state.uploading;const title=document.createElement("span"),detail=document.createElement("small"),tag=document.createElement("span");title.textContent=job.title;detail.textContent=`${new Date(job.created_at*1000).toLocaleString("vi-VN")} · ${job.frames} frame · ${job.config.lane_mode==="auto"?"Bám làn tự động":"Vùng thủ công"} · ${job.events.length} sự kiện`;title.append(detail);tag.className="tag";tag.textContent=labels[job.status]||job.status;button.append(title,tag);button.onclick=()=>restoreJob(job);list.append(button);}
}
async function loadEvaluation() {
  const data=await api("/api/evaluation"),body=$("benchmarkRows");body.replaceChildren();const methods=data.synthetic?.methods;
  if(!methods){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=3;cell.textContent="Chưa có kết quả. Chạy tools/benchmark_lanes.py trên máy này.";row.append(cell);body.append(row);}
  else for(const [key,label,format] of [["mean_corridor_iou_all_frames","Corridor IoU ↑",x=>x.toFixed(3)],["boundary_mae_px_conditional","Boundary MAE ↓",x=>`${x.toFixed(2)} px`],["frame_coverage","Frame coverage",x=>`${(100*x).toFixed(1)}%`],["sampled_row_coverage","Sampled-row coverage",x=>`${(100*x).toFixed(2)}%`]]){
    const row=document.createElement("tr");for(const value of [label,...["fixed_roi","auto_lane"].map(m=>methods[m]?.[key]==null?"—":format(methods[m][key]))]){const cell=document.createElement("td");cell.textContent=value;row.append(cell);}body.append(row);
  }
  $("benchmarkNote").textContent="MAE chỉ tính hàng có dự đoán; đọc cùng coverage. Mất vùng bị IoU = 0. Giữ cùng dữ liệu, split và protocol khi so sánh.";
  $("benchmarkImage").hidden=!data.comparison_available;
}
$("settingsForm").onsubmit=event=>{
  event.preventDefault();const warning=Number($("warning").value),critical=Number($("critical").value),confidence=Number($("confidence").value);
  if(![warning,critical,confidence].every(Number.isFinite)||critical>=warning){$("formError").textContent="Ngưỡng nguy cấp phải nhỏ hơn ngưỡng cảnh báo.";return;}
  state.config={warning_ttc:warning,critical_ttc:critical,confidence};$("formError").textContent="";if(!state.job)drawChart();toast("Đã lưu cấu hình cho phiên phân tích tiếp theo.");
};
document.querySelectorAll(".nav").forEach(el=>el.onclick=()=>go(el.dataset.page));
$("viewReport").onclick=()=>go("reports");$("watchResult").onclick=()=>{go("monitor");video.play().catch(()=>{});};$("refreshHistory").onclick=()=>loadHistory().catch(e=>error(e.message));
async function init() {
  try {
    const [health,assets]=await Promise.all([api("/api/health"),api("/api/assets")]);state.connected=true;state.device=health.device;state.assets=assets;populateSources();
    const first=assets.find(a=>a.available&&a.id==="udacity-traffic")||assets.find(a=>a.available);
    if(first)chooseAsset(first.id);else error("Chưa có video mẫu. Mở video của bạn hoặc chạy tools/prepare_dashcam.py.");
    await loadHistory();if(health.active_job_id)restoreJob(await api(`/api/jobs/${health.active_job_id}`));setBusy(state.busy);drawChart();
  }catch(e){error("Chưa kết nối backend Python. Chạy python -m collision_warning.web và mở http://127.0.0.1:8765/. "+e.message);setBusy(false);}
}
init();
