import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest

from tradingagents.llm_clients.openai_client import OpenAIClient
from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI
from tradingagents.llm_clients.openai_client import _repair_tool_call_message_sequence


@pytest.mark.unit
class TestOpenAICompatibleProviderConfig(unittest.TestCase):
    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_explicit_base_url_overrides_provider_default(self, mock_chat):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "placeholder"}):
            client = OpenAIClient(
                "qwen-plus",
                base_url="https://custom.example/v1",
                provider="qwen",
            )

            client.get_llm()

        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(call_kwargs["base_url"], "https://custom.example/v1")
        self.assertEqual(call_kwargs["api_key"], "placeholder")

    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_qwen_default_base_url_matches_cli_provider_url(self, mock_chat):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "placeholder"}):
            client = OpenAIClient("qwen-plus", provider="qwen")

            client.get_llm()

        call_kwargs = mock_chat.call_args[1]
        self.assertEqual(
            call_kwargs["base_url"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_missing_provider_api_key_fails_before_sdk_call(self):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            client = OpenAIClient("qwen-plus", provider="qwen")

            with self.assertRaisesRegex(ValueError, "Missing DASHSCOPE_API_KEY"):
                client.get_llm()

    def test_reasoning_content_is_preserved_from_openai_compatible_responses(self):
        llm = NormalizedChatOpenAI(model="deepseek-reasoner", api_key="test")
        result = llm._create_chat_result(
            {
                "model": "deepseek-reasoner",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Need a tool.",
                            "reasoning_content": "Private reasoning that DeepSeek requires.",
                            "tool_calls": [],
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        )

        message = result.generations[0].message
        self.assertEqual(
            message.additional_kwargs["reasoning_content"],
            "Private reasoning that DeepSeek requires.",
        )

    def test_reasoning_content_is_passed_back_with_assistant_history(self):
        llm = NormalizedChatOpenAI(model="deepseek-reasoner", api_key="test")
        message = AIMessage(
            content="Need a tool.",
            additional_kwargs={
                "reasoning_content": "Private reasoning that DeepSeek requires."
            },
        )

        payload = llm._get_request_payload([message])

        self.assertEqual(
            payload["messages"][0]["reasoning_content"],
            "Private reasoning that DeepSeek requires.",
        )

    def test_incomplete_tool_call_history_is_repaired_before_request(self):
        llm = NormalizedChatOpenAI(model="deepseek-chat", api_key="test")
        payload = llm._get_request_payload(
            [
                HumanMessage(content="Analyze 000001.SZ"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "search_a_share_news", "args": {}, "id": "call_1"},
                        {"name": "get_cn_macro_news", "args": {}, "id": "call_2"},
                    ],
                ),
                ToolMessage(content="company news rows", tool_call_id="call_1"),
                HumanMessage(content="Continue"),
            ]
        )

        messages = payload["messages"]
        assistant_index = next(
            i for i, message in enumerate(messages) if message["role"] == "assistant"
        )
        following = messages[assistant_index + 1 : assistant_index + 3]
        self.assertEqual([message["role"] for message in following], ["tool", "tool"])
        self.assertEqual(
            [message["tool_call_id"] for message in following],
            ["call_1", "call_2"],
        )
        self.assertIn("unavailable", following[1]["content"])

    def test_dangling_tool_message_is_removed_before_request(self):
        messages, repair_count = _repair_tool_call_message_sequence(
            [
                {"role": "user", "content": "Continue"},
                {"role": "tool", "tool_call_id": "orphan", "content": "orphan"},
                {"role": "assistant", "content": "Done"},
            ]
        )

        self.assertEqual(repair_count, 1)
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])

    def test_deepseek_models_skip_structured_output_tool_choice(self):
        for model in ("deepseek-reasoner", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat"):
            with self.subTest(model=model), patch.dict(
                "os.environ",
                {"TRADINGAGENTS_ENABLE_DEEPSEEK_STRUCTURED_OUTPUT": ""},
            ):
                llm = NormalizedChatOpenAI(model=model, api_key="test")

                with self.assertRaisesRegex(NotImplementedError, "DeepSeek"):
                    llm.with_structured_output(dict)

    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_deepseek_provider_marks_structured_output_disabled(self, mock_chat):
        with patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "placeholder", "TRADINGAGENTS_ENABLE_DEEPSEEK_STRUCTURED_OUTPUT": ""},
        ):
            client = OpenAIClient("deepseek-v4-pro", provider="deepseek")
            llm = client.get_llm()

        self.assertEqual(llm._tradingagents_provider, "deepseek")
        self.assertIn("DeepSeek", llm._tradingagents_structured_output_disabled_reason)

    @patch("tradingagents.llm_clients.openai_client.NormalizedChatOpenAI")
    def test_deepseek_structured_output_escape_hatch(self, mock_chat):
        with patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "placeholder", "TRADINGAGENTS_ENABLE_DEEPSEEK_STRUCTURED_OUTPUT": "1"},
        ):
            client = OpenAIClient("deepseek-v4-pro", provider="deepseek")
            llm = client.get_llm()

        self.assertEqual(llm._tradingagents_provider, "deepseek")
        self.assertNotIn("_tradingagents_structured_output_disabled_reason", llm.__dict__)


if __name__ == "__main__":
    unittest.main()
