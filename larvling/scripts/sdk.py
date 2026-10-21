"""Claude Agent SDK integration for Larvling.

Provides call_model() — the shared interface for calling Claude via
the Agent SDK with structured output support, monkey-patched message
parsing, and LARVLING_INTERNAL guarding.
"""

import os

from config import get_config


async def call_model(prompt, allowed_tools=None, max_turns=None, output_format=None):
    """Call the LLM via Agent SDK and return the response.

    Returns (result, usage_info) tuple where:
    - result is structured_output (dict) when output_format is set,
      otherwise response text (str).
    - usage_info is the usage dict from ResultMessage, or None if
      not available.

    Sets LARVLING_INTERNAL to prevent sub-agent from triggering hooks.
    """
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
        from claude_agent_sdk._internal.message_parser import parse_message  # noqa: PLC2701
        from claude_agent_sdk._errors import MessageParseError  # noqa: PLC2701
        import claude_agent_sdk._internal.client as _sdk_client  # noqa: PLC2701
        import claude_agent_sdk as _sdk_pkg  # noqa: PLC2701
    except ImportError:
        raise RuntimeError(
            "claude_agent_sdk is required but not installed. "
            "Install it with: pip install claude-agent-sdk"
        )

    # Verify the SDK version is compatible with our monkey-patch.
    # The patch targets _internal.client.parse_message which may move or
    # change signature in future SDK releases.
    _TESTED_SDK_VERSIONS = ("0.1",)
    sdk_version = getattr(_sdk_pkg, "__version__", "")
    if sdk_version and not any(sdk_version.startswith(v) for v in _TESTED_SDK_VERSIONS):
        import warnings
        warnings.warn(
            f"claude_agent_sdk {sdk_version} has not been tested with Larvling's "
            f"parse_message patch (tested: {', '.join(_TESTED_SDK_VERSIONS)}.*). "
            "If extraction fails, pin claude-agent-sdk to a tested version.",
            stacklevel=2,
        )

    # Patch parse_message to skip unknown message types instead of crashing.
    # The SDK (as of 0.1.39) doesn't handle rate_limit_event and other CLI
    # message types, which kills the async generator mid-stream and loses
    # all subsequent messages including the ResultMessage with structured_output.
    # Note: not concurrent-safe — callers use asyncio.run() (one loop at a time).
    def _tolerant_parse(data):
        try:
            return parse_message(data)
        except MessageParseError:
            return None

    opts = {"model": get_config()["model"], "allowed_tools": allowed_tools or []}
    if max_turns is not None:
        opts["max_turns"] = max_turns
    if output_format:
        opts["output_format"] = output_format
    options = ClaudeAgentOptions(**opts)

    os.environ["LARVLING_INTERNAL"] = "1"
    # Remove CLAUDECODE to prevent "nested session" guard in the subprocess
    saved_claudecode = os.environ.pop("CLAUDECODE", None)
    setattr(_sdk_client, "parse_message", _tolerant_parse)

    response_text = ""
    structured = None
    result_subtype = None
    usage_info = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if msg is None:
                continue
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
        setattr(_sdk_client, "parse_message", parse_message)

    if structured is not None:
        return structured, usage_info

    if output_format:
        raise RuntimeError(
            f"Structured output not returned (subtype={result_subtype})"
        )

    return response_text.strip(), usage_info
