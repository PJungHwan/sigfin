from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from services.market_data import load_market_data
from strategies.models import Strategy, default_strategy
from strategies.storage import strategy_from_dict, strategy_to_dict, strategy_to_json
from ui.strategy_builder import _load_uploaded
from test_app import APP_PATH, chart_names, full_fixture_frame


class StrategyBuilderTests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()

    def tearDown(self):
        load_market_data.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=25).run()
        self.assertFalse(app.exception)
        return app

    def first_prefix(self, app, direction="LONG"):
        id_ = app.session_state["_strategy_draft"][direction.lower()]["conditions"][0]["id"]
        return f"strategy_{app.session_state['_strategy_revision']}_{direction}_{id_}"

    def test_add_delete_and_logic_keep_other_direction_and_market_cache(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            short_before = deepcopy(app.session_state["_strategy_draft"]["short"])
            first = self.first_prefix(app)
            app.button(key="strategy_add_LONG").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.session_state["_strategy_draft"]["long"]["conditions"]), 2)
            app.selectbox(key="strategy_0_LONG_logic").select("OR").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["_strategy_draft"]["long"]["combine"], "OR")
            app.button(key=f"{first}_delete").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.session_state["_strategy_draft"]["long"]["conditions"]), 1)
            self.assertEqual(app.session_state["_strategy_draft"]["short"], short_before)
            self.assertEqual(fetch.call_count, 1)

    def test_indicator_rhs_cross_selection_and_compatibility(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            prefix = self.first_prefix(app)
            app.selectbox(key=f"{prefix}_left").select("close").run()
            app.selectbox(key=f"{prefix}_kind").select("indicator").run()
            self.assertFalse(app.exception)
            options = app.selectbox(key=f"{prefix}_right").options
            self.assertNotIn("RSI (14)", options)
            self.assertNotIn("거래량 (Volume)", options)
            app.selectbox(key=f"{prefix}_right").select("ema_60").run()
            app.selectbox(key=f"{prefix}_operator").select("Cross Up").run()
            self.assertFalse(app.exception)
            model = strategy_from_dict(app.session_state["_strategy_draft"])
            self.assertEqual(model.long.conditions[0].right.indicator.value, "ema_60")
            self.assertEqual(model.long.conditions[0].operator.value, "Cross Up")
            app.selectbox(key=f"{prefix}_left").select("rsi_14").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["_strategy_draft"]["long"]["conditions"][0]["right"]["kind"], "number")
            self.assertEqual(fetch.call_count, 1)

    def test_signal_counts_marker_and_condition_details(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            app.toggle(key="show_strategy_signals").set_value(True).run()
            self.assertFalse(app.exception)
            self.assertIn("SHORT 조건 충족", chart_names(app))
            metrics = {item.label: item.value for item in app.metric}
            self.assertEqual(metrics["LONG 조건 충족"], "0")
            self.assertEqual(metrics["SHORT 조건 충족"], "106")
            self.assertEqual(metrics["동시 충족 (충돌)"], "0")
            self.assertTrue(any("봉 마감" in item.value for item in app.caption))
            self.assertGreaterEqual(len(app.dataframe), 2)
            self.assertEqual(fetch.call_count, 1)

    def test_conflict_marker_does_not_select_a_direction(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            long_prefix = self.first_prefix(app, "LONG")
            short_prefix = self.first_prefix(app, "SHORT")
            for prefix in (long_prefix, short_prefix):
                app.selectbox(key=f"{prefix}_left").select("close").run()
                app.selectbox(key=f"{prefix}_operator").select(">").run()
                app.number_input(key=f"{prefix}_number").set_value(0.0).run()
            app.toggle(key="show_strategy_signals").set_value(True).run()
            self.assertFalse(app.exception)
            names = chart_names(app)
            self.assertIn("LONG/SHORT 충돌", names)
            self.assertNotIn("LONG 조건 충족", names)
            self.assertNotIn("SHORT 조건 충족", names)
            self.assertTrue(any("동시에 충족" in item.value for item in app.warning))
            self.assertEqual(fetch.call_count, 1)

    def test_no_signal_and_invalid_name_leave_market_chart_working(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            prefix = self.first_prefix(app, "SHORT")
            app.button(key=f"{prefix}_delete").click().run()
            app.toggle(key="show_strategy_signals").set_value(True).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("충족한 확정 봉이 없습니다" in item.value for item in app.info))
            app.text_input(key="strategy_name").set_value("").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("조건 검증 실패" in item.value for item in app.error))
            self.assertTrue(any("검증 오류" in item.value for item in app.warning))
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.assertEqual(fetch.call_count, 1)

    def test_loading_json_callback_restores_widget_values(self):
        original = default_strategy()
        saved = Strategy("불러온 전략", original.long, original.short)
        encoded = strategy_to_json(saved)
        script = (
            "import streamlit as st\n"
            "from ui.strategy_builder import render_strategy_builder, load_builder_json\n"
            f"payload = {encoded!r}\n"
            "st.button('Load fixture', key='test_load', on_click=load_builder_json, args=(payload,))\n"
            "render_strategy_builder()\n"
        )
        app = AppTest.from_string(script, default_timeout=20).run()
        app.text_input(key="strategy_name").set_value("수정된 이름").run()
        app.button(key="test_load").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="strategy_name").value, "불러온 전략")
        self.assertEqual(strategy_from_dict(app.session_state["_strategy_draft"]), saved)
        self.assertTrue(any("불러왔습니다" in item.value for item in app.success))

    def test_uploaded_json_callback_validates_before_replacing_draft(self):
        original = default_strategy()
        saved = Strategy("업로드 전략", original.long, original.short)
        for payload, success in ((strategy_to_json(saved).encode("utf-8"), True), (b"{broken}", False)):
            with self.subTest(success=success):
                state = {
                    "strategy_upload": SimpleNamespace(getvalue=lambda: payload),
                    "_strategy_draft": strategy_to_dict(original), "_strategy_revision": 0,
                    "strategy_name": original.name,
                }
                with patch("ui.strategy_builder.st.session_state", state):
                    _load_uploaded()
                if success:
                    self.assertEqual(strategy_from_dict(state["_strategy_draft"]), saved)
                    self.assertEqual(state["_strategy_notice"][0], "success")
                else:
                    self.assertEqual(strategy_from_dict(state["_strategy_draft"]), original)
                    self.assertEqual(state["_strategy_notice"][0], "error")
