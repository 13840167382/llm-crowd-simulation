# 本地 vLLM 与 Qwen3.6-35B-A3B 接入

## 哪个实现支持本地模型

公开源码使用同一个 `OpenAICompatibleDecisionEngine` 连接本地或远程模型服务，不需要切换 Git 分支或替换 Unity 工程。改变服务地址、模型别名和访问密钥即可。仓库没有加载模型权重的进程，推理服务独立运行。

项目维护者已确认原实验使用 vLLM 和 Qwen3.6-35B-A3B 模型系列。具体权重修订、量化精度、vLLM 版本及 GPU 配置尚未从原运行记录核实。因此以下是接入示例，不是原六次实验的完整启动快照，也不能据此把原实验写成 FP8 或其他量化版本。

调用链：Unity → FastAPI（默认 8987）→ vLLM（本例 8001）→ 模型输出 JSON → 目的地校验 → Unity NavMesh。

| 设置 | 实际含义 |
| --- | --- |
| 后端 `openai_compatible` / `openai` / `llm` | 调用兼容接口，既可指向本地 vLLM，也可指向远程服务 |
| 后端 `heuristic` / `mock` / `local` | 使用 `HeuristicDecisionEngine`，不调用 LLM |
| Unity `LocalOnly` | 使用客户端本地评分算法，不向后端发出决策请求；其他实验管理请求仍可能使用后端 |
| Unity `LlmOnly` | 将决策请求交给后端；后端仍可能配置成 heuristic 或触发 fallback，不能只靠该选项证明 LLM 调用 |
| Unity `HybridPreferLlm` | 优先后端，部分情况下使用客户端算法兜底 |

源码依据：[后端引擎选择](../backend/app/application/decisions/engine.py)、[模型 HTTP 请求](../backend/app/application/decisions/llm_engine.py)、[客户端调度](../Assets/Application/Scripts/Simulation/Decision/DecisionBatchScheduler.cs)。

## 1. 启动模型服务

在能运行该模型的 Linux/GPU 或受支持推理环境中，为 vLLM 建立独立环境；不要直接把推理依赖装进项目后端环境。GPU、驱动和安装方法参照 [vLLM 安装文档](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)。示例：

```bash
python3.12 -m venv .venv-vllm
source .venv-vllm/bin/activate
python -m pip install 'vllm>=0.19.0'
```

Qwen 官方模型卡推荐 vLLM 0.19.0 或更新版本；重现历史实验时应固定实际使用的版本，不能把本例的版本范围当成已测环境。

先通过正常的模型下载方式取得权重、tokenizer 和 chat template。下面的绝对路径需要替换成本机实际权重目录：

```bash
read -r -s -p 'vLLM API key: ' CROWD_MODEL_API_KEY
echo
export VLLM_API_KEY="$CROWD_MODEL_API_KEY"

vllm serve /absolute/path/to/Qwen3.6-35B-A3B \
  --served-model-name qwen36-crowd \
  --host 127.0.0.1 \
  --port 8001 \
  --reasoning-parser qwen3 \
  --generation-config vllm
```

也可将模型路径替换为官方模型标识 `Qwen/Qwen3.6-35B-A3B`，由推理工具下载。实际显存和张量并行需求取决于权重精度、上下文长度及硬件，必要时按实测配置 `--tensor-parallel-size` 和 `--max-model-len`；上面的示例不保证任意单卡都能加载模型。

`qwen36-crowd` 是本示例的服务别名，不代表另一套权重。若原实验使用 FP8，应使用相应 FP8 权重目录并明确记录，不能通过改别名把普通权重变成 FP8。

本例 `--generation-config vllm` 用于避免自动读取模型目录内的生成默认值。若原实验使用模型自带生成配置，应保留原方式并记录；两种方式不能视为同一实验设置。

### 思考模式

Qwen 支持思考模式，耗时会影响本项目的单人和批次超时。当前后端没有发送 `chat_template_kwargs`，由服务端模板默认设置决定。

如果确定要做不启用思考的接入检查，并且所用 vLLM 版本提供该参数，可在服务启动命令后增加：

```bash
--default-chat-template-kwargs '{"enable_thinking": false}'
```

这是可选的新配置，不是已确认的原实验设置。先通过 `vllm serve --help` 检查版本支持，论文需报告实际思考模式。后端读取 `choices[0].message.content` 中的最终 JSON；不要只返回单独的推理文本而缺少最终 content。

## 2. 检查 vLLM 接口

在模型服务器的 Bash 中执行；`CROWD_MODEL_API_KEY` 沿用上一步输入值：

```bash
curl --fail-with-body http://127.0.0.1:8001/v1/models \
  -H "Authorization: Bearer $CROWD_MODEL_API_KEY"

curl --fail-with-body http://127.0.0.1:8001/v1/chat/completions \
  -H "Authorization: Bearer $CROWD_MODEL_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{
    "model":"qwen36-crowd",
    "temperature":0,
    "response_format":{"type":"json_object"},
    "messages":[
      {"role":"system","content":"Return only a JSON object with chosen_exit_id, reasoning and confidence."},
      {"role":"user","content":"The only available exit is exit_a. Choose it and return a confidence between 0 and 1."}
    ]
  }'
```

确认模型列表中有 `qwen36-crowd`，并且第二个响应的 `choices[0].message.content` 可以解析为 JSON：`chosen_exit_id` 为 `exit_a`，`confidence` 为 0 到 1 的数值。这只是模型接口检查，不会创建或运行完整仿真实验。

## 3. 配置 FastAPI 后端

按 [README](../README.md) 完成 PostgreSQL/TimescaleDB、Redis 和后端依赖。复制 `backend/.env.local.example` 为私有的 `backend/.env.local` 后，将 [模型配置示例](../examples/local-llm.env.example) 中的配置合并进去，替换其中的 API key。这个 examples 文件不会自动加载，不能只修改它。

关键值如下：

```dotenv
APP_DECISION_ENGINE_MODE=openai_compatible
APP_DECISION_PROVIDER_BASE_URL=http://127.0.0.1:8001/v1
APP_DECISION_PROVIDER_API_KEY=REPLACE_WITH_YOUR_VLLM_API_KEY
APP_DECISION_PROVIDER_MODEL=qwen36-crowd
APP_DECISION_PROVIDER_TEMPERATURE=0
```

- `BASE_URL` 以 `/v1` 结尾，不要包含 `/chat/completions`；后端会自动追加路径。
- 后端要求 API key 非空，本地模型也一样。与 `VLLM_API_KEY` 对应的值保持一致即可，不需要 OpenAI 官方密钥。
- `APP_DECISION_PROVIDER_MODEL` 非空时覆盖 Unity `ExperimentManager.llmModel`；模型名最终必须与 `/v1/models` 中的标识一致。
- 操作系统已有环境变量优先于 `.env.local`。若改了文件仍访问旧服务，检查终端或进程已有变量。
- 清除不需要的 `APP_DECISION_PROVIDER_2_*`、`APP_DECISION_PROVIDER_3_*` 等配置；额外 provider 的 API key 非空时会参与轮询，可能把同一次实验分发到不同模型服务。
- 重启 API 进程使新设置生效；环境文件仅在进程启动时加载，设置还会被缓存。

网络地址从“后端进程所在环境”判断：Unity 在电脑 A、FastAPI 和 vLLM 都在服务器 B 时，只有 Unity 的后端地址需要指向 B，FastAPI 仍可用 `127.0.0.1:8001`。如果 FastAPI 与 vLLM 分别位于不同机器或容器，`127.0.0.1` 不再通向另一端，需配置实际可达地址；容器名称/隧道与访问控制也应一起配置。

Windows PowerShell 从仓库根目录启动：

```powershell
$env:APP_ENV='local'
.venv\Scripts\python.exe -m backend
```

Linux/macOS：

```bash
APP_ENV=local .venv/bin/python -m backend
```

worker 和数据库初始化步骤仍按 README 执行。

## 4. 配置 Unity

在 `Assets/Application/Scenes/App.unity` 中找到 `ExperimentManager` 和 `DecisionBatchScheduler`：

1. `ExperimentManager.backendOptions.baseUrl` 指向 FastAPI，例如 `http://127.0.0.1:8987`，不要指向 vLLM 的 8001 端口。
2. `ExperimentManager.llmModel` 设为 `qwen36-crowd`。即使后端设置了模型覆盖，也应保持实验记录中的模型名一致；另行保存别名对应的真实权重标识。
3. 要让各批次送往后端，选择 `DecisionBatchScheduler.dispatchMode=LlmOnly`。首次联调从少量人物、单并发开始。
4. 检查当前地图 `TopologySceneConfig.FixedPathConfigs`、人物覆写 `FixedPathConfigSO.mode` 和自动分配配置。固定/随机覆写即使在 LlmOnly 下也可能先应用或改变算法推荐；纯 LLM 条件下应禁用这些覆写并记录设置。
5. 明确 `fallbackToAlgorithm`。允许 fallback 的实验需报告成功/回退占比；关闭 fallback 时，失败请求不会神奇变成成功的模型决策。
6. 超时需要同时检查 Unity HTTP 超时、`llmTimeoutMs` 和后端 `APP_DECISION_BATCH_HARD_TIMEOUT_SECONDS`。默认单人约 5 秒、后端整批 8 秒可能不足以让大模型完成；按实测设置，勿为了避免回退而修改参数后仍称其为原条件。

新建实验再执行；已经写入后端的实验配置不会因为修改 Inspector 自动成为新值。

## 5. 如何确认确实调用了本地模型

同时保留以下证据：

- 后端进程实际使用 `openai_compatible`，请求日志中的 URL、model 对应本地 vLLM。
- vLLM 服务端收到 `/v1/chat/completions`，其启动命令能对应到模型权重与修订。
- 决策 transcript/数据库中每个事件的来源和 fallback/none 比例。

不能只看 UI 模式名、轨迹分组名或 `decision_source=llm`。当前后端 heuristic 也复用统一成功响应路径，服务层可能仍标记来源为 llm；所以必须联合引擎模式与真实请求日志判断。历史离线轨迹标准化还会按目录覆盖标签，详见 [数据说明](DATA.md)。

## 已有配置字段的实际边界

当前 `llm_engine.py` 的 HTTP body 显式发送 model、后端 temperature、messages 和 response_format。Unity 的 `llmTemperature` 虽写入实验配置，但当前调用使用 `APP_DECISION_PROVIDER_TEMPERATURE`；`llmMaxTokens` 没有转发为 HTTP 请求的 `max_tokens`。不能只看 Inspector 就宣称这两个值已控制实际推理。需要固定输出上限时应在模型服务明确设置支持的默认生成参数，或另行实现并验证请求透传。

## 原始实验与接入示例

上述配置、单并发、服务别名和命令用于说明如何接通当前公开实现，不是原六次实验的参数重建。本机没有运行 Qwen 权重或验证 GPU 推理，本次只核对了配置与源码路径。

论文至少应补充：权重仓库与 revision、量化格式、vLLM/依赖版本、GPU 型号/数量/显存、完整启动命令、思考模式、实际采样参数、并发和超时、Unity 场景/种子/人数/派发/覆写/fallback 设置。

## 官方参考

- [Qwen3.6-35B-A3B 模型卡](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [Qwen3.6-35B-A3B-FP8 模型卡](https://huggingface.co/Qwen/Qwen3.6-35B-A3B-FP8)：仅在实际使用 FP8 时引用为所用权重。
- [vLLM OpenAI-compatible 接口](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM serve 参数](https://docs.vllm.ai/en/latest/cli/serve/)
