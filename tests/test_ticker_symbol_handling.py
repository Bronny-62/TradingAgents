import unittest

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from cli.utils import normalize_ticker_symbol
from tradingagents.agents.utils.agent_utils import build_instrument_context, create_msg_delete


@pytest.mark.unit
class TickerSymbolHandlingTests(unittest.TestCase):
    def test_normalize_ticker_symbol_accepts_tushare_ts_code(self):
        self.assertEqual(normalize_ticker_symbol(" 000001.sz "), "000001.SZ")
        self.assertEqual(normalize_ticker_symbol("600000.SH"), "600000.SH")

    def test_build_instrument_context_mentions_exact_symbol(self):
        context = build_instrument_context("300750.SZ")
        self.assertIn("300750.SZ", context)
        self.assertIn("Tushare ts_code", context)

    def test_normalize_ticker_symbol_rejects_non_a_share_symbol(self):
        with self.assertRaises(ValueError):
            normalize_ticker_symbol("NVDA")

    def test_message_clear_uses_atomic_remove_all(self):
        delete_messages = create_msg_delete()
        result = delete_messages(
            {
                "messages": [
                    HumanMessage(content="x"),
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "foo", "args": {}, "id": "call_1"}],
                    ),
                    ToolMessage(content="ok", tool_call_id="call_1"),
                ]
            }
        )

        self.assertEqual(result["messages"][0].id, REMOVE_ALL_MESSAGES)
        self.assertEqual(result["messages"][1].content, "Continue")


if __name__ == "__main__":
    unittest.main()
