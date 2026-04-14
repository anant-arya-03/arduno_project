# Real-Time 3D Radar System

A full-stack radar dashboard built with Flask + Three.js.

## Features

- Real-time serial ingest from Arduino (`angle,distance` or `angle,distance,frequency`)
- Optional cloud ingest endpoint (`/ingest`) for serial bridge uploads
- Threaded reader with rolling buffer (default 500 points)
- 3D radar visualization with sweep beam, pulsing waves, glow points, and heatmap colors
- CSV snapshot export from `/save`
- Mobile-responsive dashboard
- Cloud-ready deployment on Render and Vercel (simulation mode by default)

## Project Structure

- `radar_web.py` - Flask backend + serial/simulation reader
- `templates/index.html` - dashboard layout + UI panel
- `static/script.js` - Three.js rendering and animation loop
- `radar.py` - serial/simulation stream inspector utility
- `arduino_bridge.py` - serial-to-HTTP bridge (Arduino -> cloud `/ingest`)
- `requirements.txt` - Python dependencies
- `render.yaml` - Render Blueprint deployment config
- `vercel.json` - Vercel routing and build config
- `Procfile` - process entry for PaaS platforms
- `arduino/radar_sender/radar_sender.ino` - sample Arduino sketch

## Data Format From Arduino

Each line over serial should be one of:

- `angle,distance`
- `angle,distance,frequency`

Examples:

- `42,118`
- `42,118,37.5`

## Local Run (Simulation)

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:RADAR_SIMULATION = "true"
python radar_web.py
```

Open: `http://127.0.0.1:5000`

## Local Production-Style Run (Windows)

Gunicorn is Linux-oriented and is used on Render. On Windows, use Waitress:

```powershell
$env:RADAR_SIMULATION = "true"
python -m waitress --host 127.0.0.1 --port 5000 wsgi:app
```

Open: `http://127.0.0.1:5000`

## Local Run (Hardware / Serial)

### Windows PowerShell

```powershell
$env:SERIAL_PORT = "COM4"
$env:SERIAL_BAUD = "9600"
$env:SERIAL_TIMEOUT = "1"
$env:RADAR_SIMULATION = "false"
python radar_web.py
```

If hardware is unavailable, set `RADAR_SIMULATION=true`.

## Stream Inspector Utility

Use this to validate serial output before running the dashboard:

```powershell
python radar.py --simulation
python radar.py --serial-port COM4 --baud 9600
```

## Connect Arduino IDE Code

Your Arduino sketch should print one line per sample in this exact format:

- `angle,distance`
- `angle,distance,frequency`

### Connect Arduino to Local App

```powershell
$env:SERIAL_PORT = "COM4"
$env:SERIAL_BAUD = "9600"
$env:RADAR_SIMULATION = "false"
python radar_web.py
```

### Connect Arduino to Deployed Vercel App

Because Vercel cannot read USB serial directly, use the included bridge script on your PC:

1. In Vercel Project Settings -> Environment Variables, set:
	- `RADAR_INGEST_TOKEN` to a secret value (for example `myRadarToken123`)
2. Run bridge from your computer (where Arduino is connected):

```powershell
python arduino_bridge.py --serial-port COM4 --baud 9600 --endpoint https://radarproject.vercel.app/ingest --token myRadarToken123
```

Keep this bridge process running while viewing the dashboard.

### Accuracy Tuning For Ultrasonic + Servo

For HC-SR04 style sensors mounted on a servo, use filtered bridge settings:

```powershell
python arduino_bridge.py --serial-port COM3 --baud 9600 --endpoint https://radarproject.vercel.app/ingest --batch-size 1 --flush-interval 0.05 --servo-mode auto --median-window 5 --smooth-alpha 0.35 --max-step-change 18 --angle-bin-deg 2 --distance-min 3 --distance-max 250
```

If left/right appears mirrored, add:

```powershell
--invert-angle
```

If the sweep is rotated, add for calibration:

```powershell
--angle-offset -90
```

Important: close Arduino Serial Monitor before running the bridge, otherwise COM port locks can cause stale or inaccurate updates.

## API Endpoints

- `GET /` - Radar dashboard
- `GET /data` - Live JSON telemetry
- `POST /ingest` - Push one or many points from external bridge/device
- `GET or POST /save` - Download CSV snapshot
- `GET /health` - Health and mode status

## Deploy to Render

### Option A: Render Blueprint (Recommended)

1. Push this repository to GitHub.
2. In Render, select **New +** -> **Blueprint**.
3. Select your repository. Render reads `render.yaml` automatically.
4. Deploy.

Default cloud setup runs in simulation mode (`RADAR_SIMULATION=true`).

### Option B: Manual Render Web Service

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn --workers 1 --threads 4 --timeout 120 wsgi:app`
- Health Check Path: `/health`

Note: Gunicorn is for Linux hosts (Render, Fly, Railway Linux images, VPS Linux).

Set environment variables as needed from `.env.example`.

## Deploy to Vercel

Vercel deployment uses serverless mode, so the app automatically enables:

- `RADAR_SIMULATION=true`
- `RADAR_DISABLE_READER_THREAD=true`

If you use cloud ingest from Arduino bridge, keep `RADAR_SERVERLESS_AUTOSIM=true` to auto-fill only when no recent live point exists.

### Option A: Vercel Dashboard

1. Import your GitHub repository into Vercel.
2. Keep framework preset as **Other**.
3. Deploy.

### Option B: Vercel CLI

```powershell
npm install -g vercel
vercel
vercel --prod
```

After deploy, verify:

- `/health`
- `/data`
- `/save`

## Production Notes

- Keep Gunicorn workers at `1` when reading from a single serial device.
- Use threads to handle concurrent HTTP requests while one reader thread ingests telemetry.
- For cloud usage without direct USB access, simulation mode is expected.
