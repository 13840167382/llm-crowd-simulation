# 第三方组件与资源

本项目的 MIT 许可不覆盖第三方素材、字体或数据。

## Modern UI Pack（需自行安装）

- 作者：Michsky。
- 来源：https://marketplace.unity.com/packages/tools/gui/modern-ui-pack-201717
- 许可：https://unity.com/legal/as-terms
- 原始工程路径：`Assets/ThirdParty/Modern UI Pack/`。
- 本仓库没有分发该目录及其 `.meta`。本机整理目录可能保留原压缩包中的本地副本，但 Git 明确忽略它。

App/Test 场景和部分 UI 预制体引用此资源。请取得相应许可，从 Asset Store 正常导入，并保留供应方 `.meta` GUID。可以在 Unity 编辑器内把文件夹移动到上述路径；移动时保留 `.meta`。原包没有可靠的版本号记录，不同版本可能需要重新绑定引用。请勿用缺失引用状态下的场景覆盖原场景。

本仓库不提供绕过此依赖的替代 UI，也未验证升级版本的兼容性。

## UniTask 2.5.5

来源：https://github.com/Cysharp/UniTask/tree/2.5.5

版权：Cysharp, Inc.；MIT。随工程保留的代码位于 `Assets/ThirdParty/UniTask`，许可文本见 `third_party_licenses/UniTask-LICENSE.txt`。

## TextMesh Pro / Unity 包

Unity 包版本见 `Packages/manifest.json`、`Packages/packages-lock.json`，由 Unity Package Manager 获取。`Assets/ThirdParty/TextMesh Pro` 含项目使用的字体、字形资产及 TMP 资源，Unity/TMP 部分仍受其原许可约束，见 `third_party_licenses/TextMeshPro-LICENSE.md`。

Liberation Sans、Noto Sans、Noto Sans SC 字体的 SIL Open Font License 文本保留在原字体目录中。字体及生成的字形资源不因项目 MIT 许可而改变原许可。

## Stanford Drone Dataset

SDD 原始视频、标注及底图不随源码仓库分发。请从项目官方页面获取并遵循数据提供方的使用条件：https://cvgl.stanford.edu/projects/uav_data/

本项目提供读取标注与计算指标的脚本；这不授予原始数据的再分发权。
