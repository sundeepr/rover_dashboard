const HISTORY_LIMIT = 30;
const TELEMETRY_POLL_MS = 2000;

const state = {
  currentUser: null,
  baseUrl: getDefaultBaseUrl(),
  telemetry: unavailableTelemetry(),
  histories: {
    cpuUsage: [],
    memoryUsage: [],
    cpuTemp: [],
    gpuUsage: [],
  },
  detailHistories: {},
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
  if (state.metricSource !== metricSource) {
    resetMetricHistories();
    state.metricSource = metricSource;
  }
  markSection("metricsGrid", metricSource);
  markSection("devicesList", state.telemetry.sources?.devices || "mock");
  markSection("odometryGrid", state.telemetry.sources?.odometry || "mock");
  markSection("jetsonStatsGrid", state.telemetry.jetson?.available ? state.telemetry.jetson.source : "unavailable");
  renderGps(state.telemetry.gps);

  setText("cpuUsageValue", status.cpuUsage);
  setText("memoryUsageValue", status.memoryUsage);
  setText("cpuTempValue", status.cpuTemp);
  setText("gpuUsageValue", status.gpuUsage);
  updateMetricHistory("cpuUsage", status.cpuUsage, status.updatedAt);
  updateMetricHistory("memoryUsage", status.memoryUsage, status.updatedAt);
  updateMetricHistory("cpuTemp", status.cpuTemp, status.updatedAt);
  updateMetricHistory("gpuUsage", status.gpuUsage, status.updatedAt);
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

function resetMetricHistories() {
  state.histories = {
    cpuUsage: [],
    memoryUsage: [],
    cpuTemp: [],
    gpuUsage: [],
  };
  state.detailHistories = {};
}

function updateMetricHistory(metricKey, rawValue, updatedAt) {
  const value = metricKey === "cpuTemp" ? parseTemperature(rawValue) : parsePercent(rawValue);
  if (value === null) {
    return;
  }

  const history = state.histories[metricKey];
  const latestPoint = history.at(-1);
  if (latestPoint && latestPoint.label === updatedAt && latestPoint.value === value) {
    return;
  }

  history.push({
    value,
    label: updatedAt || new Date().toLocaleTimeString(),
  });

  if (history.length > HISTORY_LIMIT) {
    state.histories[metricKey] = history.slice(-HISTORY_LIMIT);
  }
}

function renderMetricChart(metricKey) {
  const line = document.getElementById(`${metricKey}Line`);
  const area = document.getElementById(`${metricKey}Area`);
  const history = state.histories[metricKey];

  if (!line || !area) {
    return;
  }

  if (history.length < 2) {
    line.setAttribute("d", "");
    area.setAttribute("d", "");
    return;
  }

  const width = 240;
  const height = 72;
  const bottom = height - 6;
  const top = 6;
  const usableHeight = bottom - top;
  const stepX = width / Math.max(history.length - 1, 1);
  const maxValue = metricKey === "cpuTemp" ? Math.max(...history.map((point) => point.value), 70) : 100;

  const points = history.map((point, index) => {
    const x = index * stepX;
    const y = bottom - (point.value / maxValue) * usableHeight;
    return { x, y };
  });

  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ");

  const areaPath = [
    `M ${points[0].x.toFixed(2)} ${bottom.toFixed(2)}`,
    ...points.map((point) => `L ${point.x.toFixed(2)} ${point.y.toFixed(2)}`),
    `L ${points.at(-1).x.toFixed(2)} ${bottom.toFixed(2)}`,
    "Z",
  ].join(" ");

  line.setAttribute("d", linePath);
  area.setAttribute("d", areaPath);
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

  items.forEach((item) => {
    if (shouldShowDetailTrendline(item.name)) {
      updateDetailHistory(item.name, item.value);
    }
  });

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

function updateDetailHistory(name, rawValue) {
  const normalizedName = String(name || "");
  const value = normalizedName.toLowerCase() === "ram" ? parsePercent(rawValue) : parseTemperature(rawValue);
  if (value === null) {
    return;
  }

  const history = state.detailHistories[normalizedName] || [];
  history.push(value);
  state.detailHistories[normalizedName] = history.slice(-HISTORY_LIMIT);
}

function renderDetailChart(name) {
  const chartId = detailChartId(name);
  const line = document.getElementById(`${chartId}Line`);
  const area = document.getElementById(`${chartId}Area`);
  const history = state.detailHistories[String(name)] || [];

  if (!line || !area || history.length < 2) {
    if (line) {
      line.setAttribute("d", "");
    }
    if (area) {
      area.setAttribute("d", "");
    }
    return;
  }

  const width = 240;
  const height = 56;
  const bottom = height - 4;
  const top = 4;
  const usableHeight = bottom - top;
  const stepX = width / Math.max(history.length - 1, 1);
  const maxValue = String(name).toLowerCase() === "ram" ? 100 : Math.max(...history, 70);

  const points = history.map((value, index) => {
    const x = index * stepX;
    const y = bottom - (value / maxValue) * usableHeight;
    return { x, y };
  });

  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ");
  const areaPath = [
    `M ${points[0].x.toFixed(2)} ${bottom.toFixed(2)}`,
    ...points.map((point) => `L ${point.x.toFixed(2)} ${point.y.toFixed(2)}`),
    `L ${points.at(-1).x.toFixed(2)} ${bottom.toFixed(2)}`,
    "Z",
  ].join(" ");

  line.setAttribute("d", linePath);
  area.setAttribute("d", areaPath);
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
  setText("gpsMessage", available ? "Position reported by the rover GPS receiver." : "World view · Waiting for rover GPS data.");
  renderGpsMap(gps, available);
  const items = [
    ["Latitude", available ? `${gps.latitude.toFixed(6)}°` : "--"],
    ["Longitude", available ? `${gps.longitude.toFixed(6)}°` : "--"],
    ["Altitude", available && Number.isFinite(gps.altitude) ? `${gps.altitude} m` : "--"],
    ["Satellites", available ? gps.satellites ?? "--" : "--"],
    ["Fix", available ? gps.fix ?? "--" : "--"],
    ["Last update", available ? gps.updatedAt ?? "--" : "--"],
  ];
  const grid = document.getElementById("gpsGrid");
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

function renderGpsMap(gps, available) {
  const frame = document.getElementById("gpsMapFrame");
  const link = document.getElementById("gpsMapLink");
  link.classList.toggle("hidden", !available);
  const coordinates = available
    ? `${gps.latitude.toFixed(6)},${gps.longitude.toFixed(6)}` : null;
  const mapUrl = available
    ? `https://www.google.com/maps?q=${encodeURIComponent(coordinates)}&z=18&output=embed`
    : "https://www.google.com/maps?ll=0,0&z=2&output=embed";
  // Preserve map interaction across polling updates when the position is unchanged.
  if (frame.getAttribute("src") !== mapUrl) {
    frame.src = mapUrl;
  }
  if (available) {
    link.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(coordinates)}`;
  } else {
    link.removeAttribute("href");
  }
}

const batteryHistory = { source: null, points: [], lastStamp: null };
const BATTERY_WINDOW_MS = 5 * 60 * 1000;

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
  const now = Date.now();
  // Clear samples when switching between real, simulated and mock sources.
  if (batteryHistory.source !== payload.source) {
    batteryHistory.points = [];
    batteryHistory.lastStamp = null;
    batteryHistory.source = payload.source;
  }
  batteryHistory.points = batteryHistory.points.filter((point) => point.time >= now - BATTERY_WINDOW_MS);
  const available = payload.available === true;
  const stamp = payload.updatedAt;
  if (!available || !stamp || stamp !== batteryHistory.lastStamp) {
    const capacity = available ? Number.parseFloat(payload.summary?.capacityRemaining) : NaN;
    const current = available ? Number.parseFloat(payload.summary?.current) : NaN;
    batteryHistory.points.push({ time: now,
      Capacity: Number.isFinite(capacity) && capacity >= 0 && capacity <= 100 ? capacity : null,
      Current: Number.isFinite(current) ? current : null });
    batteryHistory.lastStamp = stamp;
  }
  for (const key of ["Capacity", "Current"]) {
    const points = batteryHistory.points;
    const values = points.map((p) => p[key]).filter((value) => value !== null);
    const unit = key === "Capacity" ? "%" : "A";
    const latest = available ? Number.parseFloat(key === "Capacity" ? payload.summary?.capacityRemaining : payload.summary?.current) : NaN;
    setText(`battery${key}Latest`, Number.isFinite(latest) ? `${latest.toFixed(1)} ${unit}` : "Unavailable");
    const extent = Math.max(1, ...values.map(Math.abs)) * 1.1;
    const low = key === "Capacity" ? 0 : -extent;
    const high = key === "Capacity" ? 100 : extent;
    setText(`battery${key}Range`, `Scale: ${low.toFixed(0)} to ${high.toFixed(0)} ${unit}`);
    const y = (value) => 132 - (value - low) / (high - low) * 124;
    let path = "";
    let previous = null;
    let last = null;
    for (const point of points) {
      if (point[key] === null) { previous = null; continue; }
      const x = 8 + (point.time - (now - BATTERY_WINDOW_MS)) / BATTERY_WINDOW_MS * 384;
      const move = !previous || point.time - previous.time > 20000;
      path += `${move ? "M" : "L"} ${x.toFixed(2)} ${y(point[key]).toFixed(2)} `;
      previous = point;
      last = { x, y: y(point[key]) };
    }
    document.getElementById(`battery${key}Path`).setAttribute("d", path);
    document.getElementById(`battery${key}Zero`).setAttribute("d", `M 8 ${y(0)} L 392 ${y(0)}`);
    const dot = document.getElementById(`battery${key}Dot`);
    dot.setAttribute("visibility", last ? "visible" : "hidden");
    if (last) { dot.setAttribute("cx", last.x); dot.setAttribute("cy", last.y); }
    document.getElementById(`battery${key}Empty`).classList.toggle("hidden", values.length > 0);
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
}
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
