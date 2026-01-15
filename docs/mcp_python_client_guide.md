# Python 连接 MCP Server 全指南：协议解析与实战

Model Context Protocol (MCP) 正在重塑 LLM 应用与外部系统的交互范式。作为客户端开发者，理解如何高效、稳定地连接 MCP Server 是构建 Agent 的第一步。本文将深入剖析 Python 环境下的连接机制，重点对比 SSE 与 Streamable HTTP 两种传输协议，并提供生产级的代码示例。

## 1. 传输层协议：SSE vs Streamable HTTP

在 MCP 的架构中，传输层（Transport Layer）负责在 Client 和 Server 之间搬运 JSON-RPC 消息。目前主流的两种远程传输协议各有千秋。

### 1.1 Server-Sent Events (SSE)
SSE 是 Web 标准中用于服务器向客户端单向推送数据的技术。在 MCP 中，它通常结合 HTTP POST 使用。

*   **机制**：Client 建立一个长连接（GET /sse）用于接收 Server 的消息（Events）。Client 发送消息则通过另一个独立的 HTTP POST 请求。
*   **适用场景**：浏览器环境、需要穿越防火墙、标准 Web 服务。
*   **连接模型**：双通道（一条长读通道，一条短写通道）。

### 1.2 Streamable HTTP (Chunked Transfer)
这是一种更现代、更纯粹的 HTTP 交互方式，通常基于 HTTP/1.1 的 Chunked Transfer Encoding 或 HTTP/2。

*   **机制**：Client 和 Server 通过一个双向流（或长轮询变体）进行通信。在 MCP 的 Python SDK 实现中，它往往复用了底层 HTTP 客户端（如 `httpx`）的流式能力。
*   **适用场景**：Server-to-Server 通信、高性能后端服务。
*   **连接模型**：单通道（或复用通道），通常更节省资源，且不需要处理跨域（CORS）的复杂性（如果在同域后端）。

---

## 2. Python 客户端实战

我们将使用官方 `mcp` SDK 来构建客户端。无论使用哪种传输协议，核心的 `ClientSession` 逻辑是一致的，区别仅在于 `Transport` 的初始化。

### 2.1 依赖安装

```bash
pip install mcp httpx
```

### 2.2 连接建立：协议适配

#### 方案 A：使用 SSE 连接

这是最常见的连接方式。

```python
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

async def connect_via_sse(url: str, headers: dict = None):
    # sse_client 是一个 Async Context Manager
    # 它自动处理 EventSource 的连接与重连
    async with sse_client(url=url, headers=headers) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            yield session
```

#### 方案 B：使用 Streamable HTTP 连接

这是更底层的连接方式，通常需要自行管理 `httpx.AsyncClient` 的生命周期。

```python
import httpx
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession

async def connect_via_http(url: str, headers: dict = None):
    # 必须显式创建 httpx client，以便复用连接池和配置超时
    async with httpx.AsyncClient(headers=headers, timeout=60.0) as http_client:
        # streamable_http_client 同样返回读写流
        async with streamable_http_client(url=url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                yield session
```

---

## 3. 核心交互流程

一旦 `ClientSession` 建立，后续的操作就是标准的 MCP 协议交互。

### 3.1 握手与初始化 (Initialize)

连接建立后的第一件事必须是握手。Server 会在此时返回它的能力（Capabilities）和元数据（Server Info）。

```python
# 初始化会话
# 这一步会协商协议版本和能力
init_result = await session.initialize()

print(f"Connected to Server: {init_result.serverInfo.name} v{init_result.serverInfo.version}")

# 技巧：这里往往藏着 Server 的 System Prompt
if hasattr(init_result, 'instructions'):
    print(f"Server Instructions: {init_result.instructions}")
```

### 3.2 工具发现 (List Tools)

这是 Agent "获取技能" 的关键步骤。

```python
# 获取工具列表
list_tools_result = await session.list_tools()
tools = list_tools_result.tools

for tool in tools:
    print(f"Found Tool: {tool.name}")
    print(f"Description: {tool.description}")
    print(f"Schema: {tool.inputSchema}") # JSON Schema 格式的参数定义
```

**工程提示**：在实际应用中，你需要将这里的 `inputSchema` 转换为你的 LLM 框架（如 LangChain 或 OpenAI SDK）所需的格式。对于 LangChain，可以使用 `pydantic.create_model` 动态生成参数模型。

### 3.3 工具调用 (Call Tool)

当 LLM 决定调用工具时，Client 负责转发请求。

```python
from mcp.types import CallToolResult

tool_name = "get_repo_list"
arguments = {"owner": "nvd11", "limit": 5}

try:
    # 发送调用请求
    result: CallToolResult = await session.call_tool(tool_name, arguments=arguments)
    
    # 解析结果
    # MCP 支持 Text 和 Image 两种内容类型
    output_text = ""
    for content in result.content:
        if content.type == "text":
            output_text += content.text
        elif content.type == "image":
            print(f"[Image Content: {content.mimeType}]")
            
    print(f"Tool Output: {output_text}")

except Exception as e:
    print(f"Tool execution failed: {e}")
```

## 4. 常见问题 (FAQ)

### Q: 我可以用 `aiohttp` 代替 `httpx` 吗？
**A: 不可以直接替换。**
`mcp.client.streamable_http.streamable_http_client` 显式依赖于 `httpx.AsyncClient` 的接口。由于 `mcp` SDK 本身就将 `httpx` 作为核心依赖，建议遵循官方实践使用 `httpx`，以避免不必要的适配工作。

## 5. 总结

| 特性 | SSE | Streamable HTTP |
| :--- | :--- | :--- |
## 5. 总结

| 特性 | SSE | Streamable HTTP |
| :--- | :--- | :--- |
| **底层实现** | EventSource + POST | HTTP Streaming / Chunked |
| **SDK 模块** | `mcp.client.sse` | `mcp.client.streamable_http` |
| **依赖管理** | SDK 内部管理 | 需外部注入 `httpx.AsyncClient` |
| **适用性** | 浏览器友好，兼容性高 | 后端服务间通信，性能更优 |

对于大多数 Python 后端服务（如 Agent 平台），**Streamable HTTP** 提供了更好的控制力（如自定义 Timeout、Proxy 配置），是更为推荐的选择。
