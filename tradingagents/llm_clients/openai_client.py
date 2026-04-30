import logging
import os
from typing import Any, Optional

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

logger = logging.getLogger(__name__)


class NormalizedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with normalized content output.

    The Responses API returns content as a list of typed blocks
    (reasoning, text, etc.). This normalizes to string for consistent
    downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = payload.get("messages", [])
        input_messages = input_ if isinstance(input_, list) else []

        for message_dict, message in zip(messages, input_messages):
            if not isinstance(message, AIMessage):
                continue
            reasoning_content = message.additional_kwargs.get("reasoning_content")
            if reasoning_content is not None:
                message_dict["reasoning_content"] = reasoning_content

        repaired_messages, repair_count = _repair_tool_call_message_sequence(messages)
        if repair_count:
            payload["messages"] = repaired_messages
            logger.debug(
                "Repaired %d incomplete or dangling tool message(s) before sending "
                "an OpenAI-compatible chat request.",
                repair_count,
            )

        return payload

    def _create_chat_result(self, response, generation_info=None):
        chat_result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(
                exclude={"choices": {"__all__": {"message": {"parsed"}}}}
            )
        )

        for generation, choice in zip(
            chat_result.generations, response_dict.get("choices", [])
        ):
            message = choice.get("message", {})
            reasoning_content = message.get("reasoning_content")
            if reasoning_content is not None:
                generation.message.additional_kwargs["reasoning_content"] = (
                    reasoning_content
                )

        return chat_result

    def with_structured_output(self, schema, *, method=None, **kwargs):
        """Wrap with structured output, defaulting to function_calling for OpenAI.

        langchain-openai's Responses-API-parse path (the default for json_schema
        when use_responses_api=True) calls response.model_dump(...) on the OpenAI
        SDK's union-typed parsed response, which makes Pydantic emit ~20
        PydanticSerializationUnexpectedValue warnings per call. The function-calling
        path returns a plain tool-call shape that does not trigger that
        serialization, so it is the cleaner choice for our combination of
        use_responses_api=True + with_structured_output. Both paths use OpenAI's
        strict mode and produce the same typed Pydantic instance.
        """
        reason = _structured_output_disabled_reason(self)
        if reason:
            raise NotImplementedError(reason)

        if method is None:
            method = "function_calling"
        return super().with_structured_output(schema, method=method, **kwargs)


def _raw_attr(obj: Any, name: str) -> Any:
    try:
        return object.__getattribute__(obj, name)
    except AttributeError:
        return None


def _structured_output_disabled_reason(llm: Any) -> str | None:
    explicit_reason = _raw_attr(llm, "_tradingagents_structured_output_disabled_reason")
    if explicit_reason:
        return str(explicit_reason)

    if _deepseek_structured_output_enabled():
        return None

    provider = _raw_attr(llm, "_tradingagents_provider") or _raw_attr(llm, "provider")
    if isinstance(provider, str) and provider.lower() == "deepseek":
        return "DeepSeek does not reliably support structured-output tool_choice"

    model_name = _raw_attr(llm, "model_name") or _raw_attr(llm, "model")
    if isinstance(model_name, str) and model_name.lower().startswith("deepseek"):
        return "DeepSeek does not reliably support structured-output tool_choice"
    return None


def _deepseek_structured_output_enabled() -> bool:
    return os.getenv("TRADINGAGENTS_ENABLE_DEEPSEEK_STRUCTURED_OUTPUT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _repair_tool_call_message_sequence(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Make chat-completions tool history protocol-valid.

    OpenAI-compatible providers reject a request when an assistant message with
    ``tool_calls`` is not immediately followed by one tool result per call id.
    LangGraph retries, interruptions, or stale checkpoints can leave that
    history incomplete. Insert an explicit unavailable-result ToolMessage so the
    model can continue instead of aborting the whole report.
    """
    repaired: list[dict[str, Any]] = []
    repair_count = 0
    i = 0

    while i < len(messages):
        message = messages[i]

        if message.get("role") == "assistant" and message.get("tool_calls"):
            required_ids: list[str] = []
            valid_tool_calls: list[dict[str, Any]] = []
            for call in message.get("tool_calls") or []:
                call_id = call.get("id")
                if not call_id:
                    repair_count += 1
                    continue
                valid_tool_calls.append(call)
                required_ids.append(call_id)

            if not valid_tool_calls:
                sanitized_message = dict(message)
                sanitized_message.pop("tool_calls", None)
                if not sanitized_message.get("content"):
                    sanitized_message["content"] = (
                        "A previous tool-call request was malformed and could not "
                        "be executed. Continue with the available evidence."
                    )
                repaired.append(sanitized_message)
                i += 1
                continue

            if len(valid_tool_calls) != len(message.get("tool_calls") or []):
                message = {**message, "tool_calls": valid_tool_calls}

            repaired.append(message)
            i += 1

            seen: set[str] = set()
            while i < len(messages) and messages[i].get("role") == "tool":
                tool_message = messages[i]
                tool_call_id = tool_message.get("tool_call_id")
                if tool_call_id in required_ids and tool_call_id not in seen:
                    repaired.append(tool_message)
                    seen.add(tool_call_id)
                else:
                    repair_count += 1
                i += 1

            for call_id in required_ids:
                if call_id in seen:
                    continue
                synthetic = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": (
                        "Tool call did not return a result before the next model "
                        "request. Treat this tool result as unavailable and "
                        "continue with the available evidence."
                    ),
                }
                repaired.append(synthetic)
                repair_count += 1
            continue

        if message.get("role") == "tool":
            repair_count += 1
            i += 1
            continue

        repaired.append(message)
        i += 1

    return repaired, repair_count

# Kwargs forwarded from user config to ChatOpenAI
_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "reasoning_effort",
    "api_key", "callbacks", "http_client", "http_async_client",
)

# Provider base URLs and API key env vars
_PROVIDER_CONFIG = {
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4/", "ZHIPU_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}


class OpenAIClient(BaseLLMClient):
    """Client for OpenAI, Ollama, OpenRouter, and xAI providers.

    For native OpenAI models, uses the Responses API (/v1/responses) which
    supports reasoning_effort with function tools across all model families
    (GPT-4.1, GPT-5). Third-party compatible providers (xAI, OpenRouter,
    Ollama) use standard Chat Completions.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)
        self.provider = provider.lower()

    def get_llm(self) -> Any:
        """Return configured ChatOpenAI instance."""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        # Provider-specific base URL and auth
        if self.provider in _PROVIDER_CONFIG:
            base_url, api_key_env = _PROVIDER_CONFIG[self.provider]
            llm_kwargs["base_url"] = self.base_url or base_url
            if api_key_env:
                api_key = os.environ.get(api_key_env)
                if not api_key:
                    raise ValueError(
                        f"Missing {api_key_env} for provider '{self.provider}'. "
                        f"Set it in .env or choose a provider whose API key is configured."
                    )
                llm_kwargs["api_key"] = api_key
            else:
                llm_kwargs["api_key"] = "ollama"
        elif self.base_url:
            llm_kwargs["base_url"] = self.base_url

        # Forward user-provided kwargs
        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Native OpenAI: use Responses API for consistent behavior across
        # all model families. Third-party providers use Chat Completions.
        if self.provider == "openai":
            llm_kwargs["use_responses_api"] = True

        llm = NormalizedChatOpenAI(**llm_kwargs)
        object.__setattr__(llm, "_tradingagents_provider", self.provider)
        if self.provider == "deepseek" and not _deepseek_structured_output_enabled():
            object.__setattr__(
                llm,
                "_tradingagents_structured_output_disabled_reason",
                "DeepSeek does not reliably support structured-output tool_choice",
            )
        return llm

    def validate_model(self) -> bool:
        """Validate model for the provider."""
        return validate_model(self.provider, self.model)
