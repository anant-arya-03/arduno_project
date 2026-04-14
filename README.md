# Real-Time 3D Radar System

A full-stack radar dashboard built with Flask + Three.js.

## Features

- Real-time serial ingest from Arduino (`angle,distance` or `angle,distance,frequency`)
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

## API Endpoints

- `GET /` - Radar dashboard
- `GET /data` - Live JSON telemetry
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
