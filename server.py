from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from rover_data import get_mock_telemetry


BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

USERS = {
    "admin": {
        "password": "admin123",
        "role": "admin",
        "display_name": "Admin User",
    },
    "operator": {
        "password": "operator123",
        "role": "user",
        "display_name": "Normal User",
    },
}


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/styles.css")
def styles():
    return send_from_directory(BASE_DIR, "styles.css")


@app.get("/app.js")
def script():
    return send_from_directory(BASE_DIR, "app.js")


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "service": "rover-dashboard-backend",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    )


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    user = USERS.get(username)
    if not user or user["password"] != password:
        return jsonify({"error": "invalid_credentials"}), 401

    return jsonify(
        {
            "username": username,
            "role": user["role"],
            "displayName": user["display_name"],
        }
    )


@app.get("/api/telemetry")
def telemetry():
    return jsonify(get_mock_telemetry())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6060, debug=True)
