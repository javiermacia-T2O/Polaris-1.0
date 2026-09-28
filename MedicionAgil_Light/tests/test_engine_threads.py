import threading

from core import engine


def test_duckdb_connections_are_isolated_and_file_views_follow_threads(tmp_path):
    source = tmp_path / "datos.csv"
    source.write_text("key,value\n1,42\n", encoding="utf-8")
    engine.reset_conn()
    main_conn = engine.get_conn()
    seen = []

    def worker():
        try:
            engine.register_file_in_duckdb(source, name="_test_thread_view")
            seen.append((engine.get_conn(),
                         engine.query("SELECT value FROM _test_thread_view").iloc[0, 0]))
        finally:
            engine.reset_conn()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    try:
        assert seen[0][0] is not main_conn
        assert seen[0][1] == 42
        assert engine.query("SELECT value FROM _test_thread_view").iloc[0, 0] == 42
    finally:
        engine.reset_conn()
        engine._FILE_VIEWS.pop("_test_thread_view", None)
        engine._FILE_VIEWS_VERSION += 1
