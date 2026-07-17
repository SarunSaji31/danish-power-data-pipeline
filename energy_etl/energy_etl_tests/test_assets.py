from datetime import datetime, timezone

import pytest

from energy_etl.assets import (
    FIFTEEN_MIN_SWITCH,
    build_briefing_text,
    build_forecast_text,
    build_upsert_sql,
    cheapest_window,
    consumer_price,
    parse_gas_chart,
    parse_utc,
)
from energy_etl import ml


class TestParseUtc:
    def test_naive_api_string_becomes_utc(self):
        result = parse_utc("2026-07-12T21:45:00")
        assert result == datetime(2026, 7, 12, 21, 45, tzinfo=timezone.utc)

    def test_market_switch_boundary_is_utc(self):
        assert parse_utc("2025-10-01T00:00:00") >= FIFTEEN_MIN_SWITCH


class TestConsumerPrice:
    def test_vat_formula(self):
        # 1000 DKK/MWh -> 1 DKK/kWh -> 1.25 incl. 25% VAT
        assert consumer_price(1000.0) == 1.25

    def test_negative_prices_stay_negative(self):
        assert consumer_price(-400.0) == -0.5

    def test_none_passes_through(self):
        assert consumer_price(None) is None


class TestBuildUpsertSql:
    def test_updates_only_non_conflict_columns(self):
        sql = build_upsert_sql("t", ["ts", "area", "price"], ["ts", "area"])
        assert "INSERT INTO t (ts, area, price)" in sql
        assert "ON CONFLICT (ts, area)" in sql
        assert "price = EXCLUDED.price" in sql
        assert "ts = EXCLUDED.ts" not in sql


class TestBuildForecastText:
    def test_labeled_as_prediction_with_window_marked(self):
        prices = {h: 1.0 for h in range(24)}
        prices[3] = prices[4] = prices[5] = 0.2  # cheap night window
        text = build_forecast_text("Sat 18 Jul", prices, [3, 4, 5], 0.2)
        assert "model prediction" in text
        assert "03:00–06:00" in text
        assert text.count("◀") >= 3  # the 3 window rows are marked
        assert "official prices arrive" in text  # never passes off as official

    def test_negative_predicted_price_gets_empty_bar(self):
        prices = {h: 1.0 for h in range(24)}
        prices[14] = -0.4
        line = [l for l in build_forecast_text("Sun", prices, None, None).splitlines()
                if l.startswith("14")][0]
        assert "▍" not in line


class TestParseGasChart:
    def test_timestamps_become_utc_dates_and_nulls_are_skipped(self):
        result = {
            "timestamp": [1661472000, 1661558400],  # 2022-08-26, 2022-08-27 UTC
            "indicators": {"quote": [{"close": [339.20, None]}]},
        }
        assert parse_gas_chart(result) == [(datetime(2022, 8, 26).date(), 339.20)]

    def test_empty_window_has_no_timestamp_key(self):
        result = {"indicators": {"quote": [{"close": []}]}}
        assert parse_gas_chart(result) == []


class TestLatestModelPath:
    def test_monthly_legacy_name_sorts_as_day_one(self, tmp_path, monkeypatch):
        # lexically 'model_2026-07.txt' > 'model_2026-07-17.txt' ('.' > '-');
        # version-date ordering must pick the daily-named retrain anyway
        for name in ["model_2026-07.txt", "model_2026-07-17.txt"]:
            (tmp_path / name).touch()
        monkeypatch.setattr(ml, "MODELS_DIR", tmp_path)
        assert ml.latest_model_path().name == "model_2026-07-17.txt"


class TestCheapestWindow:
    def test_finds_cheapest_consecutive_run(self):
        prices = {0: 1.0, 1: 1.0, 2: 0.1, 3: 0.1, 4: 0.1, 5: 2.0}
        window, cost = cheapest_window(prices)
        assert window == [2, 3, 4]
        assert cost == pytest.approx(0.1)

    def test_skips_non_consecutive_hours(self):
        # hour 4 is missing, so [3, 5] can never be part of a window;
        # [1, 2, 3] (avg 0.7) beats [0, 1, 2] (avg 1.0)
        prices = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.1, 5: 0.1}
        window, _ = cheapest_window(prices)
        assert window == [1, 2, 3]

    def test_too_few_hours_returns_none(self):
        assert cheapest_window({0: 1.0, 1: 1.0}) == (None, None)


class TestBuildBriefingText:
    def test_summary_table_and_window_markers(self):
        hourly_avg = {0: 1.0, 1: 0.5, 2: 0.4, 3: 0.6, 4: 2.0}
        wind = {h: 500.0 for h in hourly_avg}
        solar = {h: 100.0 for h in hourly_avg}
        text = build_briefing_text("Wed 15 Jul", hourly_avg, wind, solar, [1, 2, 3], 0.5)

        assert "<b>DK1 Energy Brief</b> — Wed 15 Jul" in text
        assert "Cheapest 3 h: <b>01:00–04:00</b> at 0.50 kr/kWh" in text
        pre = text.split("<pre>")[1].split("</pre>")[0]
        assert len(pre.splitlines()) == 1 + len(hourly_avg)  # header + one row per hour
        assert pre.count("◀") == 3  # cheapest-window hours marked

    def test_negative_price_gets_empty_bar(self):
        hourly_avg = {0: -0.2, 1: 1.0, 2: 1.0, 3: 1.0}
        text = build_briefing_text("Thu 16 Jul", hourly_avg, {}, {}, [0, 1, 2], 0.6)
        row_hour_0 = next(l for l in text.splitlines() if l.startswith("00"))
        assert "-0.20" in row_hour_0
        assert "▍" not in row_hour_0
