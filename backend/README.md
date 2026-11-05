# 人群流动模拟系统后端

这是一个基于 `FastAPI` 的后端项目，当前已经完成 PDF 中核心主链路、运行期写入、分析查询、`TimescaleDB`、`Redis`、`RQ` 后台任务和统一错误码体系的第一版落地。

对应设计基线：

- [backend-module-plan.md](/E:/26-4-11/backend-module-plan.md)
- [database-schema.md](/E:/26-4-11/database-schema.md)
- [人群流动模拟系统 — 后端接口文档.pdf](</E:/26-4-11/人群流动模拟系统 — 后端接口文档.pdf>)

## 目录结构

```text
backend/
  app/
    api/
    application/
    domain/
    infrastructure/
    schemas/
    core/
  tests/
```

## 启动方式

方式一：在项目根目录 [crowdFlowSimulation](/E:/26-4-11/crowdFlowSimulation) 执行：

```bash
python -m backend
```

这是最直接的启动方式。因为 `backend/` 本身就是 Python 包目录，`python -m backend` 需要从它的父目录查找 `backend.__main__`。

方式二：如果当前已经进入 [backend](/E:/26-4-11/crowdFlowSimulation/backend) 目录，先把父目录加入 `PYTHONPATH`：

```powershell
$env:PYTHONPATH = ".."
python -m backend
```

不设置 `PYTHONPATH` 时，在 `backend/` 目录里执行 `python -m backend` 会让 Python 去找 `backend/backend/__main__.py`，因此会报 `No module named backend`。

maintenance worker 同理。如果当前在 `backend/` 目录内启动 worker：

```powershell
$env:PYTHONPATH = ".."
python -m backend run-maintenance-worker --without-scheduler
```

相关文档：

- [Swagger联调指南.md](<Swagger联调指南.md>)
- [部署与运维指南.md](<部署与运维指南.md>)
- [TimescaleDB接入说明.md](<TimescaleDB接入说明.md>)
- [真实模型决策引擎接入说明.md](<真实模型决策引擎接入说明.md>)
- [决策接口收口说明.md](<决策接口收口说明.md>)
- [正式压测报告.md](<正式压测报告.md>)

## 部署与运维速览

完整发布、回滚、worker 运维、Docker Compose 示例和日志排障说明见 [部署与运维指南.md](<部署与运维指南.md>)。

最常用的三个入口命令：

```bash
python -m alembic -c alembic.ini upgrade head
python -m backend
python -m backend run-maintenance-worker
```

上面三条默认从 `crowdFlowSimulation/` 项目根目录执行；如果在 `backend/` 目录内执行 `python -m backend ...`，需要先设置 `$env:PYTHONPATH = ".."`。

完整部署形态至少包含两个常驻进程：

- API 进程
- maintenance worker 进程

Docker 部署时也是同一个原则：`api` 和 `worker` 使用同一个镜像，但在 Compose 里拆成两个服务；数据库迁移单独用一次性 `migrate` 服务执行。

发布后立即检查：

- `GET /healthz`
- `GET /healthz/db`
- `GET /healthz/redis`

## 环境配置

项目会按 `APP_ENV` 自动加载环境文件：

- `APP_ENV=local` 时加载 `.env.local`
- `APP_ENV=production` 时加载 `.env.production`

示例文件：

- [`.env.example`](<.env.example>)
- [`.env.local.example`](<.env.local.example>)
- [`.env.production.example`](<.env.production.example>)

新增的轨迹查询性能相关配置：

- `APP_TRAJECTORY_QUERY_SAMPLE_INTERVAL_MS`
  控制秒级采样轨迹表的默认采样间隔
- `APP_TRAJECTORY_QUERY_MAX_RAW_WINDOW_MS`
  限制原始轨迹明细查询允许的最大时间窗口
- `APP_TRAJECTORY_QUERY_MAX_RAW_PAGE_SIZE`
  限制原始轨迹明细查询允许的最大分页大小

本地常用：

```bash
copy .env.local.example .env.local
```

生产环境由部署系统显式注入：

```bash
set APP_ENV=production
```

## 日志文件

当前后端已经支持控制台日志和本地文件日志双写。

默认行为：

- 日志目录：`backend/logs/`
- 文件名会自动带当天日期
  例如：`backend-2026-04-13.log`
- 默认单文件大小：`10 MB`
- 默认保留份数：`7`

## 分析查询性能现状

当前已经完成以下性能化改造：

- `/stats` 的 `zone_stats / exit_stats / decision_stats / agent_stats` 已优先读取分钟聚合表
- `/trajectories` 在 `compact` 模式下默认读取秒级采样轨迹表
- `/trajectories` 的原始明细查询已增加时间窗口和分页大小限制
- `/path-traces` 已改成“先选 agent，再查秒级采样轨迹”，避免先扫全量原始轨迹点

当前新增的分析表：

- `agent_metrics_1m`
- `trajectory_points_sampled_1s`

正式压测报告记录：

- 已完成首轮正式压测，报告见 [正式压测报告.md](<正式压测报告.md>)
- 报告中 `8/9` 项已通过 PDF `9.4` 阈值校验
- 唯一未通过项是“轨迹上报（2000 点批次）”，报告记录为 `P95=1186.70 ms`、平均吞吐 `1961.55/s`，尚未达到 `P95<=500 ms` 与 `>=2000 条/秒` 目标

正式压测命令：

```bash
python -m backend run-formal-benchmark
```

P0 压测套件命令：

```bash
python -m backend run-p0-benchmark-suite --base-url http://127.0.0.1:8000
```

说明：

- `run-formal-benchmark` 仍保留为单机应用层基线压测
- `run-p0-benchmark-suite` 需要先启动真实 HTTP 服务，覆盖真实 HTTP、并发阶梯、混合流量、完整闭环、维护链路和轨迹容量曲线
- P0 套件默认输出到 `backend/P0压测报告.md` 和 `backend/logs/benchmarks/p0-benchmark-latest.json`

相关配置项：

- `APP_LOG_LEVEL`
- `APP_LOG_TO_FILE`
- `APP_LOG_DIR`
- `APP_LOG_FILENAME`
- `APP_LOG_MAX_BYTES`
- `APP_LOG_BACKUP_COUNT`

补充说明：

- 日志文件只会写到本地，不会进入 Git 仓库
- 同一天内仍会按大小滚动，第二天会自动生成新的日期日志文件
- 当前 [`.gitignore`](<.gitignore>) 已经排除了 `logs/` 和 `*.log`

## 数据库与迁移

应用运行使用 `APP_DATABASE_URL`，迁移使用 `APP_ALEMBIC_DATABASE_URL`。

常用命令：

```bash
python -m alembic -c alembic.ini upgrade head
```

```bash
python -m alembic -c alembic.ini downgrade -1
```

```bash
python -m alembic -c alembic.ini current
```

## 请求头约定

当前实现已经按 PDF 收口到下面这套规则：

- 所有 `/api/v1` 业务接口都要求 `X-Request-Id`
- 所有实验级接口额外要求 `X-Experiment-Id`
- `X-Experiment-Id` 必须与路径中的 `experiment_id` 完全一致
- `GET /healthz*`、`/`、`/api/v1/docs`、`/api/v1/openapi.json` 不要求这两个请求头

典型错误：

- 缺少 `X-Request-Id`：`1002 / 400`
- 缺少 `X-Experiment-Id`：`1002 / 400`
- `X-Experiment-Id` 与路径实验 ID 不一致：`1003 / 400`

## 统一响应体与错误码

业务接口统一返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "req-demo-001",
  "timestamp_ms": 1775900000000
}
```

当前已经正式落地并实际使用的错误码段：

- `1001-1005`：请求解析、参数校验、限流预留、认证预留
- `2001-2004`：实验、拓扑、人群分配相关错误
- `4001-4002`：系统内部错误、依赖不可用
- `5001-5003`：人群模板相关错误

补充说明：

- `1004` 常量已保留，但当前还没有真实限流器
- `3001-3003` 常量已保留，但按 PDF `5.1` 语义，`decisions` 主路径会把单人调用失败、响应解析失败和批次硬超时吸收到批内 `fallback/none` 结果中，不作为常规 HTTP 错误返回
- `3004` 已用于决策引擎整体不可用且无法 fallback 的接口级失败

## 当前可用能力

- `GET /`
- `GET /healthz`
- `GET /healthz/db`
- `GET /healthz/redis`
- `GET /ui/analytics`
- `GET /api/v1/openapi.json`
- `GET/POST/PUT/DELETE /api/v1/crowd-types*`
- `POST /api/v1/experiments`
- `PUT /api/v1/experiments/{id}/topology`
- `PUT/GET /api/v1/experiments/{id}/crowd-allocation`
- `POST /api/v1/experiments/{id}/spawn-positions`
- `POST /api/v1/experiments/{id}/start`
- `POST /api/v1/experiments/{id}/decisions`
- `POST /api/v1/experiments/{id}/trajectories`
- `POST /api/v1/experiments/{id}/complete`
- `POST /api/v1/experiments/{id}/fail`
- `GET /api/v1/experiments/{id}/stats`
- `GET /api/v1/experiments/{id}/trajectories`
- `GET /api/v1/experiments/{id}/trajectories/{agent_id}`
- `POST /api/v1/experiments/{id}/heatmap`
- `POST /api/v1/experiments/{id}/heatmap/compare`
- `GET /api/v1/experiments/{id}/path-traces`

## 拓扑约束

当前拓扑接口在 PDF `4.2` 的基础上做了一项更保守的收口：

- PDF 写的是“同一 `source_zone_id` 下 `exit_id` 不可重复”
- 当前后端要求“同一实验内 `exit_id` 全局唯一”

这样做是为了保证下游只携带 `exit_id` 的链路没有歧义，包括：

- 决策响应里的 `chosen_exit_id`
- 轨迹上报里的 `target_exit_id`
- 出口统计、分钟聚合和分析查询里的 `exit_id`
- 日志与排障中的出口定位

因此 Unity / 编辑器侧不要在不同区域复用 `exit_1`、`exit_2` 这类局部编号，使用带来源和去向的全局可读 ID，例如：

```text
exit_lobby_to_ramp
exit_lobby_to_rest
exit_shop_to_plaza
```

如果同一实验内出现重复 `exit_id`，接口会返回 `2003 / 拓扑数据校验失败`。

## 人群模板规则

当前模板模块已经按 PDF 收口到以下内置模板规则：

- 模板响应中包含 `is_builtin`
- `is_builtin=true` 的模板不可删除，删除会返回 `1003`
- `is_builtin=true` 的模板只允许修改：
  - `display_name`
  - `color_hex`
  - `metadata`
- 修改内置模板的核心行为参数会返回 `1003`
- 复制模板后，新模板会变成普通模板

默认初始化方式：

- 执行 `python -m alembic -c alembic.ini upgrade head` 时，会自动运行迁移 `20260413_0014_seed_builtin_crowd_types.py`
- 该迁移会幂等写入系统内置模板 seed 数据，因此 `GET /api/v1/crowd-types` 默认就能看到 `is_builtin=true` 模板
- `seed_builtin_crowd_types(session)` 仍保留为测试和补数辅助方法，用于补齐缺失的内置模板

## Redis 与缓存

项目已经接入 Redis，当前真实用途包括：

- `spawn-positions` 显式 `seed` 的热缓存与幂等辅助
- `heatmap` / `heatmap/compare` 的热点缓存
- `decisions` 同实验同批次执行锁
- 通用 `X-Request-Id` 幂等响应快照
- `RQ` 队列与后台任务执行

Redis 默认开启。`X-Request-Id` 通用幂等、缓存和 RQ 链路依赖 Redis；决策 `batch_id` 与轨迹 `batch_id` 的最终幂等事实来源仍是数据库唯一约束和批次表记录，Redis 锁只用于减少并发处理中重复执行。

主要配置项：

- `APP_REDIS_ENABLED`
- `APP_REDIS_URL`
- `APP_REDIS_KEY_PREFIX`
- `APP_SPAWN_POSITIONS_CACHE_TTL_SECONDS`
- `APP_HEATMAP_CACHE_TTL_SECONDS`
- `APP_DECISION_BATCH_LOCK_TTL_SECONDS`
- `APP_REQUEST_IDEMPOTENCY_TTL_SECONDS`
- `APP_REQUEST_IDEMPOTENCY_LOCK_TTL_SECONDS`

Redis 健康检查：

```text
GET /healthz/redis
```

## TimescaleDB

当前 `trajectory_points` 已迁移为 `TimescaleDB hypertable`，并启用了压缩配置。

数据库健康检查会返回：

- `timescaledb.available`
- `timescaledb.installed`
- `timescaledb.ready`
- `trajectory_points_hypertable`

详情见 [TimescaleDB接入说明.md](<TimescaleDB接入说明.md>)。

## 后台任务与归档

当前后台任务采用“数据库任务表 + Redis + RQ worker”的方式，实验完成后会自动串起：

1. `analytics_refresh`
2. `heatmap_prewarm`
3. `runtime_data_cleanup`
4. `experiment_archive`

归档语义：

- `experiment_archive` 会写入独立的 `experiment_archives` 归档包表
- 归档包不通过外键依赖在线实验、人群模板或运行明细表
- 归档包会保留实验基本信息、拓扑、人群分配、模板快照、出生点、维护任务摘要、聚合摘要和运行数据保留情况
- `experiments.summary_json.archive` 只作为轻量索引，保存 `archive_id`、归档时间、归档版本和清理摘要
- 模板删除按 PDF `5003` 语义执行：只要仍被未归档实验引用就不能删；只被已归档实验引用时可以删除

启动常驻 worker：

```bash
python -m backend run-maintenance-worker
```

如果当前工作目录已经是 `backend/`，先设置父目录为包搜索路径：

```powershell
$env:PYTHONPATH = ".."
python -m backend run-maintenance-worker
```

Windows 本地联调时，如果 worker 在启用 RQ scheduler 后报 `ValueError: cannot find context for 'fork'`，可以临时关闭 scheduler：

```bash
python -m backend run-maintenance-worker --without-scheduler
```

在 `backend/` 目录内对应写法是：

```powershell
$env:PYTHONPATH = ".."
python -m backend run-maintenance-worker --without-scheduler
```

`--without-scheduler` 仍会消费已经进入队列的普通 RQ job，适合本地开发和 smoke check。代价是 RQ 的延迟 job 不会自动唤醒，因此失败重试或未来时间维护任务到期后，需要手动补派发一次：

```bash
python -m backend dispatch-maintenance-jobs --limit 20 --fanout 1
```

短时调试：

```bash
python -m backend run-maintenance-worker --burst --max-jobs 5
```

手动补派发待执行任务到 RQ：

```bash
python -m backend dispatch-maintenance-jobs --limit 20 --fanout 1
```

本地单进程兜底执行一轮：

```bash
python -m backend run-maintenance-jobs --limit 20
```

当前 worker 已具备：

- 启动前数据库与 Redis 预检
- 常驻消费
- 失败重试与指数退避
- 空闲心跳日志
- 优雅退出

RQ 相关 Redis 键的过期策略分为三层：

- `APP_MAINTENANCE_RQ_TTL_SECONDS`
  控制排队中或延迟调度中的 maintenance job 最长保留多久，默认 `86400` 秒
- `APP_MAINTENANCE_RQ_RESULT_TTL_SECONDS`
  控制成功执行后的结果保留多久，默认 `600` 秒
- `APP_MAINTENANCE_RQ_FAILURE_TTL_SECONDS`
  控制失败 job 的结果与失败信息保留多久，默认 `604800` 秒

如果你在 Redis 里排查 `rq:*` 键，通常会看到：

- 成功 job 会较快过期
- 失败 job 会保留更久，方便排障和重放
- 队列与 registry 这类结构键不一定都带 TTL，这是 RQ 自身的数据结构行为

## 维护任务运维命令

查询某个实验的维护任务状态：

```bash
python -m backend show-maintenance-jobs --experiment-id exp_xxx
```

仅看失败任务：

```bash
python -m backend show-maintenance-jobs --experiment-id exp_xxx --status failed
```

附带结果详情：

```bash
python -m backend show-maintenance-jobs --experiment-id exp_xxx --include-result
```

重置失败任务并自动补派发到 RQ：

```bash
python -m backend reset-maintenance-jobs --experiment-id exp_xxx
```

从指定任务开始重放后面的维护链路，并自动补派发到 RQ：

```bash
python -m backend replay-maintenance-jobs --experiment-id exp_xxx --from-job-id 123
```

## 显式清理实验运行期原始数据

立即清理某个实验的运行期原始数据：

```bash
python -m backend cleanup-runtime-data --experiment-id exp_xxx
```

保留热力图缓存：

```bash
python -m backend cleanup-runtime-data --experiment-id exp_xxx --keep-heatmap-cache
```

默认会删除：

- `decision_batches`
- `agent_decisions`
- `trajectory_batches`
- `trajectory_points`
- `heatmap_cache`

默认会保留：

- `experiments`
- `experiment_archives`
- 启动前配置表
- 分钟聚合表
- 秒级采样轨迹表

## 测试

安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

执行单元测试：

```bash
python -m pytest tests/unit -q -p no:cacheprovider
```

执行 integration 测试：

```bash
python -m pytest tests/integration -q -p no:cacheprovider
```

执行 e2e 测试：

```bash
python -m pytest tests/e2e -q -p no:cacheprovider
```

执行全部测试时显式指定 `tests` 目录：

```bash
python -m pytest tests -q -p no:cacheprovider
```

补充说明：

- `tests/integration` 与 `tests/e2e` 依赖 `.env.local` 中可用的 PostgreSQL / Redis
- integration 测试会使用真实 Redis / RQ，但会自动切到测试专用缓存前缀和队列名，避免污染默认联调数据
- 不要只运行 `python -m pytest` 扫描整个 `backend/` 目录；本地历史测试缓存目录可能被 pytest 当成待收集目录，导致权限错误
