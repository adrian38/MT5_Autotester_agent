"""Fechas de las etapas tick a tick que se guardan en ui_settings.ini.

Vive aparte de ``settings_logic`` porque esa unidad esta en su techo de lineas.
"""
from __future__ import annotations


def final_tick_date_settings(app) -> dict[str, str]:
    """Las cuatro ventanas tick a tick: candidatos (corto, 6M) y seeds."""
    return {
        "ubs_final_tick_from_date": app.ubs_final_tick_from_date.get().strip(),
        "ubs_final_tick_to_date": app.ubs_final_tick_to_date.get().strip(),
        "ubs_final_tick_ohlc_from_date": app.ubs_final_tick_ohlc_from_date.get().strip(),
        "ubs_final_tick_ohlc_to_date": app.ubs_final_tick_ohlc_to_date.get().strip(),
        "ubs_final_tick_6m_from_date": app.ubs_final_tick_6m_from_date.get().strip(),
        "ubs_final_tick_6m_to_date": app.ubs_final_tick_6m_to_date.get().strip(),
        "ubs_final_tick_6m_ohlc_from_date": app.ubs_final_tick_6m_ohlc_from_date.get().strip(),
        "ubs_final_tick_6m_ohlc_to_date": app.ubs_final_tick_6m_ohlc_to_date.get().strip(),
        "ubs_seed_final_tick_from_date": app.ubs_seed_final_tick_from_date.get().strip(),
        "ubs_seed_final_tick_to_date": app.ubs_seed_final_tick_to_date.get().strip(),
        "ubs_seed_final_tick_6m_from_date": app.ubs_seed_final_tick_6m_from_date.get().strip(),
        "ubs_seed_final_tick_6m_to_date": app.ubs_seed_final_tick_6m_to_date.get().strip(),
    }
