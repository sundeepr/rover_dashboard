const TELEMETRY_POLL_MS = 2000;

const state = {
  currentUser: null,
  baseUrl: getDefaultBaseUrl(),
  telemetry: unavailableTelemetry(),
  pollTimer: null,
};

function unavailableTelemetry(message = "Waiting for SensorHub") {
  return {
    connection: {available: false, message},
    status: {source: "unavailable", cpuUsage: "Unavailable", memoryUsage: "Unavailable", cpuTemp: "Unavailable", gpuUsage: "Unavailable"},
    devices: [], odometry: {position: "Unavailable"},
    sources: {devices: "unavailable", odometry: "unavailable"},
    battery: {source: "sensorhub", available: false, status: "Unavailable", state: message},
  };
}

async function refreshTelemetry() {
  try {
    const response = await fetch(`${state.baseUrl}/api/telemetry`, {headers: {Accept: "application/json"}});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.telemetry = await response.json();
  } catch (error) {
    state.telemetry = unavailableTelemetry("Dashboard connection unavailable. Retrying…");
  }
  renderTelemetry();
}

let telemetrySocket;
let reconnectTimer;
function connectLiveUpdates() {
  const url = new URL(`${state.baseUrl}/ws`);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  telemetrySocket = new WebSocket(url);
  telemetrySocket.onopen = () => { stopTelemetryPolling(); };
  telemetrySocket.onmessage = (event) => {
    try { state.telemetry = JSON.parse(event.data); renderTelemetry(); } catch (error) { telemetrySocket.close(); }
  };
  telemetrySocket.onclose = () => {
    state.telemetry = unavailableTelemetry("Live connection lost. Reconnecting…");
    renderTelemetry();
    startTelemetryPolling();
    reconnectTimer = window.setTimeout(connectLiveUpdates, 3000);
  };
}

function renderTelemetry() {
  const { status, devices, odometry } = state.telemetry;
  setText("connectionStatus", state.telemetry.connection?.message || "SensorHub telemetry");
  const metricSource = status.source || "mock";
  markSection("metricsGrid", metricSource);
  markSection("devicesList", state.telemetry.sources?.devices || "mock");
  markSection("odometryGrid", state.telemetry.sources?.odometry || "mock");
  markSection("jetsonStatsGrid", state.telemetry.jetson?.available ? state.telemetry.jetson.source : "unavailable");
  renderGps(state.telemetry.gps);
  renderImu(state.telemetry.imu);
  renderController(state.telemetry.controller);

  setText("cpuUsageValue", status.cpuUsage);
  setText("memoryUsageValue", status.memoryUsage);
  setText("cpuTempValue", status.cpuTemp);
  setText("gpuUsageValue", status.gpuUsage);
  renderMetricChart("cpuUsage");
  renderMetricChart("memoryUsage");
  renderMetricChart("cpuTemp");
  renderMetricChart("gpuUsage");

  const devicesList = document.getElementById("devicesList");
  devicesList.innerHTML = devices
    .map(
      (device) => `
        <li>
          <div class="list-row">
            <div>
              <strong>${escapeHtml(device.name)}</strong>
              <span>${escapeHtml(device.port)}</span>
            </div>
            <span class="device-status ${device.available ? "" : "device-unavailable"}">${escapeHtml(device.status)}</span>
          </div>
        </li>
      `,
    )
    .join("");

  const odometryGrid = document.getElementById("odometryGrid");
  odometryGrid.innerHTML = Object.entries(odometry)
    .map(
      ([key, value]) => `
        <div class="stat">
          <span>${formatLabel(key)}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");

  renderDetailedJetsonStats(state.telemetry.jetson?.details);
  renderBatteryManagement(state.telemetry.battery);
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function formatLabel(key) {
  return key
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (char) => char.toUpperCase());
}

function getDefaultBaseUrl() {
  return new URL("/telemetry", window.location.origin).href;
}

function startTelemetryPolling() {
  stopTelemetryPolling();


  state.pollTimer = window.setInterval(() => {
    refreshTelemetry();
  }, TELEMETRY_POLL_MS);
}

function stopTelemetryPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

// All charts render the shared backend window; rendering never adds samples.
function historyPoints(group, key) {
  return (state.telemetry.history?.samples || []).map(point => ({
    time: point.time, value: point[group]?.[key] ?? null,
    source: group === "battery" ? point.batterySource : point.statusSource,
  }));
}

function chartPaths(points, low, high, width, bottom, top, padding = 0, history = state.telemetry.history, maxGapMs = 6000) {
  if (!history) return {line: "", area: "", last: null};
  const y = value => bottom - (Math.max(low, Math.min(high, value)) - low) / (high - low) * (bottom - top);
  let line = "", area = "", previous = null, last = null;
  for (const point of points) {
    if (!Number.isFinite(point.value)) { previous = null; continue; }
    const x = padding + (point.time - (history.now - history.windowMs)) / history.windowMs * (width - 2 * padding);
    const py = y(point.value);
    const move = !previous || point.time - previous.time > maxGapMs || point.source !== previous.source;
    line += `${move ? "M" : "L"} ${x.toFixed(2)} ${py.toFixed(2)} `;
    if (!move) area += `M ${previous.x} ${bottom} L ${previous.x} ${previous.y} L ${x} ${py} L ${x} ${bottom} Z `;
    previous = {...point, x, y: py};
    last = {x, y: py};
  }
  return {line, area, last};
}

function renderMetricChart(metricKey) {
  const points = historyPoints("status", metricKey);
  const max = metricKey === "cpuTemp" ? Math.max(70, ...points.map(p => p.value ?? 0)) : 100;
  const paths = chartPaths(points, 0, max, 240, 66, 6);
  document.getElementById(`${metricKey}Line`)?.setAttribute("d", paths.line);
  document.getElementById(`${metricKey}Area`)?.setAttribute("d", paths.area);
}

function parsePercent(value) {
  if (typeof value !== "string") {
    return null;
  }

  const parsed = Number.parseFloat(value.replace("%", "").trim());
  if (Number.isNaN(parsed)) {
    return null;
  }

  return Math.max(0, Math.min(parsed, 100));
}

function parseTemperature(value) {
  if (typeof value !== "string") {
    return null;
  }

  const parsed = Number.parseFloat(value.replace("C", "").trim());
  if (Number.isNaN(parsed) || parsed < -200) {
    return null;
  }

  return parsed;
}

function renderDetailedJetsonStats(details) {
  const statsGrid = document.getElementById("jetsonStatsGrid");
  if (!statsGrid) {
    return;
  }

  const items = Array.isArray(details) ? details : [];
  if (!items.length) {
    statsGrid.innerHTML = `
      <article class="detail-stat">
        <span>Jetson Stats</span>
        <strong>Unavailable</strong>
      </article>
    `;
    return;
  }

  statsGrid.innerHTML = items
    .map(
      (item) => `
        <article class="detail-stat ${shouldShowDetailTrendline(item.name) ? "detail-stat-trend" : ""}">
          <span>${escapeHtml(item.name)}</span>
          <strong>${escapeHtml(item.value)}</strong>
          ${
            shouldShowDetailTrendline(item.name)
              ? `<svg class="detail-chart" viewBox="0 0 240 56" preserveAspectRatio="none">
                  <path id="${detailChartId(item.name)}Area" class="detail-chart-area"></path>
                  <path id="${detailChartId(item.name)}Line" class="detail-chart-line"></path>
                </svg>`
              : ""
          }
        </article>
      `,
    )
    .join("");

  items.forEach((item) => {
    if (shouldShowDetailTrendline(item.name)) {
      renderDetailChart(item.name);
    }
  });
}

function renderBatteryManagement(battery) {
  const payload = battery || unavailableTelemetry().battery;
  markSection("batterySummaryGrid", payload.available === false ? "unavailable" : payload.source || "mock");
  const capacityValue = parsePercent(payload.summary?.capacityRemaining);
  const capacity = capacityValue === null ? 0 : capacityValue;

  renderBatteryAlerts(payload);
  renderBatteryHistory(payload);
  setText("batteryStatus", payload.status || "Unavailable");
  document.getElementById("batteryStatus").dataset.status = payload.status || "Unavailable";
  setText("batteryCapacity", payload.summary?.capacityRemaining || "Unavailable");
  setText(
    "batterySource",
    `${payload.source || "unknown"} · ${payload.state || "unknown"} · ${payload.updatedAt || "--"}`,
  );

  const fill = document.getElementById("batteryFill");
  if (fill) {
    fill.style.width = `${capacity}%`;
    fill.classList.toggle("battery-fill-low", capacity > 0 && capacity < 20);
  }

  const summaryGrid = document.getElementById("batterySummaryGrid");
  const summaryItems = [
    ["Voltage", payload.summary?.totalVoltage],
    ["Current", payload.summary?.current],
    ["Power", payload.summary?.power],
    ["Cell Delta", payload.summary?.cellDelta],
    ["Balancing", payload.controls?.balancing],
    ["Discharging", payload.controls?.discharging],
  ];

  summaryGrid.innerHTML = summaryItems
    .map(
      ([label, value]) => `
        <div class="stat">
          <span>${label}</span>
          <strong>${value || "Unavailable"}</strong>
        </div>
      `,
    )
    .join("");

  const cellsGrid = document.getElementById("batteryCellsGrid");
  const cells = Array.isArray(payload.cells) ? payload.cells : [];
  if (!cells.length) {
    cellsGrid.innerHTML = `
      <article class="detail-stat">
        <span>Cell Voltages</span>
        <strong>Unavailable</strong>
      </article>
    `;
    return;
  }

  cellsGrid.innerHTML = cells
    .map(
      (cell) => `
        <article class="cell-stat">
          <span>C${cell.index}</span>
          <strong>${cell.voltage}</strong>
        </article>
      `,
    )
    .join("");
}

function shouldShowDetailTrendline(name) {
  const normalizedName = String(name || "").toLowerCase();
  return normalizedName === "ram" || normalizedName.startsWith("temp ");
}

function renderDetailChart(name) {
  const points = historyPoints("details", name);
  const max = name.toLowerCase() === "ram" ? 100 : Math.max(70, ...points.map(p => p.value ?? 0));
  const paths = chartPaths(points, 0, max, 240, 52, 4);
  document.getElementById(`${detailChartId(name)}Line`)?.setAttribute("d", paths.line);
  document.getElementById(`${detailChartId(name)}Area`)?.setAttribute("d", paths.area);
}

function detailChartId(name) {
  return `detail-${String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}

// Source metadata lets each section switch to real data independently.
function markSection(contentId, source) {
  const panel = document.getElementById(contentId).closest(".panel");
  const sample = ["mock", "simulator", "post"].includes(source);
  const unavailable = !source || source === "unavailable";
  panel.classList.toggle("sample-data", sample || unavailable);
  let badge = panel.querySelector(".source-badge");
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "source-badge";
    panel.querySelector(".section-header").append(badge);
  }
  badge.textContent = sample ? (source === "mock" ? "Mock data" : "Simulated data")
    : unavailable ? "Awaiting data" : source === "system" ? "System data" : "Live data";
}

function renderGps(gps) {
  const validCoordinates = typeof gps?.latitude === "number" && Number.isFinite(gps.latitude)
    && Math.abs(gps.latitude) <= 90 && typeof gps?.longitude === "number"
    && Number.isFinite(gps.longitude) && Math.abs(gps.longitude) <= 180;
  const available = gps?.available === true && validCoordinates;
  markSection("gpsGrid", available ? gps.source || "unavailable" : "unavailable");
  setText("gpsMessage", available ? "Position reported by the rover GPS receiver." : `World view · ${gps?.reason || "Waiting for rover GPS data"}.`);
  renderGpsMap(gps, available);
  const items = [
    ["Latitude", available ? `${gps.latitude.toFixed(6)}°` : "--"],
    ["Longitude", available ? `${gps.longitude.toFixed(6)}°` : "--"],
    ["Altitude", available && Number.isFinite(gps.altitude) ? `${gps.altitude} m` : "--"],
    ["Speed", available && Number.isFinite(gps.speed) ? `${gps.speed.toFixed(2)} m/s` : "--"],
    ["Course over ground", available && Number.isFinite(gps.course) ? `${gps.course.toFixed(1)}°` : "--"],
    ["Satellites used / in view", gps?.receiving ? `${gps.satellites ?? "--"} / ${gps.satellitesInView ?? "--"}` : "--"],
    ["Fix", gps?.receiving ? gps.fix ?? (available ? "Valid" : "No fix") : "--"],
    ["Fix quality", gps?.receiving ? gps.fixQuality ?? "--" : "--"],
    ["HDOP", gps?.receiving ? gps.hdop ?? "--" : "--"],
    ["Last update", gps?.receiving ? gps.updatedAt ?? "--" : "--"],
  ];
  renderReadingGrid("gpsGrid", items);
}

function renderReadingGrid(id, items) {
  const grid = document.getElementById(id);
  grid.replaceChildren(...items.map(([label, value]) => {
    const card = document.createElement("div");
    card.className = "stat";
    const title = document.createElement("span");
    title.textContent = label;
    const reading = document.createElement("strong");
    reading.textContent = value;
    card.append(title, reading);
    return card;
  }));
}

function renderControllerJoystick(rc) {
  const throttle = rc?.throttle;
  const steer = rc?.steer;
  const valid = Number.isFinite(throttle) && Number.isFinite(steer);
  const inRange = valid && Math.abs(throttle) <= 100 && Math.abs(steer) <= 100;
  const label = valid ? `Throttle ${throttle} · Steering ${steer}${inRange ? "" : " · Outside −100 to +100 range"}`
    : "RC readings unavailable";
  const chart = document.getElementById("controllerJoystick");
  chart.setAttribute("aria-label", `RC joystick: ${label}`);
  chart.classList.toggle("joystick-unavailable", !inRange);
  document.getElementById("controllerJoystickPosition").setAttribute("visibility", inRange ? "visible" : "hidden");
  setText("controllerJoystickReading", label);
  if (!inRange) return;
  const x = 140 + steer * 0.8;
  const y = 140 - throttle * 0.8;
  document.getElementById("controllerJoystickDot").setAttribute("cx", x);
  document.getElementById("controllerJoystickDot").setAttribute("cy", y);
  document.getElementById("controllerJoystickStem").setAttribute("x2", x);
  document.getElementById("controllerJoystickStem").setAttribute("y2", y);
}

function renderController(controller) {
  const available = controller?.available === true;
  markSection("controllerGrid", available ? controller.source : "unavailable");
  setText("controllerMessage", available ? `SensorHub controller · ${controller.updatedAt || "--"}`
    : controller?.reason || "Waiting for SensorHub controller data");
  const data = available ? controller.data || {} : {};
  const flag = (value, yes, no) => value === true ? yes : value === false ? no : "--";
  const value = (v, unit = "") => Number.isFinite(v) ? `${v}${unit}` : "--";
  renderControllerJoystick(data.rc);
  const warnings = [];
  if (data.host_stale === true) warnings.push("Controller reports stale host commands.");
  if (data.wheel_base_matches === false) warnings.push("Reported wheel base differs from SensorHub configuration.");
  setText("controllerWarning", warnings.join(" "));
  document.getElementById("controllerWarning").classList.toggle("hidden", warnings.length === 0);
  const items = [
    ["Arming", flag(data.armed, "Armed", "Disarmed")],
    ["Autonomy", flag(data.autonomy, "Enabled", "Disabled")],
    ["Host commands", flag(data.host_stale, "Stale", "Fresh")],
    ["RC throttle", value(data.rc?.throttle)],
    ["RC steering", value(data.rc?.steer)],
    ["Host throttle", value(data.host?.throttle)],
    ["Host turn radius", value(data.host?.radius_mm, " mm")],
    ["Host auxiliary", value(data.host?.aux)],
    ["Left motor command", value(data.cmd?.left)],
    ["Right motor command", value(data.cmd?.right)],
    ["Pump command", value(data.pump_cmd)],
    ["Wheel base", value(data.wheel_base_mm, " mm")],
    ["Wheel base configuration", flag(data.wheel_base_matches, "Matches", "Mismatch")],
    ["Board time", value(data.board_t_ms, " ms")],
    ...Object.entries(data.extra || {}).map(([key, v]) => [`Extra: ${key}`, v == null ? "--" : typeof v === "object" ? JSON.stringify(v) : String(v)]),
  ];
  renderReadingGrid("controllerGrid", items);
}

function renderImu(imu) {
  const available = imu?.available === true;
  markSection("imuGrid", available ? imu.source : "unavailable");
  setText("imuMessage", available ? `SensorHub IMU · ${imu.updatedAt || "--"}` : imu?.reason || "Waiting for SensorHub IMU data");
  const data = available ? imu.data || {} : {};
  const value = (v, unit = "", digits = 2) => Number.isFinite(v) ? `${v.toFixed(digits)}${unit}` : "--";
  const vector = key => ["x", "y", "z"].map(axis => `${axis.toUpperCase()} ${value(data[key]?.[axis])}`).join(" · ");
  const items = [
    ["Roll", value(data.euler_deg?.roll, "°")],
    ["Pitch", value(data.euler_deg?.pitch, "°")],
    ["Yaw", value(data.euler_deg?.yaw, "°")],
    ["IMU heading", `${value(data.heading_deg, "°")} ${data.compass || ""}`],
    ["Acceleration (m/s²)", vector("accel_mps2")],
    ["Angular velocity (rad/s)", vector("gyro_rads")],
    ["Magnetic field (µT)", vector("mag_ut")],
    ["Linear acceleration (m/s²)", vector("linear_accel_mps2")],
    ["Gravity (m/s²)", vector("gravity_mps2")],
    ["Quaternion (w, x, y, z)", ["w", "x", "y", "z"].map(axis => value(data.quaternion?.[axis], "", 4)).join(", ")],
    ["Magnetometer accuracy", ["Unreliable", "Low", "Medium", "High"][data.mag_accuracy] || "--"],
  ];
  renderReadingGrid("imuGrid", items);
}

// Reloading the Google Maps iframe resets its view. Ignore receiver noise and
// rate-limit real movement, measuring from the last displayed position so slow
// movement eventually updates too. Numeric readings and the link stay current.
const gpsMapPosition = {latitude: null, longitude: null, updatedAt: null};
const GPS_MAP_MIN_DISTANCE_M = 10;
const GPS_MAP_MIN_INTERVAL_MS = 15000;

function gpsDistanceMeters(a, b) {
  const radians = degrees => degrees * Math.PI / 180;
  const dLat = radians(b.latitude - a.latitude);
  const dLon = radians(b.longitude - a.longitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(radians(a.latitude))
    * Math.cos(radians(b.latitude)) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, h))));
}

function renderGpsMap(gps, available) {
  const frame = document.getElementById("gpsMapFrame");
  const link = document.getElementById("gpsMapLink");
  link.classList.toggle("hidden", !available);
  const coordinates = available
    ? `${gps.latitude.toFixed(6)},${gps.longitude.toFixed(6)}` : null;
  const now = performance.now();
  const firstFix = gpsMapPosition.latitude === null;
  const moved = available && !firstFix && gpsDistanceMeters(gpsMapPosition, gps) >= GPS_MAP_MIN_DISTANCE_M;
  if (!available || firstFix || (moved && now - gpsMapPosition.updatedAt >= GPS_MAP_MIN_INTERVAL_MS)) {
    const mapUrl = available
      ? `https://www.google.com/maps?q=${encodeURIComponent(coordinates)}&z=18&t=k&output=embed`
      : "https://www.google.com/maps?ll=0,0&z=2&t=k&output=embed";
    if (frame.getAttribute("src") !== mapUrl) frame.src = mapUrl;
    gpsMapPosition.latitude = available ? gps.latitude : null;
    gpsMapPosition.longitude = available ? gps.longitude : null;
    gpsMapPosition.updatedAt = available ? now : null;
  }
  if (available) {
    link.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(coordinates)}`;
  } else {
    link.removeAttribute("href");
  }
}

function renderBatteryAlerts(payload) {
  const container = document.getElementById("batteryAlerts");
  const alerts = Array.isArray(payload.alerts) ? payload.alerts : [];
  container.replaceChildren(...alerts.map((alert) => {
    const message = document.createElement("p");
    message.className = `battery-alert ${["fault", "warning", "info"].includes(alert.severity) ? alert.severity : "warning"}`;
    message.textContent = `${alert.message} (${payload.errorMask || "BMS flag"})`;
    return message;
  }));
  container.classList.toggle("hidden", !alerts.length);
}

function renderBatteryHistory(payload) {
  for (const [key, field, unit] of [["Capacity", "capacityRemaining", "%"], ["Current", "current", "A"], ["Power", "power", "W"]]) {
    const history = key === "Capacity"
      ? {...state.telemetry.history?.capacity, now: state.telemetry.history?.now}
      : state.telemetry.history;
    const points = key === "Capacity" ? (history.samples || []) : historyPoints("battery", key);
    const values = points.map(p => p.value).filter(Number.isFinite);
    const latest = payload.available ? Number.parseFloat(payload.summary?.[field]) : NaN;
    setText(`battery${key}Latest`, Number.isFinite(latest) ? `${latest.toFixed(1)} ${unit}` : "Unavailable");
    const extent = Math.ceil(Math.max(1, ...values.map(Math.abs)) * 1.1);
    const low = key === "Capacity" ? 0 : key === "Current" ? -50 : -extent;
    const high = key === "Capacity" ? 100 : key === "Current" ? 50 : extent;
    const clipped = values.some(value => value < low || value > high);
    setText(`battery${key}Range`, `Scale: ${low} to ${high} ${unit}${clipped ? " · Values outside scale clipped" : ""}`);
    const paths = chartPaths(points, low, high, 400, 132, 8, 8, history, key === "Capacity" ? 90000 : 6000);
    document.getElementById(`battery${key}Path`).setAttribute("d", paths.line);
    const zero = 132 - (0 - low) / (high - low) * 124;
    document.getElementById(`battery${key}Zero`).setAttribute("d", `M 8 ${zero} L 392 ${zero}`);
    const dot = document.getElementById(`battery${key}Dot`);
    dot.setAttribute("visibility", paths.last ? "visible" : "hidden");
    if (paths.last) { dot.setAttribute("cx", paths.last.x); dot.setAttribute("cy", paths.last.y); }
    document.getElementById(`battery${key}Empty`).classList.toggle("hidden", values.length > 0);
  }
}

async function toggleCameraFullscreen() {
  const panel = document.getElementById("cameraPanel");
  try {
    if (document.fullscreenElement === panel) await document.exitFullscreen();
    else await panel.requestFullscreen();
  } catch (error) {
    setText("cameraMessage", "Fullscreen is unavailable in this browser. Both feeds remain visible below.");
  }
}

// Camera players have their own lifecycle; telemetry renders never recreate them.
async function loadCameras() {
  const button = document.getElementById("reloadCameras");
  button.disabled = true;
  const grid = document.getElementById("cameraGrid");
  grid.replaceChildren();
  setText("cameraMessage", "Connecting to video relay…");
  try {
    const response = await fetch(`${state.baseUrl}/api/cameras`);
    if (!response.ok) throw new Error("Camera configuration unavailable");
    const payload = await response.json();
    for (const camera of payload.cameras || []) {
      if (!/^[a-z][a-z0-9_-]{0,31}$/.test(camera.id)) continue;
      const card = document.createElement("section");
      const title = document.createElement("h4");
      title.textContent = camera.name;
      const frame = document.createElement("iframe");
      frame.title = `${camera.name} · WebRTC video`;
      frame.src = `${state.baseUrl}/video/${camera.id}/?muted=true&autoplay=true&controls=true`;
      frame.allow = "autoplay; fullscreen";
      frame.allowFullscreen = true;
      card.append(title, frame);
      grid.append(card);
    }
    setText("cameraMessage", grid.children.length
      ? "View-only · Each player shows connection errors and retries automatically."
      : "No cameras configured. Add streams to /etc/rover-dashboard/cameras.json.");
  } catch (error) {
    setText("cameraMessage", "Camera configuration unavailable. Use Reconnect video to retry.");
  } finally {
    button.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
}
// Dashboard startup
document.getElementById("expandCameras").addEventListener("click", toggleCameraFullscreen);
document.addEventListener("fullscreenchange", () => {
  setText("expandCameras", document.fullscreenElement === document.getElementById("cameraPanel")
    ? "Exit fullscreen" : "Fullscreen both cameras");
});
document.getElementById("reloadCameras").addEventListener("click", loadCameras);
loadCameras();
document.getElementById("roleBadge").textContent = "SensorHub";
renderTelemetry();
refreshTelemetry();
startTelemetryPolling();
connectLiveUpdates();
window.addEventListener("pagehide", () => {
  window.clearTimeout(reconnectTimer);
  stopTelemetryPolling();
  if (telemetrySocket) { telemetrySocket.onclose = null; telemetrySocket.close(); }
});
