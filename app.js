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
  status: {
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
const connectionForm = document.getElementById("connectionForm");
const baseUrlInput = document.getElementById("baseUrlInput");
const connectionPill = document.getElementById("connectionPill");
const connectionMessage = document.getElementById("connectionMessage");
const cpuChartMeta = document.getElementById("cpuChartMeta");
baseUrlInput.value = state.baseUrl;

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
  baseUrlInput.value = state.baseUrl;
  renderSession();
});

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  state.baseUrl = baseUrlInput.value.trim();
  await refreshTelemetry();
  startTelemetryPolling();
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
    connectionPill.textContent = "Mock Mode";
    connectionMessage.textContent =
      "No live endpoint yet. Showing mock rover telemetry.";
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
    connectionPill.textContent = "Live Link";
    connectionMessage.textContent = `Connected to ${state.baseUrl}/api/telemetry`;
  } catch (error) {
    state.telemetry = {
      ...mockTelemetry,
      status: {
        ...mockTelemetry.status,
        updatedAt: new Date().toLocaleTimeString(),
      },
    };
    connectionPill.textContent = "Fallback";
    connectionMessage.textContent =
      `Could not reach ${state.baseUrl}/api/telemetry. Falling back to mock data.`;
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
  const { status, devices, odometry, sensors } = state.telemetry;

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

  const sensorGrid = document.getElementById("sensorGrid");
  sensorGrid.innerHTML = sensors
    .map(
      (sensor) => `
        <article class="sensor-card">
          <h4>${sensor.name}</h4>
          <div class="sensor-value">${sensor.value}</div>
          <p>${sensor.detail}</p>
        </article>
      `,
    )
    .join("");
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
