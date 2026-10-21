"""Calls the model through the Claude Agent SDK, in a session kept apart from
the user's Claude Code setup."""

from config import get_config


async def call_model(prompt, schema, allowed_tools=()):
    """Run *prompt* and return the model's output for the JSON *schema*.

    The session loads none of the user's settings, CLAUDE.md, plugins, hooks
    or MCP servers, is offered only *allowed_tools*, and saves no transcript.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    options = ClaudeAgentOptions(
        model=get_config()["model"],
        tools=list(allowed_tools),
        allowed_tools=list(allowed_tools),
        setting_sources=[],
        strict_mcp_config=True,
        output_format={"type": "json_schema", "schema": schema},
        extra_args={"no-session-persistence": None},
        # Larvling's hooks skip a session with this set.
        env={"LARVLING_INTERNAL": "1"},
    )
    result = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, ResultMessage):
            result = msg
    if result is None or result.structured_output is None:
        raise RuntimeError(
            f"Structured output not returned (subtype={getattr(result, 'subtype', None)})"
        )
    return result.structured_output
