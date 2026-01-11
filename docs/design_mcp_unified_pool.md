# Design Document: MCP Integration - Option 2 (Unified Tool Pool)

## 1. 核心理念

本方案采用 **"Unified Tool Pool" (统一工具池)** 模式，即 **智能路由**。我们构建一个能够自动感知所有 MCP Server 的管理模块，将所有可用的工具（GitHub, Jira 等）聚合为一个大的工具池，并全量提供给 LLM。

LLM 充当“大脑”，根据用户的自然语言输入，**自主判断**是否需要调用工具，以及调用哪个工具。前端无需指定任何参数。

## 2. 架构设计

### 2.1 架构图 (Mermaid)

```mermaid
graph TD
    User[前端用户] -->|"POST /chat {message: '...'}"| Router[Chat Router]
    Router --> Service[Chat Service]
    
    subgraph Tool Infrastructure
        Manager[McpToolManager]
        MCP1["MCP Server (GitHub)"]
        MCP2["MCP Server (Jira)"]
        
        Manager -->|Connect| MCP1
        Manager -->|Connect| MCP2
        Manager -->|Aggregate| ToolPool[Unified Tool Pool]
    end
    
    Service -->|Get Tools| Manager
    Service -->|Bind All Tools| LLM[Smart LLM (Gemini/DeepSeek)]
    
    LLM -->|Decision: Call Tool?| Decision{Is Tool Needed?}
    Decision -->|Yes: get_repo_list| Manager
    Manager -->|Route to| MCP1
    MCP1 -->|Result| Manager
    Manager -->|Result| LLM
    
    Decision -->|No: Chit-chat| Response[Text Response]
    LLM -->|Final Response| User
```

### 2.2 核心组件

1.  **`McpToolManager` Class**:
    *   **职责**: 类似于 Plugin Manager。负责启动和维护与多个 MCP Server 的连接。
    *   **功能**: `get_all_tools()` 方法返回一个扁平化的 LangChain Tool 列表。
    *   **生命周期**: 应用启动时初始化连接，应用关闭时释放。
2.  **`ChatService` Logic**:
    *   在构建 LLM Chain 时，始终调用 `McpToolManager.get_all_tools()`。
    *   将这些工具 `bind` 到 LLM 实例上。
3.  **Frontend**: 完全无感知，维持原有的 `/chat` 接口调用方式。

## 3. 开发计划

### Phase 1: 基础建设与管理器
- [ ] **Dependencies**: 安装 `mcp` Python SDK。
- [ ] **Config**: 在 `config.py` 中支持 **列表式** 的 MCP 配置（支持配置多个 Server）。
- [ ] **Manager**: 创建 `src/services/mcp_tool_manager.py`。
    - 实现多路 SSE 连接管理。
    - 实现工具聚合逻辑。

### Phase 2: 服务层改造
- [ ] **Integration**: 修改 `src/services/chat_service.py`。
    - 引入 `McpToolManager`。
    - 在 `stream_chat_response` 中，将获取到的工具列表注入 LLM。
- [ ] **Testing**: 编写集成测试，模拟用户说 "列出仓库" 和 "你好" 两种场景，验证 LLM 的自主决策能力。

### Phase 3: 验证与优化
- [ ] **Validation**: 验证 GitHub 工具是否能被正确调用。
- [ ] **Prompt Engineering**: 如果工具较多，可能需要在 System Prompt 中简要描述工具的用途，辅助 LLM 决策（虽然 Function Calling 本身通常足够）。

## 4. 优缺点分析

| 维度 | 评价 | 理由 |
| :--- | :--- | :--- |
| **可控性** | ⭐⭐⭐ | 依赖 LLM 的判断，极少数情况可能出现"幻觉调用"（比如用户没想查库但它查了）。 |
| **隔离性** | ⭐⭐⭐ | 所有工具混在一起，需要通过命名规范避免冲突。 |
| **用户体验** | ⭐⭐⭐⭐⭐ | **极佳**。用户无需关心 Agent 概念，自然语言即入口。 |
| **扩展性** | ⭐⭐⭐⭐⭐ | 新增 MCP Server 只需改配置，代码零修改，LLM 自动获得新能力。 |
