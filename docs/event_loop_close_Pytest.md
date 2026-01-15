


## 1. 问题描述

在运行 `RetrievalService` 的集成测试（使用 `pytest-asyncio`）时，当连续运行多个异步测试用例时，遇到了以下错误：

```
RuntimeError: Task <Task pending ...> got Future <Future pending ...> attached to a different loop
...
RuntimeError: Event loop is closed
```

### 症状
- 第一个测试用例 (`test_search_knowledge_base_flow`) 成功通过。
- 第二个测试用例 (`test_search_knowledge_base_no_results`) 在 setup 或执行阶段立即失败，抛出 `RuntimeError`。

### 出错的代码（原始版本）
这是在修复之前，导致错误的测试代码结构和 `db_session` fixture：

```python
# test/services/test_retrieval_service.py

@pytest.fixture
async def db_session():
    """
    Creates a new database session for testing.
    """
    # 错误发生点：直接调用 get_async_engine()，它返回的是一个被缓存的 Engine 实例
    # 这个 Engine 绑定到了创建它时的 Event Loop（即第一个测试的 Loop）
    engine = get_async_engine() 
    
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session() as session:
        async with engine.begin() as conn: 
            await conn.run_sync(Base.metadata.create_all)
        yield session
        await session.rollback()

# 测试函数 1：使用新创建的 Loop A，成功获取 Engine（绑定到 Loop A）
@pytest.mark.asyncio
async def test_search_knowledge_base_flow(db_session):
    # ... PASS ...

# 测试函数 2：使用新创建的 Loop B
# 这里的 db_session fixture 再次运行，但 get_async_engine() 返回的是
# 绑定到已关闭的 Loop A 的旧 Engine。导致报错。
@pytest.mark.asyncio
async def test_search_knowledge_base_no_results(db_session):
    # ... FAIL with RuntimeError: Event loop is closed ...
```

## 2. 根本原因分析

### 2.1 冲突来源
该问题源于 `pytest-asyncio` 管理 Event Loop 的机制与我们应用程序创建 SQLAlchemy Engine 的方式之间存在冲突。

1.  **Pytest-Asyncio 的行为**：默认情况下（严格模式），`pytest-asyncio` 会为每个测试函数创建一个**新的** asyncio Event Loop，以确保隔离性。
2.  **应用程序的行为**：我们的 `src/configs/db.py` 使用了 `functools.lru_cache` 来缓存 `AsyncEngine` 实例：
    ```python
    # src/configs/db.py
    from functools import lru_cache

    @lru_cache()  # <--- Engine 实例被缓存了
    def get_async_engine():
        """
        Returns a cached async engine instance.
        The engine is created on the first call and reused on subsequent calls
        within the same event loop.
        """
        logger.info("Creating new async engine instance.")
        return create_async_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            echo=False,
        )
    ```

### 2.2 事件序列
1.  **测试 1 开始**：
    - Pytest 创建 **Loop A**。
    - `get_async_engine()` 被调用。它创建了 **Engine 1** 并将其绑定到 **Loop A**。
    - 测试 1 结束。Pytest 关闭 **Loop A**。
2.  **测试 2 开始**：
    - Pytest 创建 **Loop B**。
    - `get_async_engine()` 再次被调用。
    - 由于有缓存（`@lru_cache`），它返回了 **Engine 1**（这个 Engine 仍然绑定在已关闭的 **Loop A** 上）。
    - 当 SQLAlchemy 尝试使用 **Engine 1** 在 **Loop B** 中连接数据库或执行查询时，失败了，因为 Engine 的内部组件（如 `asyncpg` 连接池）试图使用已关闭的 Loop A。

## 3. 解决方案

### 3.1 修复方法
我们需要确保为每个测试上下文创建一个**新的 AsyncEngine**，并绑定到当前由 `pytest-asyncio` 提供的 Event Loop。

我们在测试文件 (`test/services/test_retrieval_service.py`) 的 `db_session` fixture 中修改了代码，在请求 Engine 之前显式清除缓存。

```python
@pytest.fixture
async def db_session():
    """
    Creates a new database session for testing.
    """
    # 修复：强制为当前 Event Loop 创建一个新的 Engine
    get_async_engine.cache_clear() 
    
    engine = get_async_engine() # 现在返回的是绑定到当前 Loop 的新 Engine
    
    # ... fixture 的其余部分 ...
```

### 3.2 为什么有效
通过调用 `get_async_engine.cache_clear()`，我们使缓存的 `AsyncEngine` 实例失效。随后的 `get_async_engine()` 调用会重新执行函数体，创建一个正确绑定到当前运行 Event Loop 的新 `AsyncEngine` 实例。

## 4. 替代方案（供参考）

1.  **Scope 匹配**：将 `event_loop` fixture 的 scope 更改为 `session`（所有测试共用一个 Loop）。这虽然降低了隔离性，但避免了多 Loop 问题。
2.  **依赖覆盖**：如果使用依赖注入框架，可以覆盖 `get_async_engine` 依赖。
3.  **全局 Conftest**：在 `conftest.py` 的 autouse fixture 中实现缓存清除，从而全局应用于所有测试。
