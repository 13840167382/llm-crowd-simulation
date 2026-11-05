# 论文中的代码公开、引用与方法描述

## 放在论文哪里

方法章节“系统实现/实验环境”中描述 Unity、后端及本地推理服务的分工；文末单列“代码与数据可用性”（或使用目标期刊的 Code Availability 栏目），给出仓库和固定公开版本。参考文献格式遵循投稿期刊模板。

## 可以改写使用的代码可用性声明

> 本研究相关实现已在 GitHub 公开，地址为 https://github.com/13840167382/llm-crowd-simulation 。公开内容包括 Unity 仿真业务代码与场景配置、Python/FastAPI 后端以及离线轨迹分析脚本，项目原创源码采用 MIT 许可证发布。公开实现版本为 v0.1.0；第三方 UI 资源及模型权重需按各自许可另行获取。该公开版本不包含原始实验的全部运行时参数快照，不能据此保证逐次重建论文原六次仿真运行。

此段只适用于对应 Release 已发布后的描述。v0.1.0 指公开实现快照，并非经过记录证明的原六次实验精确版本。若投稿前补齐并验证了参数/数据，应相应更新声明及版本；不要宣称“全部数据已开源”或“完整一键复现”而没有对应文件。

仓库不含原始 SDD 视频、标注及大体积实验导出。数据可用性需单独说明原始数据获取地址，以及仿真 CSV/TXT 实际存放的位置。不能以代码仓库链接替代未发布的数据链接。

## 方法章节的模型描述

按维护者当前确认的信息，可写为：

> 高层目的地决策由通过 vLLM 部署的 Qwen3.6-35B-A3B 模型提供。Unity 客户端将人物状态、候选出口及算法推荐提交至 Python/FastAPI 后端，后端通过兼容 Chat Completions 的接口调用模型，对返回的目的地进行合法性检查，再交由 Unity NavMesh 完成连续寻路与运动更新。

请按真实记录补充：模型权重 revision、精度/量化、vLLM 版本、GPU、思考模式、实际推理参数、批处理/超时和回退策略。如果未核实 FP8，先不要把模型名扩写为 FP8；原论文若写 Java 后端，需与实际实验版本核对，当前公开包是 Python/FastAPI。

若允许算法回退，方法章节和统计表应明确算法回退条件与比例，不宜把所有事件描述成成功的大模型决策。源码中的 `LocalOnly` / `APP_DECISION_ENGINE_MODE=local` 指规则算法，详见 [本地模型指南](LOCAL_LLM.md)。

## 固定公开版本

- 开发地址：https://github.com/13840167382/llm-crowd-simulation
- v0.1.0：https://github.com/13840167382/llm-crowd-simulation/releases/tag/v0.1.0
- 论文引用应记录实际使用的 commit SHA；不要只给持续变化的 main 分支。

正文写“公开实现版本”即可；只有在完整运行配置、数据和实验日志可与该 commit 对应时，才写“全部实验由此版本产生”。论文匿名审稿时按会议/期刊要求处理作者身份和仓库链接。

## 参考文献条目模板

以下“软件作者”需改为实际软件署名与顺序，不能把账户名、项目维护者或论文作者未经核对自动当成同一组作者。访问日期按投稿时实际访问日期填写。

```text
软件作者. LLM Crowd Simulation: v0.1.0 [软件/在线资源].
2026 [实际访问日期].
https://github.com/13840167382/llm-crowd-simulation/releases/tag/v0.1.0.
```

BibTeX 示例（使用 software 条目需参考文献工具支持；传统模板可改用 misc）：

```bibtex
@software{llm_crowd_simulation_2026,
  author  = {REPLACE WITH ACTUAL SOFTWARE AUTHORS},
  title   = {LLM Crowd Simulation},
  version = {0.1.0},
  year    = {2026},
  url     = {https://github.com/13840167382/llm-crowd-simulation/releases/tag/v0.1.0}
}
```

本仓库目前没有 DOI，也没有核实软件作者名单，因此不生成含虚构姓名、ORCID 或 DOI 的正式 CITATION.cff。待署名确定后可添加该文件，GitHub 会显示 Cite this repository。若期刊要求持久标识，可通过 Zenodo 将 GitHub Release 存档生成 DOI，再把真实 DOI 写入论文和引用文件；本次没有代为注册或创建 Zenodo 记录。

官方说明：[GitHub 引用文件](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files)、[GitHub/Zenodo DOI](https://docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content)。
