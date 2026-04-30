from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_a_share_hotness,
    get_a_share_social_sentiment,
    get_language_instruction,
    get_social_monitoring_coverage,
)


def create_social_media_analyst(llm):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_a_share_social_sentiment,
            get_a_share_hotness,
            get_social_monitoring_coverage,
        ]

        system_message = (
            "You are the Social Sentiment Analyst for China A-share spot trading. This system intentionally does not bypass anti-bot protections on Eastmoney Guba. Use get_social_monitoring_coverage first, then use get_a_share_social_sentiment and get_a_share_hotness. If authorized browser-session Eastmoney Guba forum posts exist, treat them as the primary social evidence. Otherwise evaluate authorized imports, Tushare hotness signals, and news-derived proxy sentiment. Clearly separate raw forum signals, hotness proxy signals, news proxy signals, coverage gaps, and confidence."
            + " Write only the analyst report. Do not include process narration, tool-use narration, or FINAL TRANSACTION PROPOSAL lines."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, state the coverage gap and confidence clearly."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "sentiment_report": report,
        }

    return social_media_analyst_node
