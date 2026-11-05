# 发布验证记录

日期：2026-11-05。此记录针对源码准备阶段；GitHub Actions 的执行状态以仓库中的实际结果为准。

## 已完成

- 从完整工程包提取业务源码、Unity 资源、配置和文档，保留 `.meta` GUID。
- 排除原 `.git` 历史、私有 `.env.local`、Library、Logs、UserSettings、虚拟环境及缓存。
- Modern UI Pack 本地副本保留，但不加入 Git；依赖说明见第三方清单。
- 扫描公开文件中的常见令牌/私钥模式，并与原私有配置中的密钥候选比对；未发现真实密钥进入公开文件（Redis key 前缀属于非凭证字符串）。
- 153 个 Python 文件语法解析成功。
- 修正 setuptools 包发现目录，保证 `pip install -e backend` 包含 backend 包；补上 wheel 中的分析网页 HTML/CSS 和迁移模板。
- 后端 editable 安装及 wheel 构建成功；检查 wheel 中包含 `backend/__init__.py` 与网页静态资源。
- HTTP TestClient 检查 `/healthz`、`/api/v1/docs`、`/ui/analytics`、`/ui/analytics.css` 均返回 200。
- 后端决策组件、坐标精度和日志测试合计 10 项通过；日志测试中原先硬编码的 E 盘目录改为 pytest 临时目录。
- 离线分析现有 5 项测试通过。
- 已加入 GitHub Actions 配置，用干净的 Python 3.12 环境安装依赖并运行上述组件与离线测试；本地验证使用了可访问系统已装依赖的虚拟环境，不是全新空环境验证。

## 尚未验证

- Unity 编辑器导入、Play Mode、场景完整性和打包：本机未安装对应 Unity 编辑器。
- Modern UI Pack 其他版本能否保持 GUID/场景引用兼容。
- PostgreSQL/TimescaleDB 迁移、真实 Redis/RQ worker、端到端 Unity 联调。
- 真实模型调用与论文六次实验重跑。

`backend/tests/unit` 中有部分测试依赖真实数据库，不能直接把整个目录全部作为离线 CI。本次 CI 仅选择不依赖数据库和外部模型的组件测试。

此前在原始数据目录完成过数值复现：5 份 CSV 表与 11 份 NPY 矩阵与历史输出一致。该结论只说明现有分析流程可重复计算，不表示原始仿真或数据分组已经经过独立验证。
