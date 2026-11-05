# LLM Crowd Simulation

Unity 人群流动仿真、LLM 目的地决策和离线轨迹分析工具。Unity 负责场景、NavMesh 寻路、人物行为与轨迹采样；Python/FastAPI 后端负责实验管理、批量决策、轨迹存储和分析接口。

## 当前发布范围

- Unity 场景、C# 业务代码、预制体、导航数据和配置。
- FastAPI 后端、数据库迁移、模型适配器和测试。
- SDD 标注转换、热力矩阵、JSD、Top-5% 热点 F1、覆盖相似度分析脚本。
- 原项目的使用说明与决策逻辑文档。

**完整界面依赖 Modern UI Pack，需用户自行取得许可并导入。该商业资源没有包含在 Git 仓库中。** 后端和离线分析可独立于 Unity 界面运行。当前发布未经过 Unity 编辑器构建及完整数据库/模型端到端联调，不能视为已经验证的即开即用成品。

## 环境

| 组件 | 要求 |
| --- | --- |
| Unity | 原工程版本 `2022.3.62f1c1`，见 `ProjectSettings/ProjectVersion.txt` |
| Python | 3.12 或更新版本；本次验证使用 3.12 |
| 数据库 | PostgreSQL 16 + TimescaleDB |
| 队列/缓存 | Redis；RQ worker 建议在 Linux、WSL 或 macOS 运行 |
| Unity 包 | 由 `Packages/manifest.json` 和 `packages-lock.json` 声明 |
| UI | 自行安装 Modern UI Pack；详见 [第三方说明](THIRD_PARTY_NOTICES.md) |
| 模型 | 支持 Chat Completions 风格 API；启发式模式可用于不调用模型的联调 |

`c1` 是原工程版本标识；未验证其他 Unity 版本的兼容性。

## 启动后端

在仓库根目录，Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e "backend[dev]"
Copy-Item backend/.env.local.example backend/.env.local
```

Linux/macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e 'backend[dev]'
cp backend/.env.local.example backend/.env.local
```

在私有的 `backend/.env.local` 中配置数据库、Redis、端口和模型。建议先设置 `APP_DECISION_ENGINE_MODE=heuristic` 验证链路；这不会调用 LLM，不应作为 LLM 实验结果。完整界面的 App 场景默认连接 `http://127.0.0.1:8987`，模板已统一使用该端口。Map2 调试场景和部分原测试仍使用 8000，运行前应统一。

准备 PostgreSQL/TimescaleDB 和 Redis，创建 `crowd_flow_simulation_local` 数据库，并执行：

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
```

Windows 从仓库根目录执行迁移及启动：

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
Pop-Location
.venv\Scripts\python.exe -m backend
```

Linux/macOS：

```bash
(cd backend && ../.venv/bin/python -m alembic -c alembic.ini upgrade head)
.venv/bin/python -m backend
# 另开终端：
.venv/bin/python -m backend run-maintenance-worker --without-scheduler
```

检查 `/healthz`、`/healthz/db`、`/healthz/redis`；API 文档地址为 `http://127.0.0.1:8987/api/v1/docs`。数据库和 Redis 健康检查均通过后，再运行 Unity。详细步骤见 [后端启动指南](docs/后端启动指南.md)。

### 启用 LLM

在私有环境文件中设置以下变量，不要提交真实密钥：

```dotenv
APP_DECISION_ENGINE_MODE=openai_compatible
APP_DECISION_PROVIDER_BASE_URL=http://127.0.0.1:8001/v1
APP_DECISION_PROVIDER_API_KEY=YOUR_MODEL_SERVICE_KEY
APP_DECISION_PROVIDER_MODEL=YOUR_SERVED_MODEL_NAME
```

模型服务需支持项目使用的 JSON 输出格式。实际请求和 fallback 行为见 [模型接入说明](backend/真实模型决策引擎接入说明.md)。仓库不包含模型权重。

## 启动 Unity

1. 使用 Unity Hub 将仓库根目录添加为工程，安装匹配的 Unity 版本。
2. 按 [第三方说明](THIRD_PARTY_NOTICES.md) 导入 Modern UI Pack，并保留其 `.meta` GUID。未安装时 App/Test 场景存在缺失引用。
3. 等待 Unity 恢复依赖和导入资源。
4. 打开 `Assets/Application/Scenes/App.unity`。Build Settings 已包含 App、Map1、Map2、Map3。
5. 确认 ExperimentManager 的后端地址与服务端口一致，先启动后端和 worker，再进入 Play 模式。
6. 选择地图、配置人数和决策模式，创建并启动实验。完成后通过界面导出轨迹和决策日志。

参数、拓扑、固定路径和本地回退规则见 [项目使用说明](docs/项目使用说明.md) 与 [人群流动决策逻辑](docs/人群流动决策逻辑.md)。

## 离线分析

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-analysis.txt
.venv\Scripts\python.exe -m unittest discover -s offline_analysis/tests -v
.venv\Scripts\python.exe -m offline_analysis.pipeline --project-root "D:\experiment-data" --output-dir "D:\analysis-output"
```

输入目录结构：

```text
experiment-data/
  llm组/1..3/path_traces_*.csv
  llm组/1..3/transcript_*.txt
  无llm组/1..3/path_traces_*.csv
  无llm组/1..3/transcript_*.txt
  斯坦福行人数据集/annotations/deathCircle/video0/annotations.txt
```

`1..3` 表示三个独立子目录。SDD 原始数据与大体积实验导出不放入 Git 历史；参见 [数据说明](docs/DATA.md)。输入轨迹至少含 `time_ms,agent_id,x_world,y_world,decision_mode`。红绿叠加图是单独入口，运行方法和限制同见数据说明。

## 验证与限制

本次已验证项与尚未验证项见 [验证记录](docs/VALIDATION.md)。数据库相关测试会访问真实数据库，应仅对隔离测试实例运行。不要把整个 `backend/tests/unit` 误当作完全离线测试集。

原六次实验缺少完整运行时参数快照。LLM 组第 1 次原始轨迹的 mode 为 `fixedpath`；历史分析按目录分组并覆盖 mode/decision_source。发布版本保留该既有计算行为以重现结果，但不能据标准化标签证明实际 LLM 调用。SDD 坐标使用 min-max 映射，尚非控制点标定。

## 许可

项目原创源码按 [MIT](LICENSE) 发布。第三方库、字体、素材及数据遵循各自许可，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
