from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
TMP_DIR = DATA_DIR / "tmp"  # DuckDB spill dir, on D: with the app (principal D-007)
DUCKDB_MEMORY_LIMIT = "2GB"  # 15 GB box often <1 GB free; default limit OOM-ed the SNAP-003 export

GAMMA = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

PAGE_LIMIT = 1000  # v2 max (census: 5000 -> 400)

# Fields dropped from every derived table (QA ruling D2). Raw snapshots stay byte-exact.
IDENTITY_FIELDS = frozenset({
    "name", "pseudonym", "bio", "profile_image", "profile_image_optimized",
    "display_username_public", "verified",
})
