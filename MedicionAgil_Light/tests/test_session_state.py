import pandas as pd

from app_desktop import MMMApp
from models.session_state import SessionState


def test_session_state_keeps_dataframe_identity_and_isolates_mutables():
    frame = pd.DataFrame({"value": [1, 2]})
    first = SessionState(df_raw=frame, df_view=frame)
    second = SessionState()

    assert first.df_raw is frame
    assert first.df_view is frame
    first.loaded_datasets["source"] = frame
    first.value_filters["value"] = {1}
    first.column_types["value"] = "numero"
    assert second.loaded_datasets == {}
    assert second.value_filters == {}
    assert second.column_types == {}


def test_mmmapp_session_properties_delegate_without_creating_a_window():
    app = object.__new__(MMMApp)
    app.session = SessionState()
    frame = pd.DataFrame({"value": [1]})

    app.df_raw = frame
    app.column_types["value"] = "numero"
    app._view_is_built = True

    assert app.session.df_raw is frame
    assert app.df_raw is app.session.df_raw
    assert app.session.column_types == {"value": "numero"}
    assert app.session.view_is_built is True
