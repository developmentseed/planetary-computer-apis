import asyncio
import os

import asyncpg
import pytest
from pypgstac import pypgstac
from starlette.testclient import TestClient

DATA_DIR = os.path.join(os.path.dirname(__file__), "data-files", "naip")
collections = os.path.join(DATA_DIR, "collections.json")
items = os.path.join(DATA_DIR, "items.json")


@pytest.fixture(scope="session")
def event_loop():
    return asyncio.get_event_loop()


@pytest.fixture(scope="session")
async def app_client():
    """Setup DB and application."""
    print("Setting up test db")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("POSTGRES_DBNAME", "pgstactestdb")

        # Setting this environment variable to ensure links are properly constructed
        mp.setenv("TILER_HREF_ENV_VAR", "http://localhost:8080/stac/dqe")

        from stac_fastapi.pgstac.config import Settings
        from stac_fastapi.pgstac.db import close_db_connection, connect_to_db

        from pcstac.main import app

        # Testing is set to false because creating/updating via the API is not desirable
        #  thus, we actually want to use the migrations and data loaded in via the setup
        #  script which builds PQE docker containers
        settings = Settings(testing=False)
        assert settings.postgres_dbname == "pgstactestdb"

        # First connect to the default DB (which should be `postgres`)
        defaut_dsn = settings.reader_connection_string.replace(
            "/pgstactestdb", "/postgres"
        )
        print(f"Connecting to write database {defaut_dsn}")
        print("writer conn string", defaut_dsn)
        conn = await asyncpg.connect(dsn=defaut_dsn)

        print("creating temporary `pgstactestdb` database...")
        try:
            await conn.execute("CREATE DATABASE pgstactestdb;")
            await conn.execute(
                "ALTER DATABASE pgstactestdb SET search_path to pgstac, public;"
            )
        except asyncpg.exceptions.DuplicateDatabaseError:
            print("pgstactestdb already exists, cleaning it...")
            await conn.execute("DROP DATABASE pgstactestdb;")
            await conn.execute("CREATE DATABASE pgstactestdb;")
            await conn.execute(
                "ALTER DATABASE pgstactestdb SET search_path to pgstac, public;"
            )
        finally:
            await conn.close()

        try:
            print("migrating...")
            conn = await asyncpg.connect(dsn=settings.reader_connection_string)
            val = await conn.fetchval("SELECT true")
            assert val
            await conn.close()

            version = await pypgstac.run_migration(
                dsn=settings.reader_connection_string
            )
            print(f"PGStac Migrated to {version}")

            print("Registering collection and items")
            conn = await asyncpg.connect(dsn=settings.reader_connection_string)
            await conn.copy_to_table(
                "collections",
                source=collections,
                columns=["content"],
                format="csv",
                quote=chr(27),
                delimiter=chr(31),
            )
            # Make sure we have our collection
            val = await conn.fetchval("SELECT COUNT(*) FROM collections")
            print(f"registered {val} collection")
            assert val == 1

            await conn.copy_to_table(
                "items_staging",
                source=items,
                columns=["content"],
                format="csv",
                quote=chr(27),
                delimiter=chr(31),
            )
            # Make sure we have all our items
            val = await conn.fetchval("SELECT COUNT(*) FROM items")
            print(f"registered {val} items")
            assert val == 12

            await conn.close()

            await connect_to_db(app)
            yield TestClient(app)
            await close_db_connection(app)

        finally:
            print()
            print("Getting rid of test database")
            conn = await asyncpg.connect(dsn=defaut_dsn)
            await conn.execute("DROP DATABASE pgstactestdb;")
            await conn.close()
