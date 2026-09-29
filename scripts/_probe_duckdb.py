import duckdb

for mb in (16, 32, 64, 71, 128):
    try:
        c = duckdb.connect(":memory:")
        c.execute("PRAGMA memory_limit='%dMB'" % mb)
        v = c.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        print(mb, "->", v)
        c.close()
    except Exception as e:
        print(mb, "ERR", e)