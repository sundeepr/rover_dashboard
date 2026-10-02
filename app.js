const demoUsers = {
  admin: {
    password: "admin123",
    role: "admin",
    displayName: "Admin User",
  },
  operator: {
    password: "operator123",
    role: "user",
    displayName: "Normal User",
  },
};

const mockTelemetry = {
  sources: { devices: "mock", odometry: "mock" },
  status: {
    source: "mock",
    cpuUsage: "18.2%",
    memoryUsage: "42.5%",
    cpuTemp: "58 C",
    gpuUsage: "21.0%",
    updatedAt: new Date().toLocaleTimeString(),
  },
  devices: [
    { name: "Stereo Camera", port: "/dev/video0", status: "online" },
    { name: "Lidar", port: "/dev/ttyUSB0", status: "online" },
    { name: "GPS", port: "/dev/ttyTHS1", status: "online" },
    { name: "Motor Controller", port: "can0", status: "online" },
  ],
  odometry: {
    x: "12.48 m",
    y: "-3.12 m",
    heading: "42 deg",
    speed: "0.84 m/s",
    wheelTicks: "18234",
    frame: "odom",
  },
  sensors: [
    { name: "IMU", value: "0.02 g", detail: "Roll/Pitch stable" },
    { name: "GPS", value: "17 sats", detail: "Fix: RTK float" },
    { name: "Lidar", value: "24.6 m", detail: "Front clearance" },
    { name: "Ambient", value: "29.4 C", detail: "Board enclosure" },
  ],
  jetson: {
    memoryUsage: {
      ram: {
        percent: "42.5%",
        used: "3.4 GB",
        total: "8.0 GB",
        detail: "3.4 GB / 8.0 GB",
      },
      gpu: {
        available: false,
        value: "Shared with system RAM",
        detail: "Jetson uses unified memory.",
      },
    },
    details: [],
  },
  battery: {
    available: true,
    source: "mock",
    updatedAt: new Date().toLocaleTimeString(),
    status: "Healthy",
    state: "Discharging",
    summary: {
      capacityRemaining: "82.0%",
      totalVoltage: "52.67 V",
      current: "-7.50 A",
      power: "-395 W",
      cellDelta: "0.016 V",
    },
    temperatures: [
      { name: "Power Tube", value: "31.0 C" },
      { name: "Sensor 1", value: "27.0 C" },
      { name: "Sensor 2", value: "27.8 C" },
    ],
    cells: Array.from({ length: 16 }, (_, index) => ({
      index: index + 1,
      voltage: `${(3.29 + (index % 4) * 0.003).toFixed(3)} V`,
      rawVoltage: 3.29 + (index % 4) * 0.003,
    })),
    controls: {
      balancing: "On",
      charging: "Off",
      discharging: "On",
    },
    details: [],
  },
};

const HISTORY_LIMIT = 30;
const TELEMETRY_POLL_MS = 2000;

const state = {
  currentUser: null,
  baseUrl: getDefaultBaseUrl(),
  telemetry: mockTelemetry,
  histories: {
    cpuUsage: [],
    memoryUsage: [],
    cpuTemp: [],
    gpuUsage: [],
  },
  detailHistories: {},
  pollTimer: null,
};

const authPanel = document.getElementById("authPanel");
const dashboard = document.getElementById("dashboard");
const authMessage = document.getElementById("authMessage");
const loginForm = document.getElementById("loginForm");
const logoutButton = document.getElementById("logoutButton");
const roleBadge = document.getElementById("roleBadge");
const welcomeText = document.getElementById("welcomeText");
const adminPanel = document.getElementById("adminPanel");

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const formData = new FormData(loginForm);
  const username = String(formData.get("username") || "").trim();
  const password = String(formData.get("password") || "");
  authMessage.textContent = "Signing in...";

  const user = await loginUser(username, password);

  if (!user) {
    authMessage.textContent = "Invalid login. Try one of the demo accounts.";
    return;
  }

  state.currentUser = user;
  authMessage.textContent = "";
  loginForm.reset();
  renderSession();
  await refreshTelemetry();
  startTelemetryPolling();
});

logoutButton.addEventListener("click", () => {
  stopTelemetryPolling();
  state.currentUser = null;
  state.baseUrl = getDefaultBaseUrl();
  state.telemetry = mockTelemetry;
  resetMetricHistories();
  renderSession();
});

async function refreshTelemetry() {
  if (!state.baseUrl) {
    state.telemetry = {
      ...mockTelemetry,
      status: {
        ...mockTelemetry.status,
        updatedAt: new Date().toLocaleTimeString(),
      },
    };
    renderTelemetry();
    return;
  }

  try {
    const response = await fetch(`${state.baseUrl}/api/telemetry`, {
      headers: { Accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const telemetry = await response.json();
    state.telemetry = telemetry;
  } catch (error) {
    state.telemetry = {
      ...mockTelemetry,
      status: {
        ...mockTelemetry.status,
        updatedAt: new Date().toLocaleTimeString(),
      },
    };
  }

  renderTelemetry();
}

function renderSession() {
  const signedIn = Boolean(state.currentUser);
  authPanel.classList.toggle("hidden", signedIn);
  dashboard.classList.toggle("hidden", !signedIn);

  if (!signedIn) {
    return;
  }

  const { displayName, role } = state.currentUser;
  welcomeText.textContent =
    `${displayName} is signed in. Monitor rover devices, odometry, and sensor streams from one place.`;
  roleBadge.textContent = role === "admin" ? "Admin Access" : "Normal Access";
  adminPanel.classList.toggle("hidden", role !== "admin");
  renderTelemetry();
}

async function loginUser(username, password) {
  if (state.baseUrl) {
    try {
      const response = await fetch(`${state.baseUrl}/api/login`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ username, password }),
      });

      if (response.ok) {
        const data = await response.json();
        return {
          username: data.username,
          role: data.role,
          displayName: data.displayName,
        };
      }
    } catch (error) {
      authMessage.textContent =
        "Backend login unavailable. Using local demo accounts.";
    }
  }

  const demoUser = demoUsers[username];
  if (!demoUser || demoUser.password !== password) {
    return null;
  }

  return { username, ...demoUser };
}

function renderTelemetry() {
  const { status, devices, odometry } = state.telemetry;
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
              <strong>${device.name}</strong>
              <span>${device.port}</span>
            </div>
            <span class="device-status">${device.status}</span>
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
  if (window.location.protocol.startsWith("http")) {
    return window.location.origin;
  }

  return "http://127.0.0.1:6060";
}

function startTelemetryPolling() {
  stopTelemetryPolling();

  if (!state.currentUser) {
    return;
  }

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
          <span>${item.name}</span>
          <strong>${item.value}</strong>
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
  const payload = battery || mockTelemetry.battery;
  markSection("batterySummaryGrid", payload.available === false ? "unavailable" : payload.source || "mock");
  const capacityValue = parsePercent(payload.summary?.capacityRemaining);
  const capacity = capacityValue === null ? 0 : capacityValue;

  setText("batteryStatus", payload.status || "Unavailable");
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
  setText("gpsMessage", available ? "Position reported by the rover GPS receiver." : "Waiting for rover GPS data.");
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
  frame.classList.toggle("hidden", !available);
  link.classList.toggle("hidden", !available);
  document.getElementById("gpsMapPlaceholder").classList.toggle("hidden", available);

  if (!available) {
    frame.removeAttribute("src");
    link.removeAttribute("href");
    return;
  }

  const coordinates = `${gps.latitude.toFixed(6)},${gps.longitude.toFixed(6)}`;
  const mapUrl = `https://www.google.com/maps?q=${encodeURIComponent(coordinates)}&z=18&output=embed`;
  // Preserve map interaction across polling updates when the position is unchanged.
  if (frame.getAttribute("src") !== mapUrl) {
    frame.src = mapUrl;
  }
  link.href = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(coordinates)}`;
}
