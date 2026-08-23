# GitHub 建仓填写建议

本地仓库目录已经包含 README、`.gitignore` 和 Apache-2.0 `LICENSE`。因此 GitHub 新建仓库页面应创建一个**空远程仓库**，避免 GitHub 自动产生首个提交后与本地历史冲突。

| 字段 | 推荐填写 |
|---|---|
| Owner | `LYCMYT` |
| Repository name | `reference-video-rebuilder` |
| Description | `一个注重授权与合规的 Codex Skill，可将参考视频重建为干净、可复用的模板，并替换模特、服装、商品、背景、文字和音频。` |
| Visibility | 开发阶段选 `Private`；通过公开发布清单并打当前 Alpha 标签后再改 `Public`（当前目标为 `v0.3.0-alpha`） |
| Add README | `Off` |
| Add .gitignore | `No .gitignore` |
| Add license | `No license` |

这里选择 `No license` 不是项目不授权，而是因为本地已经准备了 Apache-2.0 `LICENSE`。推送后 GitHub 会自动识别许可证。

## 开源策略

- 如果确定允许他人使用、修改和商用自研代码：保留 Apache-2.0。
- 如果还没有决定是否允许他人商用：GitHub 先建为 Private，不要先公开再撤回。开源许可证一旦对某个公开版本生效，不能撤销他人已经取得的那一版本的许可。
- 第三方模型和工具不随本仓库许可证自动变成 Apache-2.0；必须遵守 `THIRD_PARTY.md`。
- 改为 Public 前必须清除 Git 历史中的真人素材、原视频、音乐、EXIF、绝对私人路径和密钥；配置私密安全报告渠道；固定运行时依赖版本并通过许可证与 CI 审核。
- 如果仓库已经设为 Public，应启用 GitHub Private Vulnerability Reporting，并在每个公开 Alpha 前重新执行敏感信息、依赖许可证和 CI 审计。

## 推荐 Topics

创建仓库后可添加：

`codex`、`codex-skill`、`video-editing`、`remotion`、`ffmpeg`、`reference-video`、`video-remix`、`generative-ai`、`virtual-try-on`、`short-video`

## 第一次推送

确认 GitHub 创建的是空仓库后，在本地仓库根目录初始化 Git 并连接远程。远程 URL 应以 GitHub 页面实际给出的地址为准，不要在文档中硬编码访问令牌。
