"""Bridge Arduino serial data to the radar cloud ingest endpoint.

Expected serial line format:
    angle,distance
or
    angle,distance,frequency
"""

from __future__ import annotations

import argparse
import math
import os
import random
import statistics
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Generator, Iterable, List, Optional

import requests

try:
    import serial
    from serial import SerialException
except Exception:  # pragma: no cover
    serial = None
    SerialException = Exception


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arduino serial to radar cloud bridge")
    parser.add_argument(
        "--serial-port",
        default=os.getenv("SERIAL_PORT", ""),
        help="Serial port (for example COM4 or /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.getenv("SERIAL_BAUD", "9600")),
        help="Serial baud rate",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("SERIAL_TIMEOUT", "1")),
        help="Serial timeout in seconds",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("RADAR_INGEST_URL", "http://127.0.0.1:5000/ingest"),
        help="Ingest URL (for example https://radarproject.vercel.app/ingest)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("RADAR_INGEST_TOKEN", ""),
        help="Token sent in X-Radar-Token header (optional)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("RADAR_BRIDGE_BATCH_SIZE", "10")),
        help="Number of points per HTTP request",
    )
    parser.add_argument(
        "--flush-interval",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_FLUSH_INTERVAL", "0.4")),
        help="Maximum seconds to wait before posting a partial batch",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_HTTP_TIMEOUT", "8")),
        help="HTTP request timeout in seconds",
    )
    parser.add_argument(
        "--simulation",
        action="store_true",
        help="Generate synthetic points instead of reading serial",
    )
    parser.add_argument(
        "--showcase",
        action="store_true",
        default=env_flag("RADAR_BRIDGE_SHOWCASE", False),
        help="Apply low-latency showcase presets for hand-detection demos.",
    )
    parser.add_argument(
        "--servo-mode",
        choices=["auto", "raw", "centered"],
        default=os.getenv("RADAR_BRIDGE_SERVO_MODE", "auto").strip().lower() or "auto",
        help="Servo angle interpretation. 'centered' maps 0-180 to -90..90.",
    )
    parser.add_argument(
        "--invert-angle",
        action="store_true",
        default=env_flag("RADAR_BRIDGE_INVERT_ANGLE", False),
        help="Invert angle direction after servo mode transform.",
    )
    parser.add_argument(
        "--angle-offset",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_ANGLE_OFFSET", "0")),
        help="Angle offset in degrees applied after transform.",
    )
    parser.add_argument(
        "--angle-bin-deg",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_ANGLE_BIN_DEG", "2")),
        help="Angle bin size (deg) used for per-angle smoothing.",
    )
    parser.add_argument(
        "--distance-min",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_DISTANCE_MIN", "3")),
        help="Reject distances below this threshold (cm).",
    )
    parser.add_argument(
        "--distance-max",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_DISTANCE_MAX", os.getenv("RADAR_MAX_DISTANCE", "250"))),
        help="Reject distances above this threshold (cm).",
    )
    parser.add_argument(
        "--distance-scale",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_DISTANCE_SCALE", "1.0")),
        help="Multiply incoming distance by this scale before filtering.",
    )
    parser.add_argument(
        "--distance-offset",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_DISTANCE_OFFSET", "0")),
        help="Add this offset (cm) to incoming distance before filtering.",
    )
    parser.add_argument(
        "--median-window",
        type=int,
        default=int(os.getenv("RADAR_BRIDGE_MEDIAN_WINDOW", "5")),
        help="Median filter window size per angle bin.",
    )
    parser.add_argument(
        "--smooth-alpha",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_SMOOTH_ALPHA", "0.35")),
        help="EMA smoothing factor after median filter (0..1).",
    )
    parser.add_argument(
        "--fast-alpha",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_FAST_ALPHA", "0.78")),
        help="Faster EMA factor when object moves closer (0..1).",
    )
    parser.add_argument(
        "--fast-near-distance",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_FAST_NEAR_DISTANCE", "90")),
        help="Enable fast-alpha when target is closer than this distance (cm).",
    )
    parser.add_argument(
        "--max-step-change",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_MAX_STEP_CHANGE", "18")),
        help="Maximum allowed distance change per sample for a bin (cm).",
    )
    parser.add_argument(
        "--jump-reject-cm",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_JUMP_REJECT_CM", "55")),
        help="Reject improbable one-sample jumps larger than this value (cm).",
    )
    parser.add_argument(
        "--bin-timeout-sec",
        type=float,
        default=float(os.getenv("RADAR_BRIDGE_BIN_TIMEOUT_SEC", "2.0")),
        help="Reset a bin if no update arrives for this duration (seconds).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed per-post logs.",
    )
    return parser.parse_args()


class PointCalibrator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.servo_mode = args.servo_mode
        self.invert_angle = bool(args.invert_angle)
        self.angle_offset = float(args.angle_offset)
        self.angle_bin_deg = max(0.5, float(args.angle_bin_deg))

        self.distance_min = max(0.0, float(args.distance_min))
        self.distance_max = max(self.distance_min + 1.0, float(args.distance_max))
        self.distance_scale = float(args.distance_scale)
        self.distance_offset = float(args.distance_offset)

        self.median_window = max(1, int(args.median_window))
        self.smooth_alpha = clamp(float(args.smooth_alpha), 0.0, 1.0)
        self.fast_alpha = clamp(float(args.fast_alpha), 0.0, 1.0)
        self.fast_near_distance = max(self.distance_min, float(args.fast_near_distance))
        self.max_step_change = max(0.0, float(args.max_step_change))
        self.jump_reject_cm = max(0.0, float(args.jump_reject_cm))
        self.bin_timeout_sec = max(0.0, float(args.bin_timeout_sec))

        self._history: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=self.median_window))
        self._smoothed: Dict[int, float] = {}
        self._last_seen_ts: Dict[int, float] = {}

        self._auto_min_angle = 9999.0
        self._auto_max_angle = -9999.0
        self._auto_samples = 0

    def current_servo_mode(self) -> str:
        if self.servo_mode != "auto":
            return self.servo_mode
        if self._auto_samples == 0:
            return "auto"
        # Most servo-based ultrasonic scans are 0..180 and should stay raw for front-arc mapping.
        if self._auto_min_angle >= -5.0 and self._auto_max_angle <= 185.0:
            return "raw"
        return "raw"

    def _transform_angle(self, angle: float) -> float:
        self._auto_samples += 1
        self._auto_min_angle = min(self._auto_min_angle, angle)
        self._auto_max_angle = max(self._auto_max_angle, angle)

        mode = self.current_servo_mode()
        transformed = angle

        if mode == "centered":
            transformed = transformed - 90.0

        if self.invert_angle:
            transformed = -transformed

        transformed = (transformed + self.angle_offset) % 360.0
        return transformed

    def _transform_distance(self, distance: float) -> Optional[float]:
        corrected = (distance * self.distance_scale) + self.distance_offset
        if corrected < self.distance_min or corrected > self.distance_max:
            return None
        return corrected

    def apply(self, point: Dict[str, float]) -> Optional[Dict[str, float]]:
        raw_angle = float(point.get("angle", 0.0))
        raw_distance = float(point.get("distance", 0.0))
        now = time.time()

        angle = self._transform_angle(raw_angle)
        distance = self._transform_distance(raw_distance)
        if distance is None:
            return None

        bin_key = int(round(angle / self.angle_bin_deg))
        history = self._history[bin_key]

        last_seen = self._last_seen_ts.get(bin_key)
        if last_seen is not None and self.bin_timeout_sec > 0 and (now - last_seen) > self.bin_timeout_sec:
            history.clear()
            self._smoothed.pop(bin_key, None)

        history.append(distance)

        median_distance = float(statistics.median(history))
        previous = self._smoothed.get(bin_key)

        if previous is None:
            filtered = median_distance
        else:
            candidate = median_distance

            if self.jump_reject_cm > 0 and abs(candidate - previous) > self.jump_reject_cm:
                if candidate > previous:
                    # Upward spikes are usually bad ultrasonic echoes.
                    candidate = previous
                else:
                    candidate = previous - self.jump_reject_cm

            alpha = self.smooth_alpha
            if candidate < previous and candidate <= self.fast_near_distance:
                alpha = self.fast_alpha

            filtered = previous + alpha * (candidate - previous)
            if self.max_step_change > 0:
                delta = filtered - previous
                if abs(delta) > self.max_step_change:
                    filtered = previous + (self.max_step_change if delta > 0 else -self.max_step_change)

        filtered = clamp(filtered, self.distance_min, self.distance_max)
        self._smoothed[bin_key] = filtered
        self._last_seen_ts[bin_key] = now

        out = dict(point)
        out["angle"] = round(angle, 2)
        out["distance"] = round(filtered, 2)
        return out


def parse_serial_point(line: str) -> Optional[Dict[str, float]]:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return None

    try:
        angle = float(parts[0]) % 360.0
        distance = max(0.0, float(parts[1]))

        point: Dict[str, float] = {
            "angle": round(angle, 2),
            "distance": round(distance, 2),
        }

        if len(parts) >= 3 and parts[2] != "":
            point["frequency"] = round(float(parts[2]), 2)

        return point
    except ValueError:
        return None


def serial_lines(port: str, baud: int, timeout: float) -> Iterable[str]:
    if serial is None:
        raise RuntimeError("pyserial is not available")
    if not port:
        raise RuntimeError("Missing serial port. Pass --serial-port or set SERIAL_PORT.")

    with serial.Serial(port, baud, timeout=timeout) as ser:
        print(f"Connected to {port} @ {baud} baud")
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line:
                yield line


def simulation_lines() -> Generator[str, None, None]:
    sweep = 0.0
    while True:
        now = time.time()
        distance = 80 + 65 * (0.5 + 0.5 * math.sin(now * 2.1 + sweep / 22.0)) + random.uniform(-5, 5)
        frequency = 30 + 45 * (0.5 + 0.5 * math.sin(now * 2.6 + sweep / 30.0))
        yield f"{sweep:.2f},{max(5.0, distance):.2f},{frequency:.2f}"
        sweep = (sweep + 3.0) % 360.0
        time.sleep(0.05)


def post_points(
    endpoint: str,
    token: str,
    points: List[Dict[str, float]],
    http_timeout: float,
    verbose: bool,
) -> bool:
    headers = {}
    if token:
        headers["X-Radar-Token"] = token

    try:
        response = requests.post(
            endpoint,
            json={"points": points},
            headers=headers,
            timeout=http_timeout,
        )
        if response.ok:
            if verbose:
                payload = response.json()
                print(f"POST ok accepted={payload.get('accepted')} dropped={payload.get('dropped')}")
            return True

        print(f"POST failed status={response.status_code} body={response.text[:160]}")
        return False
    except requests.RequestException as exc:
        print(f"POST failed error={exc}")
        return False


def main() -> int:
    args = parse_args()

    if args.showcase:
        args.batch_size = 1
        args.flush_interval = min(args.flush_interval, 0.05)
        args.angle_bin_deg = 2.0
        args.median_window = 5
        args.smooth_alpha = 0.28
        args.fast_alpha = 0.82
        args.fast_near_distance = 95.0
        args.max_step_change = 20.0
        args.jump_reject_cm = 55.0
        args.bin_timeout_sec = 2.0
        if args.servo_mode == "auto":
            args.servo_mode = "raw"

    batch_size = max(1, args.batch_size)
    flush_interval = max(0.05, args.flush_interval)
    calibrator = PointCalibrator(args)

    source = simulation_lines() if args.simulation else serial_lines(args.serial_port, args.baud, args.timeout)

    buffer: List[Dict[str, float]] = []
    last_flush = time.time()
    last_stat_log = time.time()
    total_points_sent = 0
    total_batches_sent = 0

    print(f"Sending to {args.endpoint}")
    if args.token:
        print("Token auth: enabled")
    else:
        print("Token auth: disabled")
    print(
        "Filter settings: "
        + f"servoMode={args.servo_mode} "
        + f"bin={calibrator.angle_bin_deg}deg "
        + f"median={calibrator.median_window} "
        + f"alpha={calibrator.smooth_alpha}/{calibrator.fast_alpha} "
        + f"fastNear<={calibrator.fast_near_distance}cm "
        + f"jumpReject={calibrator.jump_reject_cm}cm "
        + f"distanceRange={calibrator.distance_min}-{calibrator.distance_max}cm"
    )

    try:
        for raw_line in source:
            point = parse_serial_point(raw_line)
            if point is None:
                continue

            point = calibrator.apply(point)
            if point is None:
                continue

            buffer.append(point)
            now = time.time()
            should_flush = len(buffer) >= batch_size or (now - last_flush) >= flush_interval

            if not should_flush:
                continue

            if post_points(args.endpoint, args.token, buffer, args.http_timeout, args.verbose):
                total_points_sent += len(buffer)
                total_batches_sent += 1
                buffer.clear()
                last_flush = now

                if not args.verbose and (now - last_stat_log) >= 2.0:
                    print(
                        "stream ok "
                        + f"batches={total_batches_sent} "
                        + f"points={total_points_sent} "
                        + f"servoMode={calibrator.current_servo_mode()}"
                    )
                    last_stat_log = now
            else:
                time.sleep(0.5)
                if len(buffer) > batch_size * 30:
                    # Prevent unbounded memory growth if endpoint is down.
                    buffer = buffer[-batch_size * 20 :]

    except KeyboardInterrupt:
        print("\nBridge stopped.")
        if buffer:
            post_points(args.endpoint, args.token, buffer, args.http_timeout, args.verbose)
        return 0
    except (RuntimeError, SerialException) as exc:
        print(f"Bridge error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
