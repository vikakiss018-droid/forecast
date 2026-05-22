from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
LIQUIDATIONS_DIR = DATA_DIR / "liquidations"
LIQUIDITY_DIR = DATA_DIR / "liquidity"
CONFIGS_DIR = PROJECT_ROOT / "configs"
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env(*, force: bool = False) -> None:
    """Load project .env into os.environ. force=True: file overrides existing env."""
    if not ENV_FILE.is_file():
        return
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=force)


def ensure_directories() -> None:
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LIQUIDATIONS_DIR.mkdir(parents=True, exist_ok=True)
    LIQUIDITY_DIR.mkdir(parents=True, exist_ok=True)
