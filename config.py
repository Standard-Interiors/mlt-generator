import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
TEMPLATE_PATH = os.path.join(BASE_DIR, "data", "MLT_Template.xlsx")
MAX_CONTENT_LENGTH = 1024 * 1024 * 1024  # 1GB (IFC plans can be 500MB+)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
