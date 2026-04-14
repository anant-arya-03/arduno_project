import atexit
import csv
import io
import json
import logging
import math
import os
import random
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Tuple

from flask import Flask, jsonify, render_template, request, send_file

try:
    import serial
    from serial import SerialException
except Exception:  # pragma: no cover
    serial = None
    SerialException = Exception

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("radar-web")

MAX_POINTS = int(os.getenv("RADAR_BUFFER_SIZE", "500"))
RADAR_MAX_DISTANCE = float(os.getenv("RADAR_MAX_DISTANCE", "250"))
READ_INTERVAL = float(os.getenv("RADAR_READ_INTERVAL", "0.03"))

SERIAL_PORT = (os.getenv("SERIAL_PORT") or os.getenv("RADAR_SERIAL_PORT") or "").strip()
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "9600"))
SERIAL_TIMEOUT = float(os.getenv("SERIAL_TIMEOUT", "1"))
SIMULATION_MODE = os.getenv("RADAR_SIMULATION", "auto").strip().lower()
DISABLE_READER_THREAD = os.getenv("RADAR_DISABLE_READER_THREAD", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
INGEST_TOKEN = os.getenv("RADAR_INGEST_TOKEN", "").strip()
MAX_INGEST_BATCH = max(1, int(os.getenv("RADAR_INGEST_MAX_BATCH", "200")))
SERVERLESS_AUTOSIM = os.getenv("RADAR_SERVERLESS_AUTOSIM", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SERVERLESS_FRESH_SECONDS = max(0.1, float(os.getenv("RADAR_SERVERLESS_FRESH_SECONDS", "2.5")))

points_lock = threading.Lock()
radar_points = deque(maxlen=MAX_POINTS)
latest_point = {
    "angle": 0.0,
    "distance": 0.0,
    "intensity": 0.0,
    "frequency": None,
    "ts": time.time(),
}

runtime_state = {
    "mode": "starting",
    "serial_port": SERIAL_PORT or None,
    "last_error": None,
}

stop_event = threading.Event()
reader_thread: Optional[threading.Thread] = None
serverless_state_lock = threading.Lock()
serverless_sweep_angle = 0.0


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def normalize_angle(angle: float) -> float:
    return angle % 360.0


def force_simulation() -> bool:
    return SIMULATION_MODE in {"1", "true", "yes", "on", "sim", "simulation"}


def disable_simulation() -> bool:
    return SIMULATION_MODE in {"0", "false", "no", "off"}


def derive_intensity(distance: float, frequency: Optional[float]) -> float:
    if frequency is not None:
        return clamp(frequency / 100.0, 0.0, 1.0)
    if RADAR_MAX_DISTANCE <= 0:
        return 0.0
    return clamp(1.0 - (distance / RADAR_MAX_DISTANCE), 0.0, 1.0)


def simulated_sample(angle: float, now: Optional[float] = None) -> Tuple[float, float, float, float]:
    if now is None:
        now = time.time()

    base = RADAR_MAX_DISTANCE * 0.38
    wave = RADAR_MAX_DISTANCE * 0.47 * (0.5 + 0.5 * math.sin(now * 1.8 + angle / 15.0))
    jitter = random.uniform(-5.0, 5.0)
    distance = clamp(base + wave + jitter, 5.0, RADAR_MAX_DISTANCE)

    frequency = 30.0 + 60.0 * (0.5 + 0.5 * math.sin(now * 2.6 + angle / 28.0))
    intensity = derive_intensity(distance, frequency)
    return angle, distance, intensity, frequency


def seed_serverless_point() -> None:
    global serverless_sweep_angle

    with serverless_state_lock:
        angle = serverless_sweep_angle
        serverless_sweep_angle = normalize_angle(serverless_sweep_angle + 2.5)

    point = simulated_sample(angle)
    add_point(*point)


def parse_serial_line(line: str) -> Optional[Tuple[float, float, float, Optional[float]]]:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2:
        return None

    try:
        angle = normalize_angle(float(parts[0]))
        distance = max(0.0, float(parts[1]))
        frequency = None
        if len(parts) >= 3 and parts[2] != "":
            frequency = float(parts[2])
        intensity = derive_intensity(distance, frequency)
        return angle, distance, intensity, frequency
    except ValueError:
        return None


def parse_ingest_point(item) -> Optional[Tuple[float, float, float, Optional[float]]]:
    if not isinstance(item, dict):
        return None

    if "angle" not in item or "distance" not in item:
        return None

    try:
        angle = normalize_angle(float(item.get("angle")))
        distance = max(0.0, float(item.get("distance")))

        frequency_raw = item.get("frequency")
        frequency = float(frequency_raw) if frequency_raw is not None and frequency_raw != "" else None

        intensity_raw = item.get("intensity")
        if intensity_raw is None or intensity_raw == "":
            intensity = derive_intensity(distance, frequency)
        else:
            intensity = clamp(float(intensity_raw), 0.0, 1.0)

        return angle, distance, intensity, frequency
    except (TypeError, ValueError):
        return None


def add_point(angle: float, distance: float, intensity: float, frequency: Optional[float]) -> None:
    now = time.time()
    point = {
        "angle": round(angle, 2),
        "distance": round(distance, 2),
        "intensity": round(clamp(intensity, 0.0, 1.0), 3),
        "frequency": round(frequency, 2) if frequency is not None else None,
        "ts": now,
    }
    with points_lock:
        radar_points.append(point)
        latest_point.update(point)


def run_simulation() -> None:
    runtime_state["mode"] = "simulation"
    logger.warning("Running in simulation mode. Configure SERIAL_PORT for live data.")

    sweep_angle = 0.0
    while not stop_event.is_set():
        add_point(*simulated_sample(sweep_angle))
        sweep_angle = normalize_angle(sweep_angle + 2.5)
        time.sleep(READ_INTERVAL)


def run_serial_reader() -> None:
    if serial is None:
        if disable_simulation():
            runtime_state["last_error"] = "pyserial unavailable"
            logger.error("pyserial is not available in the runtime.")
            runtime_state["mode"] = "error"
            return
        runtime_state["last_error"] = None
        logger.warning("pyserial unavailable; falling back to simulation mode.")
        run_simulation()
        return

    if not SERIAL_PORT:
        if disable_simulation():
            runtime_state["last_error"] = "SERIAL_PORT / RADAR_SERIAL_PORT not configured"
            runtime_state["mode"] = "error"
            logger.error("SERIAL_PORT (or RADAR_SERIAL_PORT) is required when simulation is disabled.")
            return
        runtime_state["last_error"] = None
        logger.info("No serial port configured; running simulation mode.")
        run_simulation()
        return

    ser = None
    try:
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
        runtime_state["mode"] = "serial"
        runtime_state["last_error"] = None
        logger.info("Connected to serial device on %s @ %s baud", SERIAL_PORT, SERIAL_BAUD)

        while not stop_event.is_set():
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
            if not raw:
                time.sleep(max(READ_INTERVAL * 0.5, 0.01))
                continue

            parsed = parse_serial_line(raw)
            if parsed is None:
                continue

            add_point(*parsed)

    except SerialException as exc:
        runtime_state["last_error"] = str(exc)
        logger.exception("Serial reader error: %s", exc)
        if disable_simulation():
            runtime_state["mode"] = "error"
            return
        run_simulation()
    except Exception as exc:  # pragma: no cover
        runtime_state["last_error"] = str(exc)
        logger.exception("Unexpected reader error: %s", exc)
        if disable_simulation():
            runtime_state["mode"] = "error"
            return
        run_simulation()
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def reader_loop() -> None:
    if force_simulation():
        run_simulation()
        return
    run_serial_reader()


def start_reader_thread() -> None:
    global reader_thread
    if reader_thread and reader_thread.is_alive():
        return
    stop_event.clear()
    reader_thread = threading.Thread(target=reader_loop, name="radar-reader", daemon=True)
    reader_thread.start()


def stop_reader_thread() -> None:
    stop_event.set()
    if reader_thread and reader_thread.is_alive():
        reader_thread.join(timeout=2.0)


@atexit.register
def _shutdown() -> None:
    stop_reader_thread()


@app.route("/")
def index() -> str:
    return render_template("index.html", max_distance=RADAR_MAX_DISTANCE)


@app.route("/data")
def data():
    if DISABLE_READER_THREAD and SERVERLESS_AUTOSIM:
        with points_lock:
            snapshot_len = len(radar_points)
            latest_ts = float(latest_point.get("ts", 0.0))

        has_fresh_point = snapshot_len > 0 and (time.time() - latest_ts) <= SERVERLESS_FRESH_SECONDS
        if not has_fresh_point:
            runtime_state["mode"] = "simulation"
            runtime_state["last_error"] = None
            seed_serverless_point()

    with points_lock:
        snapshot = list(radar_points)
        current = dict(latest_point)

    return jsonify(
        {
            "points": snapshot,
            "current": current,
            "count": len(snapshot),
            "bufferSize": MAX_POINTS,
            "mode": runtime_state.get("mode", "unknown"),
            "serialPort": runtime_state.get("serial_port"),
            "lastError": runtime_state.get("last_error"),
            "serverTime": time.time(),
            "maxDistance": RADAR_MAX_DISTANCE,
        }
    )


@app.route("/ingest", methods=["POST"])
def ingest():
    if INGEST_TOKEN:
        token = request.headers.get("X-Radar-Token", "").strip()
        if token != INGEST_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True)

    # Some serverless adapters can pass JSON bodies in ways Flask does not auto-parse.
    if payload is None:
        raw_body = request.get_data(cache=False, as_text=True)
        if raw_body:
            try:
                payload = json.loads(raw_body)
            except json.JSONDecodeError:
                payload = None

    if payload is None and request.form:
        candidate = request.form.get("payload") or request.form.get("data")
        if candidate:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                payload = None

    if payload is None:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    if isinstance(payload, dict) and isinstance(payload.get("points"), list):
        raw_points = payload.get("points")
    else:
        raw_points = [payload]

    accepted = 0
    dropped = 0

    for raw in raw_points[:MAX_INGEST_BATCH]:
        parsed = parse_ingest_point(raw)
        if parsed is None:
            dropped += 1
            continue
        add_point(*parsed)
        accepted += 1

    if accepted == 0:
        return jsonify({"ok": False, "error": "no_valid_points", "dropped": dropped}), 400

    runtime_state["mode"] = "ingest"
    runtime_state["last_error"] = None

    return jsonify(
        {
            "ok": True,
            "accepted": accepted,
            "dropped": dropped,
            "bufferSize": len(radar_points),
            "mode": runtime_state.get("mode", "unknown"),
        }
    )


@app.route("/save", methods=["GET", "POST"])
def save():
    with points_lock:
        snapshot = list(radar_points)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp_iso", "timestamp_unix", "angle_deg", "distance", "intensity", "frequency"])

    for point in snapshot:
        ts = point.get("ts", time.time())
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        writer.writerow(
            [
                iso,
                ts,
                point.get("angle"),
                point.get("distance"),
                point.get("intensity"),
                point.get("frequency"),
            ]
        )

    payload = io.BytesIO(output.getvalue().encode("utf-8"))
    payload.seek(0)
    filename = f"radar_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"

    return send_file(
        payload,
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv; charset=utf-8",
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "mode": runtime_state.get("mode", "unknown"),
            "bufferSize": len(radar_points),
            "serialConfigured": bool(SERIAL_PORT),
            "readerThreadEnabled": not DISABLE_READER_THREAD,
            "ingestTokenRequired": bool(INGEST_TOKEN),
            "serverlessAutoSimulation": SERVERLESS_AUTOSIM,
        }
    )


# Start reader at import time so production WSGI servers also receive live updates.
if not DISABLE_READER_THREAD:
    start_reader_thread()
else:
    runtime_state["mode"] = "simulation" if SERVERLESS_AUTOSIM else "idle"
    runtime_state["last_error"] = None


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
