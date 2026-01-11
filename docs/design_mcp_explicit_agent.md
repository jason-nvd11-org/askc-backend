# Design Document: MCP Integration - Option 1 (Explicit Agent)

## 1. 核心理念

本方案采用 **"Explicit Agent" (显式代理)** 模式。我们将为每一个 MCP Server（或每一类任务）构建一个独立的 `Agent` 类。前端在发起请求时，必须显式指定 `agent_type`，后端根据该标识将请求路由到对应的 Agent 进行处理。

此方案强调 **隔离性** 和 **确定性**。

## 2. 架构设计

### 2.1 架构图 (Mermaid)

```mermaid
graph TD
    User[前端用户] -->|"POST /chat {agent: 'github'}"| Router[Chat Router]
    Router --> Service[Chat Service]
    Service -->|"Switch(agent_type)"| Factory[Agent Factory]
    
    Factory -->|agent='github'| GithubAgent[Github Agent]
    Factory -->|agent='jira'| JiraAgent[Jira Agent]
    Factory -->|agent='general'| GeneralAgent[General Agent]
    
    subgraph GithubAgent Context
        GA_Prompt[System Prompt: Github Expert]
        GA_Conn[MCP Client]
        GA_LLM[Gemini Chat Model]
    end
    
    GithubAgent -->|Connect & List Tools| MCP_Server[MCP Server (GitHub)]
    GithubAgent -->|Bind Tools| GA_LLM
    
    GA_LLM -->|Function Call| MCP_Server
    MCP_Server -->|Result| GA_LLM
    GA_LLM -->|Response| User
```

### 2.2 核心组件

1.  **`BaseAgent` Interface**: 定义所有 Agent 必须实现的方法（如 `ainvoke`, `astream`）。
2.  **`GithubAgent` Class**:
    *   **职责**: 封装 GitHub 相关的 MCP 连接、工具绑定和 System Prompt。
    *   **初始化**: 启动时建立与 GitHub MCP Server 的 SSE 连接。
    *   **配置**: 加载 "You are a GitHub Helper..." 的 System Prompt。
3.  **`ChatRequest` Update**: API 请求体增加 `agent_type` 字段。
4.  **`ChatService` Logic**: 根据请求分发到不同的 Agent 实例。

## 3. 开发计划

### Phase 1: 基础建设
- [ ] **Dependencies**: 安装 `mcp` Python SDK。
- [ ] **Config**: 在 `config.py` 中添加 MCP Server URL 和 Token 配置。
- [ ] **Interface**: 创建 `src/agents/base.py` 定义 `BaseAgent` 抽象基类。

### Phase 2: 实现 GithubAgent
- [ ] **Implementation**: 创建 `src/agents/github_agent.py`。
    - 实现 MCP SSE 连接逻辑。
    - 获取 `get_repo_list` 等工具。
    - 将工具转换为 LangChain 格式并绑定到 LLM。
- [ ] **Testing**: 编写单元测试验证 `GithubAgent` 能成功调用 MCP 工具。

### Phase 3: 服务层集成
- [ ] **Schema**: 更新 `src/schemas/chat.py`，在 `ChatRequest` 中添加 `agent_type: str = "general"`。
- [ ] **Service**: 修改 `src/services/chat_service.py`，引入 Agent 路由逻辑。
- [ ] **Frontend (模拟)**: 使用 `curl` 或 Postman 发送带 `agent_type="github"` 的请求进行验证。

## 4. 优缺点分析

| 维度 | 评价 | 理由 |
| :--- | :--- | :--- |
| **可控性** | ⭐⭐⭐⭐⭐ | 前端明确指定意图，不会出现"我想聊家常结果调用了GitHub"的误判。 |
| **隔离性** | ⭐⭐⭐⭐⭐ | 不同 Agent 的 Prompt 和 Tools 完全物理隔离，互不干扰。 |
| **用户体验** | ⭐⭐⭐ | 用户需要手动选择 Agent，增加了操作负担。 |
| **扩展性** | ⭐⭐⭐⭐ | 新增 Agent 需要编写新类并注册到 Factory。 |
