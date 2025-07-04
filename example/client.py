# 파일 2: FastMCP 클라이언트

import os
import argparse
import asyncio
from typing import Optional, Dict, Any, List

import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
from dotenv import load_dotenv

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# .env 파일에서 환경 변수 로드
load_dotenv()

# Gemini API 키 설정
genai.configure(api_key=os.getenv("GOOGLE_API_KEY", ''))

class CryptoAssistantClient:
    """
    'Intelligent Crypto Assistant' MCP 서버와 통신하는 클라이언트 클래스.
    Gemini를 사용하여 사용자의 질문을 이해하고 서버의 도구를 호출합니다.
    """
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.model = genai.GenerativeModel(
            'gemini-2.5-flash', 
            system_instruction="당신은 친절하고 전문적인 암호화폐 시장 분석가입니다. 사용자의 질문에 답하기 위해 사용 가능한 도구를 활용하세요."
        )
        self.chat = None
        self._streams_context = None
        self._session_context = None

    async def connect(self, server_url: str):
        """지정된 URL의 MCP 서버에 연결하고 세션을 초기화합니다."""
        print(f"...{server_url} 에 연결 중...")
        try:
            self._streams_context = streamablehttp_client(url=server_url, headers={})
            read_stream, write_stream, _ = await self._streams_context.__aenter__()
            self._session_context = ClientSession(read_stream, write_stream)
            self.session = await self._session_context.__aenter__()
            await self.session.initialize()
            print("✅ 서버에 성공적으로 연결되었습니다!")
        except Exception as e:
            print(f"❌ 서버 연결 실패: {e}")
            raise

    def _remove_keys_recursively(self, obj: Any, keys_to_remove: List[str]) -> Any:
        """딕셔너리/리스트에서 특정 키들을 재귀적으로 제거합니다."""
        if isinstance(obj, dict):
            return {
                key: self._remove_keys_recursively(value, keys_to_remove)
                for key, value in obj.items() if key not in keys_to_remove
            }
        elif isinstance(obj, list):
            return [self._remove_keys_recursively(item, keys_to_remove) for item in obj]
        return obj

    def _mcp_tools_to_gemini_tools(self, mcp_tools: list) -> list[Tool]:
        """MCP 도구 스키마를 Gemini가 이해할 수 있는 형식으로 변환합니다."""
        gemini_tools = []
        # Gemini API와 호환되지 않아 제거해야 할 스키마 필드 목록
        keys_to_remove = ['title', 'default']

        for tool in mcp_tools:
            # 재귀 함수를 사용해 불필요한 키들을 제거
            gemini_compatible_schema = self._remove_keys_recursively(tool.inputSchema, keys_to_remove)

            function_declaration = FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=gemini_compatible_schema,
            )
            gemini_tools.append(Tool(function_declarations=[function_declaration]))
        return gemini_tools

    async def process_query(self, query: str) -> str:
        """사용자 쿼리를 처리하고, 필요 시 도구를 호출한 뒤 최종 답변을 반환합니다."""
        if not self.session:
            raise ConnectionError("MCP 서버에 연결되지 않았습니다.")

        # 서버로부터 사용 가능한 도구 목록을 가져와 Gemini 형식으로 변환
        response = await self.session.list_tools()
        available_tools = self._mcp_tools_to_gemini_tools(response.tools)

        if self.chat is None:
            self.chat = self.model.start_chat(enable_automatic_function_calling=False)

        print("...Gemini에게 질문을 보내는 중...")
        response = self.chat.send_message(query, tools=available_tools)

        response_part = response.parts[0]
        if response_part.function_call:
            fc = response_part.function_call
            tool_name = fc.name
            tool_args = dict(fc.args)

            print(f"🛠️ Gemini가 도구 호출을 요청합니다: {tool_name}({tool_args})")
            tool_result_mcp = await self.session.call_tool(tool_name, tool_args)
            print("...도구 실행 결과를 Gemini에게 다시 보내는 중...")

            # MCP 서버의 응답(Streamable)에서 실제 텍스트 내용을 추출
            tool_response_content = ""
            if isinstance(tool_result_mcp.content, list) and tool_result_mcp.content:
                tool_response_content = tool_result_mcp.content[0].text

            # 추출한 결과를 Gemini에 전달하여 최종 답변 생성
            response = self.chat.send_message(
                [{"function_response": {
                    "name": tool_name,
                    "response": {"content": tool_response_content},
                }}]
            )

        return response.text

    async def chat_loop(self):
        """사용자와 상호작용하는 메인 채팅 루프입니다."""
        print("\n🤖 지능형 암호화폐 비서 클라이언트입니다.")
        print("   'quit' 또는 'exit'을 입력하여 종료하세요.")

        while True:
            try:
                query = input("\n👤 You: ").strip()
                if query.lower() in ["quit", "exit"]:
                    break
                if not query: continue

                response_text = await self.process_query(query)
                print(f"\n🤖 Assistant:\n{response_text}")

            except Exception as e:
                print(f"\n💥 예기치 않은 오류 발생: {e}")
                print("   대화를 다시 시작합니다.")
                self.chat = None # 오류 발생 시 대화 상태 초기화

    async def cleanup(self):
        """클라이언트 종료 시 연결을 안전하게 해제합니다."""
        if self._session_context: await self._session_context.__aexit__(None, None, None)
        if self._streams_context: await self._streams_context.__aexit__(None, None, None)
        print("\n👋 클라이언트를 종료합니다.")

async def main():
    """스크립트 실행 시 인자를 파싱하고 클라이언트를 시작합니다."""
    parser = argparse.ArgumentParser(description="Client for Intelligent Crypto Assistant MCP server.")
    parser.add_argument("--host", type=str, default="localhost", help="The hostname or IP address.")
    parser.add_argument("--port", type=int, default=8123, help="The port number of the MCP server.") 
    args = parser.parse_args()

    server_url = f"http://{args.host}:{args.port}"
    client = CryptoAssistantClient()

    try:
        await client.connect(server_url)
        await client.chat_loop()
    except KeyboardInterrupt:
        pass 
    except Exception as e:
        print(f"클라이언트 시작 중 심각한 오류 발생: {e}")
    finally:
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())