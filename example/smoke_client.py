import argparse
import asyncio
import sys

from fastmcp import Client
from fastmcp.client.client import CallToolResult
from mcp.types import TextContent


REQUIRED_TOOLS = {
    "get_market_overview",
    "get_coin_details",
    "get_realtime_news",
    "get_telegram_message",
}


def _text_content(result: CallToolResult) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(block, TextContent)
    ).strip()


async def run_smoke(server_url: str) -> int:
    try:
        async with Client(server_url) as client:
            tools = await client.list_tools()
            tool_names = {tool.name for tool in tools}
            missing_tools = sorted(REQUIRED_TOOLS - tool_names)
            if missing_tools:
                raise RuntimeError(
                    f"server is missing expected tool(s): {', '.join(missing_tools)}"
                )

            result = await client.call_tool(
                "get_market_overview",
                {},
                raise_on_error=False,
            )
    except Exception as exc:
        print(f"MCP smoke check failed: {exc}", file=sys.stderr)
        print(
            "Start the server with `uv run python -m src.main`, then retry.",
            file=sys.stderr,
        )
        return 1

    text = _text_content(result)
    if result.is_error or not text:
        detail = text or "the tool returned no text"
        print(f"MCP smoke check failed: {detail}", file=sys.stderr)
        return 1

    print(f"Connected to {server_url}")
    print(f"Tools: {', '.join(sorted(tool_names))}")
    print()
    print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the crypto-info-mcp server through its HTTP MCP endpoint."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8123/mcp",
        help="Streamable HTTP MCP endpoint.",
    )
    args = parser.parse_args()
    return asyncio.run(run_smoke(args.url))


if __name__ == "__main__":
    raise SystemExit(main())
