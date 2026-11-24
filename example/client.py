"""Optional Gemini-powered conversational client for the MCP service."""

import argparse
import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult

load_dotenv()

GEMINI_MODEL = "gemini-2.5-flash"
MAX_TOOL_CALL_TURNS = 5
MAX_TOOL_CALLS = 20
QUIT_COMMANDS = frozenset({"quit", "exit"})


def _load_gemini() -> tuple[Any, Any]:
    """Load the optional SDK without making ``--help`` depend on it."""
    try:
        import google.genai as genai  # pyright: ignore[reportMissingImports]
        from google.genai import types  # pyright: ignore[reportMissingImports]
    except ModuleNotFoundError as exc:
        missing_module = exc.name or ""
        if missing_module in {"google", "google.genai"} or missing_module.startswith(
            "google.genai."
        ):
            raise RuntimeError(
                "Gemini client dependency is missing. Run this example with "
                "`uv run --with google-genai python example/client.py`."
            ) from exc
        raise
    return genai, types


def _tool_result_response(tool_result: CallToolResult) -> dict[str, Any]:
    """Convert all MCP content blocks into Gemini function-response data."""
    function_response_payload: dict[str, Any] = {
        "content": [
            block.model_dump(mode="json", exclude_none=True) for block in tool_result.content
        ],
        "isError": tool_result.isError,
    }
    if tool_result.structuredContent is not None:
        function_response_payload["structuredContent"] = tool_result.structuredContent
    return function_response_payload


class CryptoAssistantClient:
    """Bridge Gemini function calls to a crypto-info-mcp session."""

    def __init__(self) -> None:
        self._genai_module, self._gemini_types = _load_gemini()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is required for the conversational example client.")

        self._gemini_client = self._genai_module.Client(api_key=api_key)
        self._exit_stack = AsyncExitStack()
        self._mcp_session: ClientSession | None = None
        self._gemini_chat: Any | None = None

    async def connect(self, server_url: str) -> None:
        """Open and initialize an MCP session."""
        print(f"Connecting to {server_url} ...")
        try:
            read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                streamablehttp_client(url=server_url, headers={})
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except Exception:
            self._mcp_session = None
            try:
                await self._exit_stack.aclose()
            except Exception as cleanup_error:
                print(f"Client cleanup warning: {cleanup_error}")
            raise

        self._mcp_session = session
        print("Connected to the MCP server.")

    def _mcp_tools_to_gemini_tools(self, mcp_tools: list[Any]) -> list[Any]:
        function_declarations = [
            self._gemini_types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=tool.inputSchema,
            )
            for tool in mcp_tools
        ]
        if not function_declarations:
            return []
        return [self._gemini_types.Tool(function_declarations=function_declarations)]

    def _ensure_chat(self, available_tools: list[Any]) -> Any:
        if self._gemini_chat is None:
            self._gemini_chat = self._gemini_client.chats.create(
                model=GEMINI_MODEL,
                config=self._gemini_types.GenerateContentConfig(
                    system_instruction=(
                        "당신은 암호화폐 시장 정보를 간결하게 설명하는 분석 도우미입니다. "
                        "사실 확인에 필요한 경우 제공된 MCP 도구를 사용하고, "
                        "도구 결과에 없는 사실은 추측하지 마세요."
                    ),
                    tools=available_tools,
                    automatic_function_calling=self._gemini_types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
        return self._gemini_chat

    @staticmethod
    def _function_calls(model_response: Any) -> list[Any]:
        return [
            part.function_call
            for part in getattr(model_response, "parts", [])
            if getattr(part, "function_call", None)
        ]

    async def _execute_tool_calls(self, function_calls: list[Any]) -> list[Any]:
        if self._mcp_session is None:
            raise ConnectionError("MCP 서버에 연결되지 않았습니다.")

        requested_calls = [
            (function_call.name, dict(function_call.args)) for function_call in function_calls
        ]
        for tool_name, tool_arguments in requested_calls:
            print(f"Calling MCP tool: {tool_name}({tool_arguments})")

        results = await asyncio.gather(
            *(
                self._mcp_session.call_tool(tool_name, tool_arguments)
                for tool_name, tool_arguments in requested_calls
            )
        )
        return [
            self._gemini_types.Part.from_function_response(
                name=tool_name,
                response=_tool_result_response(tool_result),
            )
            for (tool_name, _), tool_result in zip(
                requested_calls,
                results,
                strict=True,
            )
        ]

    async def process_query(self, query: str) -> str:
        """Resolve one user query, including bounded MCP tool-call turns."""
        if self._mcp_session is None:
            raise ConnectionError("MCP 서버에 연결되지 않았습니다.")

        listed_tools = await self._mcp_session.list_tools()
        available_tools = self._mcp_tools_to_gemini_tools(listed_tools.tools)
        chat = self._ensure_chat(available_tools)

        print("Sending the query to Gemini ...")
        model_response = await asyncio.to_thread(chat.send_message, query)
        total_tool_calls = 0

        for tool_call_turn in range(MAX_TOOL_CALL_TURNS + 1):
            function_calls = self._function_calls(model_response)
            if not function_calls:
                return model_response.text
            if tool_call_turn == MAX_TOOL_CALL_TURNS:
                break

            requested_count = len(function_calls)
            if total_tool_calls + requested_count > MAX_TOOL_CALLS:
                raise RuntimeError(f"Gemini requested more than {MAX_TOOL_CALLS} total tool calls.")
            total_tool_calls += requested_count

            function_responses = await self._execute_tool_calls(function_calls)
            print("Returning MCP results to Gemini ...")
            model_response = await asyncio.to_thread(
                chat.send_message,
                function_responses,
            )

        raise RuntimeError(
            f"Gemini requested more than {MAX_TOOL_CALL_TURNS} consecutive tool-call turns."
        )

    async def chat_loop(self) -> None:
        """Run the interactive terminal loop."""
        print("\nCrypto Info MCP conversational client")
        print("Enter 'quit' or 'exit' to close the client.")

        while True:
            try:
                query = (await asyncio.to_thread(input, "\nYou: ")).strip()
                if query.lower() in QUIT_COMMANDS:
                    break
                if not query:
                    continue

                answer = await self.process_query(query)
                print(f"\nAssistant:\n{answer}")
            except EOFError:
                print("\nInput closed; exiting the client.")
                break
            except Exception as exc:
                print(f"\nQuery failed: {exc}")
                print("Starting a new Gemini conversation.")
                self._gemini_chat = None

    async def cleanup(self) -> None:
        """Close every entered MCP context in reverse order."""
        self._mcp_session = None
        try:
            await self._exit_stack.aclose()
        except Exception as exc:
            print(f"Client cleanup warning: {exc}")
        print("\nClient closed.")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Conversational client for the Crypto Info MCP server."
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="MCP server hostname or IP address.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8123,
        help="MCP server port.",
    )
    return parser


async def main() -> int:
    args = _argument_parser().parse_args()
    server_url = f"http://{args.host}:{args.port}/mcp"
    client: CryptoAssistantClient | None = None

    try:
        client = CryptoAssistantClient()
        await client.connect(server_url)
        await client.chat_loop()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Client startup failed: {exc}")
        return 1
    finally:
        if client is not None:
            await client.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
