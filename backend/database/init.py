"""`python -m backend.database.init` - creates all tables in the database named by DATABASE_URL."""
from ..config.settings import get_settings
from ..utils.logging_config import setup_logging
from .db import Database, DatabaseError


def main() -> None:
    s = get_settings()
    setup_logging(s.log_level, s.log_dir, s.secret_values())
    db = Database(s.database_url)
    try:
        db.init_schema()
    except DatabaseError as exc:
        print(f"\nFAILED: {exc}\nCheck DATABASE_URL in your .env file (example: postgresql://postgres:YOUR_PASSWORD@localhost:5432/travelai).")
        raise SystemExit(1)
    print(f"\nOK - database tables are ready ({db.engine}).")


if __name__ == "__main__":
    main()
