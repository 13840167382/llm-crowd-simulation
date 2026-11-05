# TimescaleDB 接入说明

这份说明用于记录当前 TimescaleDB 接入状态、环境校验方式，以及轨迹原始数据的安全清理入口。

## 当前本地检查结果

当前本地环境最近一次探测结果是：

- PostgreSQL 版本：`18.3`
- `pg_available_extensions` 中已经可以看到 `timescaledb`
- `pg_extension` 中也已经能看到当前库已安装的 `timescaledb`
- 当前 `/healthz/db` 的状态是：
  - `timescaledb.available = true`
  - `timescaledb.installed = true`
  - `timescaledb.ready = true`
  - `timescaledb.trajectory_points_compression_enabled = true`
  - `timescaledb.trajectory_points_chunk_interval_ms = 3600000`

这说明 TimescaleDB 扩展已经在当前数据库启用成功，`trajectory_points` 已完成 `hypertable` 迁移，并且已经启用压缩参数配置。

## 接入前提

1. 数据库迁移已执行到最新版本
2. 当前数据库已安装并启用 `timescaledb`
3. 后端重启后，`GET /healthz/db` 返回 `timescaledb.ready=true`

## 如何自检

你可以在目标数据库里执行这几条 SQL：

```sql
SELECT version();

SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'timescaledb';

SELECT extname
FROM pg_extension
WHERE extname = 'timescaledb';

SELECT hypertable_name
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'trajectory_points';

SELECT hypertable_name, compression_enabled
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'trajectory_points';

SELECT hypertable, segmentby, orderby
FROM timescaledb_information.hypertable_compression_settings
WHERE hypertable = to_regclass(current_schema() || '.trajectory_points');

SELECT integer_interval, integer_now_func
FROM timescaledb_information.dimensions
WHERE hypertable_schema = current_schema()
  AND hypertable_name = 'trajectory_points'
ORDER BY dimension_number
LIMIT 1;

SELECT job_id, proc_name, scheduled, schedule_interval, next_start
FROM timescaledb_information.jobs
WHERE hypertable_schema = current_schema()
  AND hypertable_name = 'trajectory_points'
ORDER BY job_id;
```

## 切换完成后的验收标准

后端启动后，访问 `GET /healthz/db`，理想状态应至少满足：

```json
{
  "status": "ok",
  "timescaledb": {
    "available": true,
    "installed": true,
    "ready": true
  }
}
```

其中：

- `available=true` 表示数据库实例里已经有 `timescaledb` 扩展包
- `installed=true` 表示当前数据库已经执行过 `CREATE EXTENSION`
- `ready=true` 表示 `trajectory_points` 已经完成 hypertable 迁移
- `trajectory_points_compression_enabled=true` 表示轨迹表已经开启 TimescaleDB 压缩配置
- `trajectory_points_chunk_interval_ms=3600000` 表示当前按 1 小时模拟时间切 chunk
- `trajectory_points_jobs.compression_policy=null` 和 `trajectory_points_jobs.retention_policy=null` 是当前设计状态，不是异常

## 当前压缩策略

当前已经落地的压缩配置是：

- `timescaledb.compress = true`
- `timescaledb.compress_segmentby = 'experiment_id'`
- `timescaledb.compress_orderby = 'simulation_time_ms ASC'`
- chunk interval：`3600000 ms`

这样做的目标是：

1. 让查询先按 `experiment_id` 收敛
2. 在实验内部继续保留 `simulation_time_ms` 的时间顺序
3. 为实验级归档和清理任务提供统一的数据布局

## 为什么没有直接启用自动保留策略

当前 `trajectory_points` 的时间维度是 `simulation_time_ms`，它是“实验内部的相对模拟时间”，不是全局单调递增的真实时间。

这意味着：

- 不同实验通常都会从 `0 ms` 重新开始
- 同一个时间 chunk 里可能混有多个实验的数据
- 如果直接上 `add_retention_policy()` 或 `drop_chunks()`，存在误删仍在使用数据的风险

所以当前的安全策略是：

- 先启用 TimescaleDB 压缩配置
- 暂不直接启用自动 `retention policy`
- 保留策略收口为“按实验归档 / 清理”的后台任务方案

## 当前可执行的安全清理方式

目前已经提供“按实验安全清理原始运行数据”的入口：

显式按实验立即清理：

```bash
python -m backend cleanup-runtime-data --experiment-id exp_xxx
```

直接执行一轮数据库维护任务：

```bash
python -m backend run-maintenance-jobs --limit 20
```

常驻 RQ worker 消费维护队列：

```bash
python -m backend run-maintenance-worker
```

这些入口执行的清理动作会：

- 清理该实验的决策批次与决策明细
- 清理该实验的轨迹批次与轨迹点
- 默认一并清理该实验的热力图缓存
- 保留实验主记录、启动前配置、分钟聚合结果和归档摘要

它的定位是：

- 替代当前不安全的自动 `drop_chunks`
- 先按实验维度显式释放大体量原始明细数据
- 为后台归档 / 清理任务提供同一套业务规则

如果开启了 `APP_EXPERIMENT_RUNTIME_CLEANUP_AUTO_ENQUEUE=true`，那么实验在调用 `POST /complete` 成功后，会按顺序自动写入三条待处理维护任务：

- `analytics_refresh`
- `heatmap_prewarm`
- `runtime_data_cleanup`

`runtime_data_cleanup` 成功后会继续补入 `experiment_archive` 任务。完整维护链路最终是：

1. `analytics_refresh`
2. `heatmap_prewarm`
3. `runtime_data_cleanup`
4. `experiment_archive`

随后可以通过 `python -m backend run-maintenance-worker` 或 `python -m backend run-maintenance-jobs --limit 20` 处理这批任务。

其中自动清理会在已经预热热力图时保留热力图缓存，以便实验完成且原始轨迹已清理后，常用热力图请求仍能直接命中缓存。

如果同时启用了 Redis，那么这些热力图请求还会优先命中 Redis 热点缓存；Redis 未命中时再回退到数据库 `heatmap_cache`。
