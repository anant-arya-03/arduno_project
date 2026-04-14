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
import time
from typing import Dict, Generator, Iterable, List, Optional

import requests

try:
    import serial
    from serial import SerialException
except Exception:  # pragma: no cover
    serial = None
    SerialException = Exception


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
    return parser.parse_args()


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


def post_points(endpoint: str, token: str, points: List[Dict[str, float]], http_timeout: float) -> bool:
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
    batch_size = max(1, args.batch_size)
    flush_interval = max(0.05, args.flush_interval)

    source = simulation_lines() if args.simulation else serial_lines(args.serial_port, args.baud, args.timeout)

    buffer: List[Dict[str, float]] = []
    last_flush = time.time()

    print(f"Sending to {args.endpoint}")
    if args.token:
        print("Token auth: enabled")
    else:
        print("Token auth: disabled")

    try:
        for raw_line in source:
            point = parse_serial_point(raw_line)
            if point is None:
                continue

            buffer.append(point)
            now = time.time()
            should_flush = len(buffer) >= batch_size or (now - last_flush) >= flush_interval

            if not should_flush:
                continue

            if post_points(args.endpoint, args.token, buffer, args.http_timeout):
                buffer.clear()
                last_flush = now
            else:
                time.sleep(0.5)
                if len(buffer) > batch_size * 30:
                    # Prevent unbounded memory growth if endpoint is down.
                    buffer = buffer[-batch_size * 20 :]

    except KeyboardInterrupt:
        print("\nBridge stopped.")
        if buffer:
            post_points(args.endpoint, args.token, buffer, args.http_timeout)
        return 0
    except (RuntimeError, SerialException) as exc:
        print(f"Bridge error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
