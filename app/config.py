from pathlib import Path

APP_TITLE = "Mini-SCADA"
VIEWPORT_W = 1450
VIEWPORT_H = 980
FONT_SIZE = 18

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = str(DATA_DIR / "step7trend.db")

S7_POLL_INTERVAL = 1.0
