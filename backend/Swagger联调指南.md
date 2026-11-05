# Swagger 联调指南

本文档用于说明如何在 Swagger 中联调当前已经接入真实数据库、`Redis`、`TimescaleDB` 和后台任务链路的后端接口。

Swagger 地址：

[http://127.0.0.1:8000/api/v1/docs](http://127.0.0.1:8000/api/v1/docs)

## 1. 启动前准备

先确认：

- 已在 [backend](/E:/26-4-11/crowdFlowSimulation/backend) 目录完成数据库迁移
- `.env.local` 里的 PostgreSQL 和 Redis 配置可用
- 服务端口未被占用

常用命令：

```bash
python -m alembic -c alembic.ini upgrade head
python -m backend
```

上面的 `python -m backend` 默认从 [crowdFlowSimulation](/E:/26-4-11/crowdFlowSimulation) 项目根目录执行。原因是 `backend/` 本身就是 Python 包目录，Python 需要从它的父目录查找 `backend.__main__`。

如果当前已经在 [backend](/E:/26-4-11/crowdFlowSimulation/backend) 目录内，不需要退出目录，但要先设置：

```powershell
$env:PYTHONPATH = ".."
python -m backend
```

worker 也一样：

```powershell
$env:PYTHONPATH = ".."
python -m backend run-maintenance-worker --without-scheduler
```

## 2. 先理解请求头规则

这一步很重要。当前实现已经按 PDF 的约定收口：

- 所有 `/api/v1` 业务接口都必须带 `X-Request-Id`
- 所有实验级接口还必须带 `X-Experiment-Id`
- `X-Experiment-Id` 必须与路径里的 `experiment_id` 一致

你在 Swagger 里联调时，可以直接在每个接口的参数区域填写这两个请求头。

示例写法：

- `X-Request-Id`: `swagger-demo-001`
- `X-Experiment-Id`: 使用你创建实验后返回的真实实验 ID

常见错误：

- 缺 `X-Request-Id`：`1002 / 400`
- 缺 `X-Experiment-Id`：`1002 / 400`
- `X-Experiment-Id` 与路径不一致：`1003 / 400`

补充说明：

- `GET /healthz`
- `GET /healthz/db`
- `GET /healthz/redis`

这几个健康检查接口不要求这两个请求头。

## 3. 当前可直接联调的真实 API

- `GET /healthz`
- `GET /healthz/db`
- `GET /healthz/redis`
- `GET /api/v1/crowd-types`
- `GET /api/v1/crowd-types/{type_id}`
- `POST /api/v1/crowd-types`
- `PUT /api/v1/crowd-types/{type_id}`
- `DELETE /api/v1/crowd-types/{type_id}`
- `POST /api/v1/crowd-types/{type_id}/clone`
- `POST /api/v1/experiments`
- `PUT /api/v1/experiments/{experiment_id}/topology`
- `PUT /api/v1/experiments/{experiment_id}/crowd-allocation`
- `GET /api/v1/experiments/{experiment_id}/crowd-allocation`
- `POST /api/v1/experiments/{experiment_id}/spawn-positions`
- `POST /api/v1/experiments/{experiment_id}/start`
- `POST /api/v1/experiments/{experiment_id}/decisions`
- `POST /api/v1/experiments/{experiment_id}/trajectories`
- `POST /api/v1/experiments/{experiment_id}/complete`
- `POST /api/v1/experiments/{experiment_id}/fail`
- `GET /api/v1/experiments/{experiment_id}/stats`
- `GET /api/v1/experiments/{experiment_id}/trajectories`
- `GET /api/v1/experiments/{experiment_id}/trajectories/{agent_id}`
- `POST /api/v1/experiments/{experiment_id}/heatmap`
- `POST /api/v1/experiments/{experiment_id}/heatmap/compare`
- `GET /api/v1/experiments/{experiment_id}/path-traces`

## 4. 联调顺序

主链路按下面顺序测试：

1. `GET /healthz/db`
2. `POST /api/v1/crowd-types`
3. `POST /api/v1/experiments`
4. `PUT /api/v1/experiments/{experiment_id}/topology`
5. `PUT /api/v1/experiments/{experiment_id}/crowd-allocation`
6. `GET /api/v1/experiments/{experiment_id}/crowd-allocation`
7. `POST /api/v1/experiments/{experiment_id}/spawn-positions`
8. `POST /api/v1/experiments/{experiment_id}/start`
9. `POST /api/v1/experiments/{experiment_id}/decisions`
10. `POST /api/v1/experiments/{experiment_id}/trajectories`
11. `POST /api/v1/experiments/{experiment_id}/complete`
12. `GET /api/v1/experiments/{experiment_id}/stats`
13. `GET /api/v1/experiments/{experiment_id}/trajectories`
14. `GET /api/v1/experiments/{experiment_id}/trajectories/{agent_id}`
15. `POST /api/v1/experiments/{experiment_id}/heatmap`
16. `POST /api/v1/experiments/{experiment_id}/heatmap/compare`
17. `GET /api/v1/experiments/{experiment_id}/path-traces`

失败路径单独验证：

- `POST /api/v1/experiments/{experiment_id}/fail`

## 5. 联调时怎么操作

### 5.1 先检查数据库

打开 `GET /healthz/db`，点击：

`Try it out` -> `Execute`

预期关键返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "ok"
  }
}
```

如果你已经接好了 TimescaleDB，通常还会看到：

```json
{
  "timescaledb": {
    "available": true,
    "installed": true,
    "ready": true
  }
}
```

### 5.2 再按顺序执行业务接口

每一轮联调都按这套动作走：

1. 先创建当前轮要用的人群模板
2. 再创建实验，记下返回里的 `experiment_id`
3. 后面的所有实验级接口都带上：
   - `X-Request-Id`
   - `X-Experiment-Id`
4. `X-Experiment-Id` 就填上一步的 `experiment_id`
5. 提交拓扑
6. 配置人群分配
7. 生成出生点
8. 启动实验
9. 上报决策和轨迹
10. 完成实验
11. 最后再查统计、热力图和路径追踪

### 5.3 联调时要注意的事

- 每一轮尽量使用新的 `type_id` 和新的实验名，避免冲突
- 每一次业务请求都要用新的 `X-Request-Id`
- 同一个实验链路里，实验级接口的 `X-Experiment-Id` 始终保持一致
- 如果你重新提交拓扑，系统会清空旧的人群分配和旧的出生点
- 如果你重新配置人群分配，系统会清空旧的出生点
- 同一个实验、同一个 `seed` 重复调用 `spawn-positions`，会直接返回同一批结果
- `start` 只能在 `configured` 状态调用，重复启动会返回 `2002`
- 同一个 `X-Request-Id` 如果请求内容发生变化，服务会返回 `409`

## 6. 数据组 A：基础单模板联调

这组最适合第一次联调。

### 6.1 创建模板

打开 `POST /api/v1/crowd-types`，填上请求头：

- `X-Request-Id`: `swagger-a-001`

请求体：

```json
{
  "type_id": "tourist_demo_a",
  "display_name": "观光游客A",
  "color_hex": "#FF6B35",
  "base_speed": 1.2,
  "social_distance": 0.8,
  "tolerance_duration_ms": 30000,
  "attraction_weights": {
    "exhibit": 0.6,
    "commercial": 0.3,
    "rest": 0.2,
    "service": 0.2,
    "plaza": 0.4,
    "passage": 0.5,
    "exit_final": 0.1
  },
  "metadata": {}
}
```

### 6.2 创建实验

打开 `POST /api/v1/experiments`，填上请求头：

- `X-Request-Id`: `swagger-a-002`

请求体：

```json
{
  "name": "Swagger联调实验A",
  "description": "基础单模板联调",
  "map_id": "demo-map-a",
  "llm_config": {
    "model": "gpt-4.1-mini",
    "temperature": 0.6,
    "max_tokens": 512,
    "timeout_ms": 5000
  },
  "decision_config": {
    "batch_interval_ms": 1000,
    "batch_size": 20,
    "fallback_to_algorithm": true
  },
  "tags": ["swagger-a"]
}
```

执行后记下返回中的 `experiment_id`。

### 6.3 提交拓扑

打开 `PUT /api/v1/experiments/{experiment_id}/topology`：

- 路径参数 `experiment_id` 用上一步返回值
- `X-Request-Id`: `swagger-a-003`
- `X-Experiment-Id`: 与路径相同

```json
{
  "zones": [
    {
      "zone_id": "entry_a",
      "zone_name": "入口A",
      "zone_type": "passage",
      "bounds": {
        "min_x": 0,
        "min_z": 0,
        "max_x": 10,
        "max_z": 10
      },
      "capacity": 200,
      "is_entry_zone": true,
      "metadata": {}
    },
    {
      "zone_id": "hall_1",
      "zone_name": "展厅1",
      "zone_type": "exhibit",
      "bounds": {
        "min_x": 12,
        "min_z": 0,
        "max_x": 30,
        "max_z": 20
      },
      "capacity": 300,
      "is_entry_zone": false,
      "metadata": {}
    }
  ],
  "exits": [
    {
      "exit_id": "exit_1",
      "source_zone_id": "entry_a",
      "target_zone_id": "hall_1",
      "exit_type": "portal",
      "position": {
        "x": 11,
        "z": 5
      },
      "pass_capacity_per_sec": 20,
      "metadata": {}
    }
  ]
}
```

拓扑 ID 约束：

- `zone_id` 在同一实验内必须唯一
- `exit_id` 当前按同一实验内全局唯一处理
- PDF 原文只要求同一 `source_zone_id` 下 `exit_id` 不重复，但当前后端更严格，目的是让决策 `chosen_exit_id`、轨迹 `target_exit_id`、出口统计和分析查询在只携带 `exit_id` 时没有歧义
- 不要在多个区域里重复使用 `exit_1`、`exit_2`；使用 `exit_lobby_to_hall`、`exit_hall_to_shop` 这类全局可读命名
- 如果提交重复 `exit_id`，会返回 `2003 / 拓扑数据校验失败`

### 6.4 配置人群分配

打开 `PUT /api/v1/experiments/{experiment_id}/crowd-allocation`：

- `X-Request-Id`: `swagger-a-004`
- `X-Experiment-Id`: 与路径相同

```json
{
  "groups": [
    {
      "type_id": "tourist_demo_a",
      "count": 120,
      "distribution": {
        "mode": "zone_weighted",
        "spread_radius": 2.5,
        "avoid_exits": true,
        "zone_weights": [
          {
            "zone_id": "entry_a",
            "weight": 0.4
          },
          {
            "zone_id": "hall_1",
            "weight": 0.6
          }
        ]
      }
    }
  ],
  "agent_id_prefix": "swaggera"
}
```

预期：

- HTTP `200`
- `status = "configured"`
- `total_count = 120`

### 6.5 查询分配

打开 `GET /api/v1/experiments/{experiment_id}/crowd-allocation`：

- `X-Request-Id`: `swagger-a-005`
- `X-Experiment-Id`: 与路径相同

### 6.6 生成出生点

打开 `POST /api/v1/experiments/{experiment_id}/spawn-positions`：

- `X-Request-Id`: `swagger-a-006`
- `X-Experiment-Id`: 与路径相同

```json
{
  "seed": 42
}
```

预期：

- HTTP `200`
- `seed = 42`
- `total_count = 120`
- `agents[0].agent_id` 以 `swaggera_` 开头

### 6.7 启动实验

打开 `POST /api/v1/experiments/{experiment_id}/start`：

- `X-Request-Id`: `swagger-a-007`
- `X-Experiment-Id`: 与路径相同

```json
{
  "actual_start_time_ms": 1700000100000
}
```

### 6.8 提交决策批次

打开 `POST /api/v1/experiments/{experiment_id}/decisions`：

- `X-Request-Id`: `swagger-a-008`
- `X-Experiment-Id`: 与路径相同

```json
{
  "batch_id": "decision_batch_demo_a_001",
  "simulation_time_ms": 1000,
  "agents": [
    {
      "agent_id": "swaggera_0001",
      "crowd_type": "tourist_demo_a",
      "current_zone_id": "entry_a",
      "stay_duration_ms": 2600,
      "patience_ratio": 0.15,
      "algorithm_recommendation": "exit_1",
      "dilemma_summary": "入口区域逐渐拥挤，优先前往展厅1",
      "available_exits": [
        {
          "exit_id": "exit_1",
          "target_zone_id": "hall_1",
          "algorithm_score": 0.82,
          "nl_description": "前往展厅1的通道距离较短，当前拥挤度较低"
        }
      ]
    }
  ]
}
```

### 6.9 提交轨迹批次

打开 `POST /api/v1/experiments/{experiment_id}/trajectories`：

- `X-Request-Id`: `swagger-a-009`
- `X-Experiment-Id`: 与路径相同

```json
{
  "batch_id": "trajectory_batch_demo_a_001",
  "simulation_time_ms": 1000,
  "sample_interval_ms": 200,
  "points": [
    {
      "agent_id": "swaggera_0001",
      "crowd_type": "tourist_demo_a",
      "position": {
        "x": 2.0,
        "z": 3.5
      },
      "zone_id": "entry_a",
      "speed": 1.1,
      "state": "walking",
      "target_exit_id": "exit_1",
      "is_queuing": false
    }
  ]
}
```

### 6.10 完成实验

打开 `POST /api/v1/experiments/{experiment_id}/complete`：

- `X-Request-Id`: `swagger-a-010`
- `X-Experiment-Id`: 与路径相同

```json
{
  "reason": "simulation_finished",
  "actual_duration_ms": 120000,
  "final_agent_count": 120
}
```

### 6.11 查询统计和热力图

完成实验后，可以继续测：

- `GET /api/v1/experiments/{experiment_id}/stats`
- `GET /api/v1/experiments/{experiment_id}/trajectories`
- `GET /api/v1/experiments/{experiment_id}/trajectories/{agent_id}`
- `POST /api/v1/experiments/{experiment_id}/heatmap`
- `POST /api/v1/experiments/{experiment_id}/heatmap/compare`
- `GET /api/v1/experiments/{experiment_id}/path-traces`

这些接口都要继续带：

- `X-Request-Id`
- `X-Experiment-Id`

如果希望实验完成后的 `analytics_refresh`、`heatmap_prewarm`、`runtime_data_cleanup` 和 `experiment_archive` 都执行完，需要另开终端运行 maintenance worker：

```bash
python -m backend run-maintenance-worker --burst --max-jobs 10
```

Windows 下如果遇到 RQ scheduler 的 `cannot find context for 'fork'`，本地联调用：

```bash
python -m backend run-maintenance-worker --burst --max-jobs 10 --without-scheduler
```

## 7. 模板接口的额外说明

当前模板接口已经支持 `is_builtin` 字段返回，并按 PDF 收口了内置模板规则：

- `is_builtin=true` 的模板不可删除
- `is_builtin=true` 的模板只允许修改：
  - `display_name`
  - `color_hex`
  - `metadata`
- 复制内置模板后，新模板会变成普通模板

补充说明：

- 执行 `python -m alembic -c alembic.ini upgrade head` 后，会自动运行迁移 `20260413_0014_seed_builtin_crowd_types.py`
- `GET /api/v1/crowd-types` 默认 `include_builtin=true`，所以联调时可以直接看到 `is_builtin=true` 模板
- 如果只想看用户自定义模板，可以显式传 `include_builtin=false`
- 自定义模板删除按 PDF `5003` 语义判断：仍被未归档实验引用时不可删除；只被已归档实验引用时可以删除
- 实验归档会写入 `experiment_archives` 独立归档包，归档包中保留当时的人群分配与模板快照，因此已归档实验不再依赖在线模板表

## 8. 常见错误

- `1002`：缺请求头或缺必填字段
- `1003`：字段值不合法，或 `X-Experiment-Id` 与路径不一致
- `2001`：实验不存在
- `2002`：实验状态冲突
- `2003`：拓扑校验失败
- `2004`：人群分配校验失败
- `5001`：模板不存在
- `5002`：模板 ID 冲突
- `5003`：模板仍被未归档实验引用，无法删除

## 9. pytest 安装与运行

安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

执行单元测试：

```bash
python -m pytest tests/unit -q -p no:cacheprovider
```

执行集成测试：

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

不要只运行 `python -m pytest` 扫描整个 `backend/` 目录；本地历史测试缓存目录可能被 pytest 当成待收集目录，导致权限错误。
