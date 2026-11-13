import unittest

import pandas as pd

from collectors.binance import OHLCV_COLUMNS
from ui.charts import DOWN_COLOR, UP_COLOR, create_candlestick_chart
from indicators.technical import add_technical_indicators
from strategies.evaluator import evaluate_strategy
from strategies.models import (
    Condition, ConditionGroup, Direction, Indicator, Logic, Operand,
    OperandKind, Operator, Strategy,
)


class ChartTests(unittest.TestCase):
    def indicator_data(self):
        close = pd.Series(range(100, 200), dtype=float)
        raw = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC"),
            "open": close - 1, "high": close + 2, "low": close - 2,
            "close": close, "volume": 10.0,
        })
        return add_technical_indicators(raw, timeframe="1h")

    def test_candlestick_volume_hover_and_axes(self):
        data = pd.DataFrame(
            [
                [pd.Timestamp("2026-10-14T10:00:00Z"), 100, 105, 98, 103, 10],
                [pd.Timestamp("2026-10-14T11:00:00Z"), 103, 104, 95, 97, 12],
            ],
            columns=OHLCV_COLUMNS,
        )
        figure = create_candlestick_chart(data, "BTC/USDT", "1h")
        self.assertEqual([trace.type for trace in figure.data], ["candlestick", "bar"])
        self.assertEqual(figure.data[0].increasing.line.color, UP_COLOR)
        self.assertEqual(figure.data[0].decreasing.line.color, DOWN_COLOR)
        self.assertEqual(list(figure.data[1].marker.color), [UP_COLOR, DOWN_COLOR])
        self.assertIn("시가: 100.00 USDT", figure.data[0].hovertext[0])
        self.assertIn("UTC", figure.data[0].hovertext[0])
        self.assertEqual(figure.layout.xaxis2.title.text, "날짜 (UTC)")
        self.assertEqual(figure.layout.yaxis.title.text, "가격 (USDT)")
        self.assertFalse(figure.layout.xaxis.rangeslider.visible)
        self.assertFalse(figure.layout.yaxis.fixedrange)
        self.assertEqual(figure.layout.dragmode, "zoom")
        self.assertTrue(figure.to_json())

    def test_empty_data_is_rejected(self):
        with self.assertRaises(ValueError):
            create_candlestick_chart(pd.DataFrame(columns=OHLCV_COLUMNS), "BTC/USDT", "1h")

    def test_missing_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            create_candlestick_chart(pd.DataFrame({"close": [100]}), "BTC/USDT", "1h")

    def test_all_selected_indicators_have_correct_panels_and_reference_lines(self):
        figure = create_candlestick_chart(
            self.indicator_data(), "BTC/USDT", "1h", show_rsi=True, show_macd=True,
            show_ema=True, show_sma=True, show_bollinger=True, show_volume_sma=True,
        )
        self.assertEqual(len(figure.data), 14)
        traces = {trace.name: trace for trace in figure.data}
        self.assertEqual(traces["EMA 20"].yaxis, "y")
        self.assertEqual(traces["SMA 60"].yaxis, "y")
        self.assertEqual(traces["BB Lower"].fill, "tonexty")
        self.assertEqual(traces["Volume SMA 20"].yaxis, "y2")
        self.assertEqual(traces["RSI 14"].yaxis, "y3")
        self.assertEqual(traces["MACD Histogram"].yaxis, "y4")
        self.assertEqual(tuple(figure.layout.yaxis3.range), (0, 100))
        self.assertEqual({shape.y0 for shape in figure.layout.shapes}, {0, 30, 70})
        self.assertEqual(figure.layout.xaxis4.title.text, "날짜 (UTC)")
        self.assertTrue(figure.to_json())

    def test_unselected_indicators_create_no_traces_or_panels(self):
        figure = create_candlestick_chart(self.indicator_data(), "BTC/USDT", "1h")
        self.assertEqual([trace.type for trace in figure.data], ["candlestick", "bar"])
        self.assertNotIn("yaxis3", figure.layout)

    def test_macd_only_uses_third_panel(self):
        figure = create_candlestick_chart(self.indicator_data(), "BTC/USDT", "1h", show_macd=True)
        self.assertEqual(len(figure.data), 5)
        self.assertEqual(figure.data[-1].yaxis, "y3")
        self.assertEqual(figure.layout.xaxis3.title.text, "날짜 (UTC)")

    def test_missing_selected_indicator_columns_raise_clear_error(self):
        data = self.indicator_data().drop(columns="rsi_14")
        with self.assertRaisesRegex(ValueError, "먼저 계산"):
            create_candlestick_chart(data, "BTC/USDT", "1h", show_rsi=True)

    def test_signal_markers_keep_conflicts_separate_and_price_volume_present(self):
        data = self.indicator_data()
        data.attrs["timeframe"] = "1h"
        long = Condition("long", Indicator.CLOSE, Operator.GT, Operand(OperandKind.NUMBER, 150))
        short = Condition("short", Indicator.CLOSE, Operator.GE, Operand(OperandKind.NUMBER, 150))
        strategy = Strategy("marker test", ConditionGroup(Direction.LONG, Logic.AND, (long,)),
                            ConditionGroup(Direction.SHORT, Logic.AND, (short,)))
        result = evaluate_strategy(data, strategy)
        figure = create_candlestick_chart(data, "BTC/USDT", "1h", signals=result)
        self.assertEqual([trace.type for trace in figure.data[:2]], ["candlestick", "bar"])
        markers = {trace.name: trace for trace in figure.data[2:]}
        self.assertNotIn("LONG 조건 충족", markers)
        self.assertEqual(len(markers["SHORT 조건 충족"].x), 1)
        self.assertEqual(len(markers["LONG/SHORT 충돌"].x), 49)
        self.assertEqual(markers["LONG/SHORT 충돌"].marker.symbol, "x")
        self.assertIn("체결 가격 아님", markers["LONG/SHORT 충돌"].hovertemplate)
        self.assertTrue(figure.to_json())
