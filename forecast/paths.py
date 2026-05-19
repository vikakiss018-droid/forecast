from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
LIQUIDATIONS_DIR = DATA_DIR / "liquidations"
LIQUIDITY_DIR = DATA_DIR / "liquidity"
CONFIGS_DIR = PROJECT_ROOT / "configs"


def ensure_directories() -> None:
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LIQUIDATIONS_DIR.mkdir(parents=True, exist_ok=True)
    LIQUIDITY_DIR.mkdir(parents=True, exist_ok=True)
