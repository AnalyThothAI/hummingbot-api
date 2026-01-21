## 0. 角色与工作方式

- 你（AI 助手）等同一名中级工程师：
  - 实现新特性、修复 bug、小范围重构。
  - 优先改动小、回归风险可控的方案。
- 思考方式（简化版 CoT/AoT）：
  - 修改前用 2–6 行写清：问题、本质、影响范围、验收方式。
  - 任务拆成可一次提交完成的小步骤（原子任务）。

---

## 1. 核心工程原则

- **YAGNI**：不实现当前确实用不到的功能。
- **KISS**：能用简单方案就不用复杂方案。
- **DRY**：避免复制粘贴；共用逻辑抽到 shared/ 或 agents/shared/。
- 变更必须有 **可验证标准**：能说清「通过哪些测试/命令算完成」。

高风险改动（数据库 schema、交易决策算法、Store 结构等）要：
- 在说明中显式标注风险；
- 增加/更新测试覆盖关键路径；
- 给出简单回滚方案。

**Git 操作禁令（必须）**：
- **禁止** 执行 `git revert`、`git reset --hard`、`git checkout -- <file>` 等会丢弃用户未提交修改的命令；
- **禁止** 主动清理用户的工作区变更（包括 staged 和 unstaged）；
- 只在用户明确要求时才执行 commit / push 等提交操作；
- 如需回滚代码，应先告知用户影响范围并获得明确授权。

---

## 2. Hummingbot 开发规范

- **改动位置优先级**：策略/控制器优先落在 `bots/controllers`、`bots/scripts`；只有框架能力缺失时才改 `hummingbot/` 核心。
- **对齐官方风格（必须）**：修改 `hummingbot/` 时先对比官方 main 分支同文件；沿用既有写法与结构，避免引入全局/隐式行为改动（如默认值校验策略）；若必须偏离，需在说明中写清原因与影响。
- **核心改动门槛**：新增/扩展 executor 类型时，需同时更新：
  - `hummingbot/hummingbot/strategy_v2/executors/data_types.py` 的 `ExecutorConfigBase.type`
  - `hummingbot/hummingbot/strategy_v2/models/executors_info.py` 的 `AnyExecutorConfig` union
  - `hummingbot/hummingbot/strategy_v2/executors/executor_orchestrator.py` 的映射表
- **避免循环导入**：`executors/*/__init__.py` 不直接 import 实现类；如需导出类，用惰性导入或 `TYPE_CHECKING`。
- **配置与加载约定**：
  - `controller_name` 必须与模块文件名一致；
  - `controller_type` 必须与 `controllers/<type>/` 子目录一致；
  - `id` 是实例唯一标识；**推荐配置文件名 == id**（便于部署和复制）。
- **连接器/网关**：Controller 的 `update_markets` 必须注册 `connector_name` 与 `router_connector`；链上 swap 必须通过 executor 完成，Controller 不直接调用 connector。
- **数据与精度**：资金/价格统一使用 `Decimal`；配置参数使用 `Decimal` 默认值，避免 float 误差。
- **时间戳**：创建 ExecutorConfig 时显式传入 `timestamp=market_data_provider.time()`，不要依赖默认值。
- **日志**：记录状态机迁移与关键动作；避免每 tick 输出噪音日志。

---

## 3. V2 Controller 策略规范

- **职责边界**：Controller 只做“决策”，Executor 只做“执行”；两者通过 `ExecutorAction` 交互，Controller 不直接下单/改余额。
- **状态机**：使用显式状态枚举；单一时间源（`market_data_provider.time()`）；迁移必须幂等且可重入。
- **预算与锁**：
  - 多 executor 共享 `BudgetCoordinator` / `FixedBudgetPool`；
  - 使用 `budget_key`（默认 `config.id`）避免预算冲突；
  - 链上动作串行化（共享锁或全局串行）。
- **Swap 结算**：预算回填以 **实际成交量** 为准；失败时允许单边开仓，但必须记录原因。
- **风控与退出**：止损/止盈由 Controller 决策；是否清仓换 quote 通过配置控制；`manual_kill_switch` 必须立刻触发 stop。
- **多 Controller**：避免预算重叠；实例级全局回撤遵循 `v2_with_controllers.py` 参数。
- **可维护性**：Executor 运行数据通过 `executors_info.custom_info` 读取，不依赖内部对象引用。
