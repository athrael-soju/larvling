"""Claude Agent SDK integration for Larvling.

Provides call_model() — the shared interface for calling Claude via
the Agent SDK with structured output support, run apart from the user's
Claude Code setup, and LARVLING_INTERNAL guarding.
"""

import os

from config import get_config

# The oldest claude-agent-sdk Larvling runs with; requirements.txt says the same.
MIN_SDK_VERSION = "0.2.170"


def _version(text):
    """*text* such as "0.2.170" as a tuple of ints, or () if it isn't one."""
    try:
        return tuple(int(part) for part in text.split(".")[:3])
    except (AttributeError, ValueError):
        return ()


def sdk_outdated(version):
    """True if claude-agent-sdk *version* is older than MIN_SDK_VERSION."""
    return _version(version) < _version(MIN_SDK_VERSION)


async def call_model(prompt, allowed_tools=None, max_turns=None, output_format=None):
    """Call the LLM via Agent SDK and return the response.

    Returns (result, usage_info) tuple where:
    - result is structured_output (dict) when output_format is set,
      otherwise response text (str).
    - usage_info is the usage dict from ResultMessage, or None if
      not available.

    The call runs apart from the user's Claude Code setup: it loads no
    settings, CLAUDE.md, plugins, hooks or MCP servers, sees only the tools
    in *allowed_tools*, and leaves no session transcript behind.

    Sets LARVLING_INTERNAL to prevent sub-agent from triggering hooks.
    """
    try:
        import claude_agent_sdk
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
    except ImportError:
        raise RuntimeError(
            "claude_agent_sdk is required but not installed. "
            "Install it with: pip install claude-agent-sdk"
        )
    if sdk_outdated(claude_agent_sdk.__version__):
        raise RuntimeError(
            f"claude_agent_sdk {claude_agent_sdk.__version__} is older than "
            f"{MIN_SDK_VERSION}. Upgrade it with: "
            f"pip install --upgrade 'claude-agent-sdk>={MIN_SDK_VERSION}'"
        )

    tools = list(allowed_tools or [])
    opts = {
        "model": get_config()["model"],
        # Offering only the allowed tools keeps the others' definitions,
        # most of an unrestricted session's context, out of every call.
        "tools": tools,
        "allowed_tools": tools,
        "setting_sources": [],
        "strict_mcp_config": True,
        # Otherwise Claude Code saves each call as a session transcript, a
        # copy of the conversation being extracted.
        "extra_args": {"no-session-persistence": None},
    }
    if max_turns is not None:
        opts["max_turns"] = max_turns
    if output_format:
        opts["output_format"] = output_format
    options = ClaudeAgentOptions(**opts)

    os.environ["LARVLING_INTERNAL"] = "1"
    # Remove CLAUDECODE to prevent "nested session" guard in the subprocess
    saved_claudecode = os.environ.pop("CLAUDECODE", None)

    response_text = ""
    structured = None
    result_subtype = None
    usage_info = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                result_subtype = getattr(msg, "subtype", None)
                if msg.structured_output:
                    structured = msg.structured_output
                # Capture usage from ResultMessage
                msg_usage = getattr(msg, "usage", None)
                if msg_usage:
                    usage_info = msg_usage
                continue
            content = getattr(msg, "content", None)
            if not content:
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    response_text += text
    finally:
        os.environ.pop("LARVLING_INTERNAL", None)
        if saved_claudecode is not None:
            os.environ["CLAUDECODE"] = saved_claudecode

    if structured is not None:
        return structured, usage_info

    if output_format:
        raise RuntimeError(
            f"Structured output not returned (subtype={result_subtype})"
        )

    return response_text.strip(), usage_info
