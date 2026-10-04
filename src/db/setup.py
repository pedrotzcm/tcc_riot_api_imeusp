import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()

SCHEMA = Path(__file__).parent / "schema.sql"


def main() -> None:
    dsn = os.environ["DATABASE_URL"]
    sql = SCHEMA.read_text()
    with psycopg.connect(dsn) as conn:
        conn.execute(sql)
        conn.commit()
    print("Schema aplicado")


if __name__ == "__main__":
    main()
