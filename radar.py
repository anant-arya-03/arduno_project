"""Utility to inspect a radar serial stream.

Expected incoming line format from Arduino:
    angle,distance
or
    angle,distance,frequency

This script is optional and is not used by the Flask web app directly.
It is helpful for validating hardware output without running the UI.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import time
from typing import Generator, Iterable, Optional

try:
    import serial
    from serial import SerialException
except Exception:  # pragma: no cover
    serial = None
    SerialException = Exception


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Radar serial stream inspector")
    parser.add_argument(
        "--serial-port",
        default=os.getenv("SERIAL_PORT", ""),
        help="Serial port (for example COM4 or /dev/ttyUSB0). Can also use SERIAL_PORT env var.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.getenv("SERIAL_BAUD", "9600")),
        help="Serial baud rate. Can also use SERIAL_BAUD env var.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("SERIAL_TIMEOUT", "1")),
        help="Serial timeout in seconds.",
    )
    parser.add_argument(
        "--simulation",
        action="store_true",
        help="Generate synthetic radar lines instead of reading a serial device.",
    )
    parser.add_argument(
        "--max-distance",
        type=float,
        default=float(os.getenv("RADAR_MAX_DISTANCE", "250")),
        help="Maximum simulated distance (cm).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("RADAR_READ_INTERVAL", "0.03")),
        help="Line interval in seconds for simulation or read loop pacing.",
    )
    return parser.parse_args()


def simulation_lines(max_distance: float, interval: float) -> Generator[str, None, None]:
    sweep_angle = 0.0
    while True:
        now = time.time()
        base = max_distance * 0.4
        wave = max_distance * 0.45 * (0.5 + 0.5 * math.sin(now * 1.9 + sweep_angle / 15.0))
        jitter = random.uniform(-4.0, 4.0)
        distance = max(0.0, min(max_distance, base + wave + jitter))
        frequency = 30.0 + 60.0 * (0.5 + 0.5 * math.sin(now * 2.3 + sweep_angle / 23.0))

        yield f"{sweep_angle:.2f},{distance:.2f},{frequency:.2f}"
        sweep_angle = (sweep_angle + 2.5) % 360.0
        time.sleep(interval)


def serial_lines(port: str, baud: int, timeout: float, interval: float) -> Iterable[str]:
    if serial is None:
        raise RuntimeError("pyserial is not available in this environment")
    if not port:
        raise RuntimeError("No serial port configured. Use --serial-port or SERIAL_PORT.")

    with serial.Serial(port, baud, timeout=timeout) as ser:
        print(f"Connected to {port} @ {baud} baud")
        while True:
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
            if raw:
                yield raw
            else:
                time.sleep(max(interval * 0.5, 0.01))


def normalize_line(line: str) -> Optional[str]:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return None

    try:
        angle = float(parts[0]) % 360.0
        distance = max(0.0, float(parts[1]))
        if len(parts) >= 3 and parts[2]:
            frequency = float(parts[2])
            return f"{angle:.2f},{distance:.2f},{frequency:.2f}"
        return f"{angle:.2f},{distance:.2f}"
    except ValueError:
        return None


def main() -> int:
    args = parse_args()
    use_sim = args.simulation or not args.serial_port

    if use_sim:
        print("Running in simulation mode (no serial hardware required).")
        source = simulation_lines(args.max_distance, args.interval)
    else:
        try:
            source = serial_lines(args.serial_port, args.baud, args.timeout, args.interval)
        except (RuntimeError, SerialException) as exc:
            print(f"Unable to start serial reader: {exc}")
            return 1

    try:
        for raw in source:
            line = normalize_line(raw)
            if line:
                print(line)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return 1
    except SerialException as exc:
        print(f"Serial error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())