from psycopg_pool import ConnectionPool


def open_pool(dsn: str) -> ConnectionPool:
    pool = ConnectionPool(dsn, min_size=1, max_size=5, open=False, kwargs={"autocommit": True})
    pool.open()
    return pool


def ping(pool: ConnectionPool) -> bool:
    try:
        with pool.connection(timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return False
    return True
