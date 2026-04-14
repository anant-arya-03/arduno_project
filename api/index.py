import os
import pathlib
import sys

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("RADAR_SIMULATION", "true")
os.environ.setdefault("RADAR_DISABLE_READER_THREAD", "true")

from radar_web import app
