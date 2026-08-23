# Codex Reference Video Rebuilder 完整设计方案

版本：0.6.0-alpha（在 0.5.0-alpha 基础上的增量）
日期：2026-08-24
目标仓库：`LYCMYT/reference-video-rebuilder`
Skill 名称：`reference-video-rebuilder`

当前实现状态：本地 Alpha 已具备 FFmpeg/ffprobe 媒体探测、受限参考调查、Compiler Plan/Template IR/资产合同校验、固定 S1 确定性合成和逐输出技术 QA。0.4.0-alpha 新增已授权、本地 fixed-subject-carousel S1 的 propose -> review -> freeze-plan -> compile -> render 工作流：propose 只产生严格 0.4.0 Proposal 与待审工件，Review 显式绑定 Proposal 哈希，freeze-plan 才生成既有编译器消费的规范 Compiler Plan。Frozen Compiler Plan schema 保持 0.3.0 以兼容 v0.3，编译输出的 Template IR schema 仍为 0.2.0。

0.5.0-alpha 在不改变上述 v0.4 计划冻结路径的前提下，增加严格的本地
asset-pack 路径：对已验证 Template IR 执行 propose-assets，人工审核每个
映射，再以 freeze-assets 发布可渲染的本地冻结资产。

0.6.0-alpha 在不内置生成模型的前提下增加“外部生成资产桥”：
prepare-generation 生成待审计划，外部 Codex ImageGen/用户自有本地 CUDA
工作流仅以 file-drop 方式把结果写入新 result pack，随后执行逐槽 result
review 和 assemble-generation-pack，最后仍必须进入 v0.5 的
propose-assets -> review -> freeze-assets。CLI 不运行模型、任意 shell、CUDA
任务、网络请求、上传或权重下载；它只记录受限执行声明并处理本地文件。

> **当前可执行边界（而非未来路线图）**：没有 OCR、没有任意视频的语义理解或自动 family 发现、没有 CLI 内置的云端执行或素材/换装资产生成。propose 不能猜测身份、服装、商品、文字、平台 UI、水印或隐藏像素；它只能提出有界 S1 候选，且 Proposal 永远 review_required=true。v0.6 可记录 `local-file-drop` 或 `controller-managed` 的外部执行声明；后者可标记为 `local-only` 或经双重显式确认的 `controller-cloud`，但 CLI 从不上传。本文后文出现的 OCR、检测、生成模型或 S2/S3 内容均为历史设计或未来设想，不能解释为当前 CLI 能力。

## 目录

1. [执行摘要](#1-执行摘要)
2. [产品目标](#2-产品目标)
3. [非目标与承诺边界](#3-非目标与承诺边界)
4. [两种工作模式](#4-两种工作模式)
5. [支持等级](#5-支持等级)
6. [总体架构](#6-总体架构)
7. [核心数据模型](#7-核心数据模型)
8. [完整工作流](#8-完整工作流)
9. [模块设计](#9-模块设计)
10. [命令行和工具接口](#10-命令行和工具接口)
11. [目录与项目状态](#11-目录与项目状态)
12. [渲染策略](#12-渲染策略)
13. [质量验收](#13-质量验收)
14. [失败、降级和人工关卡](#14-失败降级和人工关卡)
15. [隐私、安全与权利](#15-隐私安全与权利)
16. [开源项目借鉴边界](#16-开源项目借鉴边界)
17. [本机能力与部署配置](#17-本机能力与部署配置)
18. [测试与评测体系](#18-测试与评测体系)
19. [开发路线图](#19-开发路线图)
20. [模型路由、质量治理与 Token 预算](#20-模型路由质量治理与-token-预算)
21. [发布标准](#21-发布标准)
22. [已知未知与未知的未知](#22-已知未知与未知的未知)
23. [当前视频的首个金标准](#23-当前视频的首个金标准)
24. [最终决策](#24-最终决策)
25. [v0.5 严格本地资产包增量](#25-v05-严格本地资产包增量)
26. [v0.6 外部生成资产桥](#26-v06-外部生成资产桥)

## 1. 执行摘要

本项目要实现的不是单一视频生成模型，而是一个由 Codex 驱动的**参考视频编译与重建工作流**。

用户提供一条参考视频，系统提取其中的形式信息：画幅、时长、场景、镜头、构图、图层、位置、动作、切点、节奏、转场、滚动轨迹和音频结构。系统把模特、服装、商品、背景、文字、Logo、道具和音乐抽象成可替换槽位，把平台 UI、弹幕、水印、账号信息等标记为删除层。用户再提供自己的素材，系统生成或整理中间资产，最后通过确定性渲染重建视频。

关键原则：

- 参考视频是一份“形式和时间轴规范”，不是要复制的像素文件。
- Skill 是控制平面；脚本、模型、MCP 和渲染器是执行平面。
- 首次处理新参考视频叫“编译模板”；审核后反复换素材叫“复用模板”。
- 任意视频不能无条件全自动；必须先判断支持等级并允许人工修正。
- 平台元素通过重建排除，而不是承诺恢复其遮挡下的真实原像素。
- 高成本生成放在预览审批之后，确定性合成尽量留在本机。

## 2. 产品目标

### 2.1 用户输入

- 一条本地参考视频；
- 一个或多个模特身份参考；
- 服装、商品、道具、背景、Logo 和文字素材；
- 原音乐保留或用户上传的替换音源；
- 删除清单和替换清单；
- 输出画幅、分辨率、编码和隐私模式；
- 对参考视频、肖像、品牌和音频拥有处理权限的确认。

### 2.2 系统输出

- 可审核的参考视频分析报告；
- Template IR；
- 槽位和替换素材映射；
- 生成后的标准化中间资产；
- 带槽位编号的调试预览；
- 低清正式预览；
- 指定分辨率的最终视频；
- QA 报告；
- 可重复运行的项目目录和运行清单。

### 2.3 “其他不变”的工程定义

允许重建时，“其他不变”表示尽量保持以下结构约束，而不是保持原像素：

- 画幅、时长、帧率；
- 镜头顺序与时间范围；
- 构图、视觉重心、主体相对比例；
- 动作轨迹和镜头运动；
- 转场类型、缓动、停留时间；
- 商品滚轮和卡片的运动路径；
- 音乐节拍与换装点；
- 背景的风格、明暗和空间感觉。

生成式替换的脸、服装、头发、手指、反射、阴影和遮挡不能承诺逐像素一致。

## 3. 非目标与承诺边界

第一阶段不承诺：

- 对任意视频进行无条件、一键、像素级复刻；
- 从平台 UI 或弹幕完全遮挡的区域恢复真实原画面；
- 对快速舞蹈、多人交互、复杂反射和透明材质实现商品级逐帧服装准确；
- 在未经确认时上传用户的人脸、服装或商业素材到外部服务；
- 复制平台水印、账号信息、受保护 UI 或未授权品牌内容；
- 将第三方开源模型的许可证自动转化为本项目许可证；
- 在没有适合 GPU 的机器上提供高质量本地视频扩散速度保证。

## 4. 两种工作模式

### 4.1 Propose -> Review -> Freeze -> Compile：新参考视频编译模式

当前实现只适用于已授权、本地 fixed-subject-carousel S1 的第一次编译。流程为：确认权利与本地边界 → propose → 显式哈希绑定 Review → freeze-plan → validate-compiler-plan → compile → Template IR 0.2.0 → 既有审核与渲染。

propose 自动提出 source_rect、顶部 carousel 边界、subject 区域、slot_count、切换时序、按比例的 carousel 布局和背景色，并生成 overview contact sheet、geometry preview、timing profile、严格 0.4.0 Proposal JSON 与 pending review template。source_rect 只是匹配受支持 9:16 输出比例的最大居中源裁剪；仅当源本身已匹配时才使用完整帧。它是构图启发式，不是 platform chrome 或 UI 的语义检测/移除结果。chrome、非居中内容、非均匀裁剪、语义不明或时间模糊时，审核者必须修正。

Proposal 永远 review_required=true，不能直接编译。审核者必须确认 family、geometry、slot_count、timing、carousel、background、audio 和 authorization，并可更正 approved_plan。freeze-plan 校验精确 Proposal 哈希、所有确认与 approved_plan 后，才发布 schema 0.3.0 的 Frozen Compiler Plan。不得通过 OCR、自动批准或任意语义推断填补缺失事实。

Compile 模式的产物必须是可重复使用的模板，而不是一次性脚本；既有确定性 compiler、render 和 QA 合同不因 0.4 Proposal 阶段而改变。

### 4.2 Remix：已审核模板复用模式

适用于已经冻结的 Template IR。

当前流程：已提供 render-ready 素材走 v0.5 的显式映射/冻结；尚未 render-ready
的静态素材可先走 v0.6 的 Generation Request -> Plan Review -> 外部 file-drop/
controller -> Result Review -> media-only assembly，再进入 v0.5 冻结。CLI 内的
素材生成、云端 adapter、CUDA、shell 和上传仍不是当前路径。

Remix 模式的验收要求是：更换第二组完整素材时不修改程序，只修改资产和映射文件即可生成。

## 5. 支持等级

| 等级 | 视频特征 | 自动化承诺 | 失败策略 |
|---|---|---|---|
| S1 确定性模板 | 单主体、固定镜头、简单背景、规律硬切、2D 叠加、轻微动作 | 当前仅限已授权的 fixed-subject-carousel；propose 可给出有界几何、slot_count 和时序候选 | Proposal 永远需要人工/Codex Review；不自动发现任意 family 或推断语义槽位 |
| S2 跟踪合成 | 单主体中等运动、缓慢运镜、可跟踪遮挡、动态背景 | 中；结构和运动可保持，需动态蒙版 | 请求修正关键帧、轨迹或蒙版 |
| S3 生成式修改 | 快速运动、转身、复杂衣服动态、强运镜、较大遮挡 | 低到中；只保证整体效果和节奏相似 | 分段生成、局部重试、明确实验性 |
| S4 不支持精确模式 | 多人紧密交互、镜面、透明物、严重遮挡、极快混剪、输入损坏 | 不承诺 | 输出分析报告并建议拆分、简化或人工模板 |

分类必须保守：错误地拒绝精确模式优于静默输出错误商品、错误人物或残留平台标识。

## 6. 总体架构

本节描述目标架构；图中的 OCR、检测、生成 adapter、云端和高级 QA
组件不是 0.6.0-alpha 的已实现能力。当前可执行路径为本地、已授权的
fixed-subject-carousel S1 的 propose -> review -> freeze-plan -> compile，
以及已验证 Template IR 的 v0.6 外部静态资产桥（不运行外部生成器）和
v0.5 propose-assets -> 人工审核 -> freeze-assets -> 确定性渲染。

```text
┌─────────────────────────────────────────────────────────────┐
│ Codex Skill：理解请求、选择模式、执行关卡、解释错误          │
└──────────────────────────┬──────────────────────────────────┘
                           │ structured CLI / optional MCP
┌──────────────────────────▼──────────────────────────────────┐
│ Project Controller：状态机、缓存、任务、运行清单、恢复        │
└───────────┬──────────────────┬──────────────────┬───────────┘
            │                  │                  │
┌───────────▼──────────┐ ┌─────▼────────────┐ ┌──▼──────────────┐
│ Reference Analyzer   │ │ Asset Processors │ │ Render Compiler │
│ probe/scene/OCR/beat │ │ image/VTON/mask  │ │ IR -> timeline  │
│ detect/track/camera  │ │ product/bg/audio │ │ Remotion/FFmpeg │
└───────────┬──────────┘ └─────┬────────────┘ └──┬──────────────┘
            │                  │                  │
            └──────────────────┼──────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │ QA & Review Artifacts│
                    │ metrics/contact sheet│
                    │ OCR/residual/flicker │
                    └──────────────────────┘
```

### 6.1 控制平面

Codex Skill 只负责：

- 读取用户目标和项目状态；
- 选择 Compile 或 Remix；
- 调用稳定 CLI 或 MCP；
- 读取小型 JSON、关键帧和异常报告；
- 在规定关卡请求确认；
- 根据错误代码选择重试、降级或停止；
- 交付最终结果。

Codex 不应逐帧读取视频，也不应每次临时拼接复杂 FFmpeg 命令。

### 6.2 执行平面

- Python：媒体分析、图像处理、状态和验证；
- FFmpeg/ffprobe：解码、编码、音频、抽帧、合成；
- Remotion：复杂 2D 时间轴、动画、预览和确定性渲染；
- 可选分析模型：检测、OCR、分割、跟踪、姿态和光流；
- 可替换生成 adapter：ImageGen、虚拟试衣、图像修复、视频修改；
- 可选本地 MCP：长任务、队列、进度、取消和模型驻留。

## 7. 核心数据模型

### 7.0 Proposal、Review 与 Frozen Compiler Plan

0.4 的三种计划文档不可混用：

| 文档 | 版本 | 作用 |
|---|---|---|
| Proposal JSON | 0.4.0 | 严格、有界、待审核的候选；review_required 固定为 true |
| Review decision | 0.4.0 | 显式绑定一份 Proposal SHA-256 的审核决定 |
| Frozen Compiler Plan | 0.3.0 | 既有 compiler 的规范输入，保持 v0.3 兼容 |

Proposal 需包含一个候选计划、受限候选/置信度、受限本地证据引用和安全技术
source fingerprint。允许的 fingerprint 仅为 SHA-256、width、height、精确
frame_count、fps 和 has_audio；不得包含源文件名或绝对路径、工具绝对路径、
容器 tags、title、artist、comments、账号身份、原始 probe 或原始媒体。

Review 的 approved_plan 可以由审核者修正，但批准时必须同时满足：
Proposal 哈希完全匹配、decision 为 approved、reviewer_confirmed 为 true，
且 family、geometry、slot_count、timing、carousel、background、audio、
authorization 八项确认均为 true。freeze-plan 只从通过这些条件的
approved_plan 规范化生成 0.3.0 Frozen Compiler Plan；Proposal 的候选、
置信度、证据和 source fingerprint 不进入冻结计划。

### 7.1 Template IR

Template IR 是系统可扩展性的核心，也是 renderer 的冻结执行合同。`slots` 只描述用户输入或生成输入，`layers` 才描述实际进入画面的渲染实例；这样可以阻止 renderer 把服装平铺图直接贴到人物层上。所有时间范围采用半开区间 `[start_frame, end_frame)`，所有坐标都以 clean canvas 左上角为原点，排序固定为 `(track.z_index, layer.z_offset, layer.id)`。

下面只展示代表性字段；机器可执行真相以仓库内 Draft 2020-12 Schema 和完整示例为准：

```json
{
  "schema_version": "0.2.0",
  "template_id": "outfit-reel-001",
  "coordinate_space": "canvas-pixels",
  "canvas": {
    "width": 576,
    "height": 1024,
    "background": "#FFFFFF",
    "source_rect": {"x": 0, "y": 128, "width": 576, "height": 1024}
  },
  "source": {
    "duration_frames": 347,
    "fps": 30,
    "width": 576,
    "height": 1280,
    "source_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "support": {"level": "S1", "confidence": 0.96, "review_required": false, "warnings": []},
  "tracks": [
    {"id": "model", "type": "subject", "z_index": 10, "overlap_policy": "forbid"}
  ],
  "slots": [
    {"id": "model.identity", "type": "identity", "required": true, "accepted_media": ["image/jpeg", "image/png", "image/webp"]},
    {"id": "outfit.01", "type": "garment", "required": true, "accepted_media": ["image/png", "image/webp"]}
  ],
  "layers": [
    {
      "id": "look.01",
      "track_id": "model",
      "source": {"slot_id": "outfit.01", "representation": "render-ready"},
      "active_ranges": [{"start_frame": 0, "end_frame": 29}],
      "layout": {
        "box": {"x": 0, "y": 200, "width": 576, "height": 824},
        "fit": "contain",
        "object_position": {"x": 0.5, "y": 0.5}
      },
      "transform": {
        "anchor": {"x": 0.5, "y": 0.5},
        "keyframes": [{
          "frame": 0,
          "translate_x": 0,
          "translate_y": 0,
          "scale_x": 1,
          "scale_y": 1,
          "rotation_deg": 0,
          "opacity": 1,
          "easing": {"type": "hold"}
        }]
      },
      "mask": null,
      "blend": {"mode": "normal", "opacity": 1},
      "z_offset": 0
    }
  ],
  "remove_layers": [
    {
      "id": "mobile-chrome",
      "policy": "crop-source-before-analysis",
      "regions": [{
        "active_range": {
          "start_frame": 0,
          "end_frame": 347
        },
        "operation": "keep",
        "geometry": {
          "type": "rect",
          "space": "source",
          "rect": {"x": 0, "y": 128, "width": 576, "height": 1024}
        }
      }]
    }
  ],
  "events": [
    {
      "id": "switch.outfit.01",
      "frame": 0,
      "type": "slot-switch",
      "track_id": "model",
      "slot_id": "outfit.01",
      "transition": {"type": "cut", "duration_frames": 0}
    }
  ],
  "audio": {
    "slot_id": "audio",
    "timeline_start_frame": 0,
    "timeline_end_frame": 347,
    "source_in_ms": 0,
    "source_out_ms": 11567,
    "playback_rate": 1,
    "loop": false,
    "gain_db": 0,
    "fade_in_frames": 0,
    "fade_out_frames": 0
  },
  "outputs": [
    {
      "id": "preview-720p",
      "width": 720,
      "height": 1280,
      "codec": "h264",
      "pixel_format": "yuv420p",
      "audio_codec": "aac",
      "filename": "preview-720p.mp4",
      "reframe": {
        "mode": "stretch",
        "object_position": {"x": 0.5, "y": 0.5},
        "background": "#FFFFFF"
      }
    }
  ]
}
```

冻结前必须把占位 source hash 换成真实 SHA-256。12套服装分别形成12个 `render-ready` layer，顶部商品滚轮用 carousel track 的项目顺序、viewport 和 group transform 表示，音频使用独立时序对象，删除层必须带有可验证的空间和帧范围。`events` 用于编辑与QA，最终 renderer 只以规范化后的 layers/track transforms 为画面真相，避免同一运动存在两套相互冲突的数据。

### 7.2 Asset Manifest

每个输入素材必须记录：

- 槽位 ID；
- 本地路径和 SHA-256；
- 媒体类型、尺寸、色彩空间和时长；
- 来源和权利确认；
- 是否允许云端上传；
- 预处理器；
- 生成 adapter、版本、提示词摘要、种子；
- 派生资产路径；
- QA 状态和人工批准人/时间。

### 7.3 Run Manifest

每次运行必须记录：

- 项目和模板版本；
- Git commit；
- Python、FFmpeg、Node、Remotion 版本；
- 模型、checkpoint、provider 和参数；
- 输入与输出哈希；
- 每个阶段的开始、结束、缓存命中和错误；
- 最终 QA 指标；
- 是否包含需要人工接受的警告。

### 7.4 置信度

0.4 仅允许把下列字段作为 Proposal 中的有界候选，而不是自动确定的语义槽位：

- `confidence`：0 到 1；
- `evidence`：关键帧、框、蒙版或检测来源；
- `review_required`；
- `reason`；
- `allowed_processors`。

无论置信度高低，Proposal 都不得静默进入正式渲染或冻结。它必须保持 review_required=true；由审核者确认或修正 geometry、slot_count、timing、carousel、background、audio 和 authorization 后，freeze-plan 才可继续。

## 8. 完整工作流

本节记录完整产品流程。0.4.0-alpha 的新参考路径以 propose -> review
-> freeze-plan -> compile 为准；v0.5 增加严格 asset-pack 冻结；v0.6 在两者
之间增加外部静态资产的计划、审核和无 metadata 组装桥。下面涉及 OCR、任意
视频分类、语义槽位推断、CLI 内模型执行或自动云端 adapter 的历史设计不得
解释为当前 CLI 能力。

### 8.1 Preflight

1. 检查 FFmpeg、ffprobe、可写 project-root 和输入可读性。
2. 只接受精确 CFR、零旋转且时长不超过 60 秒的本地源。
3. 确认参考、肖像、商品、品牌和音频权限；若保留音频，单独确认音频权利。
4. 对 v0.6 外部执行声明确定 `local-file-drop` 或 `controller-managed`，并
   记录 `local-only` 或 `controller-cloud`；后者必须在 Request 和 Plan Review
   中都设置 `cloud_upload_confirmed=true`，但 CLI 不上传任何文件。
5. 建立项目隔离目录。源与证据留在本地，工件路径不得逃逸 project-root。

### 8.2 Propose

调用 propose。它输出严格 0.4.0 Proposal、pending review template、overview
contact sheet、geometry preview 与 timing profile，并提出以下受限候选：

1. 匹配受支持 9:16 输出比例的最大居中 source_rect；仅源本身已匹配时使用完整帧；
2. 顶部 carousel 边界和 subject 区域；
3. slot_count、切换帧/时序和按比例表达的 carousel 布局；
4. 背景色以及选定的音频/授权事实。

source_rect 是构图启发式，不是 OCR、platform chrome 检测、语义 UI 分割或
覆盖层移除。它无法判断人物、服装、商品、文字、水印或被遮挡的内容。候选
和证据必须有数量/尺寸边界；Proposal 只可带安全技术 source fingerprint
与项目相对工件引用，不可带源/工具绝对路径、文件名、容器 tags、账号身份、
raw probe 或原始证据。

### 8.3 Review

审核 Proposal，而不是让系统自动分类任意视频。审核者应查看 contact sheet、
geometry preview 和 timing profile，明确确认 family、geometry、slot_count、
timing、carousel、background、audio 和 authorization。每份 Proposal 的
review_required 固定为 true；置信度不是批准。审核者可在 approved_plan 中
纠正裁剪、边界、时序和其他计划值，尤其在 chrome、非居中内容、非均匀裁剪
或模糊切换使候选不可靠时。

### 8.4 Freeze

调用 validate-proposal 和 validate-review 后，调用 freeze-plan。它必须校验
Proposal 的精确 SHA-256 绑定、approved Review、reviewer_confirmed、全部八项
确认、approved_plan、权利和本地路径/工件边界。任一失败返回 exit 2，且冻结
输出不得写入部分文件。成功时只生成 schema 0.3.0 的 Frozen Compiler Plan。
freeze-plan 的 Proposal 与 Review 参数必须是相对 project-root 的规范路径；
绝对本地路径、盘符根路径和 UNC 路径会在检查候选文件前拒绝。该限制只适用于
冻结；独立的 validate-proposal 与 validate-review 仍可检查用户指定的文件。

### 8.5 Compile

对 Frozen Compiler Plan 运行 validate-compiler-plan 与既有 compile。compile
仍在最终可见输出目录创建前完成 schema 和媒体相关语义预检，并输出 Template
IR 0.2.0、审核工件和既有 exit 0/1/2 结果。Proposal 阶段不会改变 deterministic
compiler 或 Template IR 合同。

### 8.6 Validate replacement assets

- 对已存在 render-ready 素材，使用 v0.5 的 exact-stem asset-pack 提议、人工
  Review 与 freeze-assets；
- 检查必需槽位、数量、编号、映射重复、媒体类型和可渲染性；
- 自动探测只证明技术兼容性，不判断服装图是否真的属于某件服装、是否保持同一
  模特，或 Logo/文字是否正确；
- 不默认生成笛卡尔积，所有映射必须显式。

### 8.7 Prepare derived assets

对于尚未 render-ready 的模特、服装、商品或背景，v0.6 执行以下固定停顿链：

1. 用已验证 Template IR、Generation Request 和 direct-child reference pack
   执行 `prepare-generation`；它输出 `generation-plan.json`、待审 Plan Review
   和输入联系表；
2. 人工逐槽确认引用、目标、`adapter_id`、`adapter_version`、可选
   `controller_label`、执行模式、权利与隐私配置。`local-file-drop` 和
   `controller-managed` 是唯一允许模式；不允许 `local-command`；
3. 若选 `controller-cloud`，Request 和 Plan Review 都必须显式
   `cloud_upload_confirmed=true`；普通 rights flag、控制器名称或 passing schema
   都不是上传同意。CLI 仍不上传；
4. 已批准后，外部 Codex ImageGen 控制器或用户自有本地 CUDA 工具在 CLI 外创建
   静态结果，写进一个新的 direct-child result pack。每个非 passthrough/非 omit
   target slot 恰有一张静态图片；audio 不属于 result pack；不得改写 plan；
5. 用 `propose-generation-results` 建立逐槽 result proposal、待审 result
   review 与结果联系表；拒绝槽位必须进入新 result pack/proposal/review，而不是
   覆盖已批准图；
6. 两份 Review 通过后执行 `assemble-generation-pack`。静态图按 EXIF 方向转正、
   去 metadata 重编码为 PNG；仅允许从 reference pack 的已批准 audio
   passthrough 原样透传；输出仅含 exact-slot 媒体且无
   JSON、prompt、报告或 sidecar；
7. 组装包仍必须进入 v0.5 `propose-assets -> asset review -> freeze-assets`，
   才能作为 renderer 的冻结资产。

该流程不是虚拟试衣/受控图像生成/背景补全的内置 adapter。CLI 不调用这些模型、
不调用任意 shell、不自动发现 CUDA、不下载权重、不持有 provider 凭据，也不作
任何网络请求。

### 8.8 Review looks

先审核 generation 输入联系表和结果联系表，再审核 v0.5 asset contact sheet。
逐槽检查身份、身材、姿态、服装版型、颜色、图案、Logo、商品文字、背景、手部
和异常肢体。任何未批准项只重试相关槽位，但重试必须使用新 result pack/proposal/
review；技术 probe、哈希和联系表不能自动证明视觉正确。

### 8.9 Preview

按顺序输出：

1. 带槽位边界的调试预览；
2. 低清无水印预览；
3. 关键帧差异报告；
4. 待确认警告。

### 8.10 Render and package

预览通过后渲染主分辨率，再从主时间轴生成其他分辨率。复用或替换音频，执行最终 QA，输出成片、模板、映射、run manifest 和报告。

## 9. 模块设计

### 9.1 `doctor`

职责：发现工具、版本、GPU、编解码器、磁盘和执行模式。输出稳定 JSON，不自动安装大模型或上传文件。

### 9.2 `reference-analyzer`

子模块：

- `probe_media`；
- `extract_survey`；
- `detect_scenes`；
- `analyze_audio`；
- `detect_overlays`；
- `detect_entities`；
- `track_entities`；
- `estimate_camera_motion`；
- `infer_slots`；
- `classify_support`。

### 9.3 `asset-normalizer`

将不同来源素材转成统一尺寸、色彩空间、命名和透明度，生成主体框、锚点、缩放策略和安全区。

### 9.4 `generation-router`

长期目标接口：

```text
prepare_identity(input, policy) -> identity_asset
prepare_outfit(identity, garment, pose, policy) -> look_asset
prepare_product(input, policy) -> product_asset
prepare_background(input, geometry, policy) -> background_asset
modify_video(segment, references, policy) -> video_asset
```

v0.6 不实现或调用上述接口。它只实现 Generation Request/Plan/Result 的本地
合同：允许 `local-file-drop` 或 `controller-managed`，并记录受限
`adapter_id`、`adapter_version` 和 controller-managed 的 `controller_label`。
这些字符串不能包含路径、URL 或凭据。隐私仅可标记 `local-only` 或
`controller-cloud`；云端情形在 Request 与 Plan Review 都必须
`cloud_upload_confirmed=true`。CLI 不执行 adapter、CUDA、shell、下载或网络；
真实外部控制器的许可证、成本、GPU、保留期、模型版本和可重复性仍需由主模型/
人工在审查中判断。

### 9.5 `render-compiler`

把 Template IR 和 Asset Manifest 编译为确定性时间轴。第一实现建议以 Remotion 为主，FFmpeg 负责媒体探测、预处理、音频和最终编码；简单模板可以直接使用 FFmpeg。

### 9.6 `qa-engine`

输出机器状态和供人审阅的少量视觉材料，避免让 Codex观看全部帧。支持失败定位到具体镜头、帧段和槽位。

### 9.7 `project-controller`

维护状态机：

```text
NEW
-> PREFLIGHTED
-> ANALYZED
-> SUPPORT_CLASSIFIED
-> SLOTS_CONFIRMED
-> TEMPLATE_FROZEN
-> ASSETS_VALIDATED
-> LOOKS_APPROVED
-> PREVIEW_APPROVED
-> RENDERED
-> QC_PASSED
-> PACKAGED
```

目标状态机应在失败时保留已完成阶段，并允许从最近成功点恢复；当前 Alpha 尚未实现该项目控制器和断点恢复。

## 10. 命令行和工具接口

当前产品/CLI 版本为 `0.6.0-alpha`，并保留下列在 v0.4 引入的稳定 JSON
参考计划命令；Codex 不应依赖自然语言日志。参考 Proposal schema 为
`0.4.0`，Frozen Compiler Plan schema 保持 `0.3.0`，编译输出的 Template IR
schema 保持 `0.2.0`。v0.5 的资产命令和 schema 版本见第 25 节：

```text
video-remix doctor [--ffmpeg <path>] [--ffprobe <path>] --json
video-remix probe <reference> [--ffmpeg <path>] [--ffprobe <path>] --json
video-remix survey <reference> --project-root <project-dir> [--output-dir reference-survey] [--frame <n> ...] [--samples <n>] [--ffmpeg <path>] --json
video-remix validate-proposal <proposal.json> --json
video-remix validate-review <review.json> --json
video-remix propose <source> --project-root <project-dir> --output-dir <output-dir> --template-id <template-id> [--slot-count-hint] --reference-rights-confirmed [--audio-rights-confirmed] [--audio-mode] [--output-profile] --ffmpeg <path> --ffprobe <path> --json
video-remix freeze-plan <project-relative-proposal.json> <project-relative-review.json> --project-root <project-dir> --output-dir <output-dir> --json
video-remix validate-compiler-plan <compiler-plan.json> --json
video-remix compile <reference> <compiler-plan.json> --project-root <project-dir> [--output-dir template-compile] [--ffmpeg <path>] [--ffprobe <path>] [--timeout <seconds>] --json
video-remix validate-template <template.ir.json> --json
video-remix validate-assets <template.ir.json> <assets.json> --project-root <project-dir> --json
video-remix validate-generation-request <generation-request.json> --json
video-remix prepare-generation <template.ir.json> <generation-request.json> --project-root <project-dir> --reference-pack <direct-child> --output-dir <direct-child> --generation-rights-confirmed --ffprobe <path> --timeout <seconds> --json
video-remix validate-generation-plan <generation-plan.json> --json
video-remix validate-generation-plan-review <generation-plan-review.json> --json
video-remix propose-generation-results <generation-plan.json> <generation-plan-review.json> --project-root <project-dir> --result-pack <direct-child> --output-dir <direct-child> --generation-results-rights-confirmed --ffprobe <path> --timeout <seconds> --json
video-remix validate-generation-results-proposal <generation-results-proposal.json> --json
video-remix validate-generation-results-review <generation-results-review.json> --json
video-remix assemble-generation-pack <generation-plan.json> <generation-plan-review.json> <generation-results-proposal.json> <generation-results-review.json> --project-root <project-dir> --output-dir <direct-child> --ffprobe <path> --timeout <seconds> --json
video-remix render <template.ir.json> <assets.json> --project-root <project-dir> [--frame-directory render/master-frames] [--debug-bounds] [--summary <root-contained.json>] [--ffmpeg <path>] --json
video-remix qa <delivery.mp4> [--width <n>] [--height <n>] [--fps <n>] [--frames <n>] [--expect-audio|--expect-no-audio] [--ffmpeg <path>] --json
```

v0.4 的 `propose` 与 `freeze-plan` 要求 `--output-dir` 是
`project-root` 下一个尚不存在的一级子目录名称，例如 `proposal` 或
`frozen-plan`。绝对路径、嵌套路径、`.`、`..` 和已存在目标会在媒体处理或
任何工件写入前被拒绝；该限制用于保证 Windows 本地原子发布和路径边界。
另外，freeze-plan 的两个 packet 参数必须是相对 `project-root` 的规范路径；
绝对本地、盘符根和 UNC 路径在候选 packet 检查前拒绝。此规则不限制独立的
validate-proposal 或 validate-review。

v0.6 的 `reference-pack` 和 `result-pack` 必须是合同允许的现有一级子目录，
`output-dir` 必须是新的一级子目录名称；均不得为绝对、嵌套、点段、链接或
reparse-point 路径。plan/review/proposal 的 packet 参数使用合同
规定的 project-root 相对规范路径。`assemble-generation-pack` 的输出只能包含
媒体，不能带 prompt、JSON、报告、凭据或其他 sidecar，随后仍需经过 v0.5
资产包的独立扫描和冻结。

Alpha 通用规则：

- 标准输出返回稳定 JSON 摘要；propose、freeze-plan、`compile`、`survey` 和可选 `render --summary` 写入的工件均受 project root 约束；
- propose 成功返回 exit 0 但状态始终为 review_required，不是批准；Proposal/Review 校验失败和 freeze-plan 失败返回 exit 2。freeze-plan 在失败时不得写出部分 Frozen Compiler Plan；
- `compile` 在任何最终可见输出目录创建前完成 Compiler Plan Schema 校验和媒体相关语义预检。`review_required=false` 返回码为 `0`；成功但需要人工审核返回码为 `1`；校验或运行错误返回码为 `2`；
- `compile` 的 JSON 只包含根目录相对工件路径、哈希和简短审核事实，绝不内联完整 Template IR、逐帧评分、源文件绝对路径或工具绝对路径；
- Proposal 仅可包含 SHA-256、width、height、精确 frame_count、fps 和 has_audio 这组安全技术 source fingerprint；不得包含源文件名/绝对路径、工具路径、容器 tags、title、artist、comments、账号身份、raw probe 或原始证据；
- `render` 写入任何帧前必须完成模板与文件资产校验，之后必须对每个输出执行 QA；
- 禁止 shell 字符串拼接执行 FFmpeg，使用参数数组；
- 不自动发现任意视频 family 或分类语义槽位、不执行 OCR、不在 CLI 内生成换装
  资产、不执行任意 shell/CUDA/模型/权重下载/网络上传，也不把技术解码 QA 误称
  为视觉/权利验收。v0.6 仅可记录并审核外部执行声明；`controller-cloud` 必须在
  Request 与 Plan Review 都 `cloud_upload_confirmed=true`，CLI 自身仍离线。

propose 只能产生最大居中 9:16 source crop、carousel、subject、slot_count、timing、比例布局和背景色等待审候选。它不是 platform chrome/UI 检测或移除器；chrome、非居中内容、非均匀裁剪和语义/时序歧义必须由审核者更正。

## 11. 目录与项目状态

运行项目不提交 Git：

```text
workspaces/<project-id>/
├── project.json
├── rights.json
├── source/
├── templates/
│   └── template.ir.json
├── assets/
│   ├── original/
│   └── derived/
├── generation-reference-pack/       # private user references; never commit
├── generation-plan/                 # private plan/review/contact sheet
├── generation-result-pack/          # private external result file-drop
├── generation-results-proposal/     # private result review/contact sheet
├── generation-asset-pack/           # media-only bridge output
├── analysis/
│   ├── media.json
│   ├── slots.json
│   ├── contact-sheets/
│   └── warnings.json
├── renders/
│   ├── debug/
│   ├── previews/
│   └── final/
├── qa/
└── runs/<run-id>/run.json
```

源素材、Generation Request、raw prompt、外部控制器声明、reference/result pack
和审核联系表默认保持在项目隔离目录，不进入 Skill 目录和 Git 历史。`generation-asset-pack`
虽只包含媒体，仍是用户数据；它必须继续经过 v0.5 freeze-assets，不能当成可提交
的已冻结 manifest。

0.4 的 proposal 输出目录还包含严格 Proposal、pending Review、overview
contact sheet、geometry preview 和 timing profile。所有这些工件都必须是
project-root 相对引用，并按原子发布处理；不得把源文件名、用户绝对路径、
工具绝对路径、容器 metadata、账号信息、raw probe 或原始帧写进 Proposal
或公开 CLI JSON。安全技术 source fingerprint 仅允许 SHA-256、width、
height、精确 frame_count、fps 和 has_audio。

## 12. 渲染策略

### 12.1 确定性优先

能够通过 2D 图层、裁切、遮罩、跟踪、转场和关键帧完成的部分不使用视频生成模型。这样时间轴稳定、成本低、重渲染可复现。

### 12.2 静态资产优先

固定姿势换装视频使用一组批准的高质量静态人物图，通过硬切、轻推拉、呼吸位移和程序化滚轮产生动态效果。只有参考视频真实需要连续运动时才进入视频 adapter。

### 12.3 主时间轴

以帧为唯一时间基准，避免浮点秒累积误差。音频和转场全部换算成帧；目标误差不超过一帧。

### 12.4 输出

默认 H.264/AAC、`yuv420p`、恒定帧率和 faststart；主输出 1080×1920，同时生成 720×1280。保留可配置 HEVC、透明中间文件和无音频母版。

## 13. 质量验收

### 13.0 Proposal、Review 与 Freeze

0.4 必须在既有 Template IR 与媒体 QA 之前通过下列自动关卡：

- Proposal schema 0.4.0 及其嵌套 candidate plan；
- 精确 CFR、零旋转、时长不超过 60 秒、FFmpeg/ffprobe 可用性；
- project-root 路径约束、无源/工具绝对路径泄露与工件原子性；
- propose/freeze-plan 输出必须是 project-root 下尚不存在的一级子目录，嵌套、绝对、点段或已存在目标在任何处理前拒绝；
- overview contact sheet、geometry preview、timing profile 和代表帧的证据边界；
- Proposal/Review 的精确 SHA-256 绑定；
- family、geometry、slot_count、timing、carousel、background、audio、authorization 全部显式确认；
- freeze-plan 的失败无写入，以及输出仍为 schema 0.3.0 Frozen Compiler Plan。

Proposal 的 source_rect 只可被描述为最大居中 9:16 构图候选；当 chrome、
非居中内容或非均匀裁剪使它不正确时，审核者必须修正。该关卡不包含 OCR、
platform UI 语义检测、自动批准或隐藏像素恢复。

### 13.0.6 外部生成资产桥

v0.6 在 v0.5 资产冻结之前增加以下 P0：

- `prepare-generation` 的 rights flag 与 `propose-generation-results` 的
  results-rights flag 必须在读取相应私有 pack 前存在；
- Request/Plan/Review/Result Proposal 的 schema、哈希绑定和 project-root
  路径规则必须通过；reference/result pack 仅允许合同中的一级安全媒体；
- 仅可声明 `local-file-drop` 或 `controller-managed`，并记录受限
  `adapter_id`/`adapter_version` 和可选 `controller_label`；这些字段不允许
  path、URL 或凭据；
- `local-command`、任意 shell、CLI CUDA/模型执行、权重下载、浏览器、API SDK
  和网络请求均为 fail；
- 对 `controller-cloud`，Request 和 Plan Review 都必须
  `cloud_upload_confirmed=true`。这只是外部控制器的审计声明，CLI 不上传，且
  不自动证明服务条款、保留期、许可证或实际上传范围；
- 人工逐槽确认身份、身材/姿态、服装/商品/Logo/背景忠实度、手部及其他 artifact、
  render-ready 状态与结果权利。媒体解码、哈希和联系表不能替代这些判断；
- 被拒结果只能以新的 result pack/proposal/review 局部重试；不得改写已批准 plan
  或已批准图片；
- assembly 必须原子输出纯媒体 exact-slot pack：静态图片 EXIF 转正并去 metadata
  重编码 PNG，reference pack 的已批准 audio passthrough 原样透传，禁止 JSON、
  prompt、报告、sidecar、凭据和其他未知
  文件；之后仍要通过 v0.5 `propose-assets -> review -> freeze-assets`。

### 13.1 结构和媒体

- 文件可解码；
- 画幅、分辨率、帧率和时长符合配置；
- 时间误差不超过一帧；
- 音画同步误差不超过一帧；
- 无黑帧、坏帧、非预期冻结和尾帧截断。

### 13.2 模板复刻

- 镜头和换装切点误差不超过一帧；
- 关键图层位置和滚轮轨迹在模板容差内；
- 缓动、停留和节拍事件符合 IR；
- 删除层不进入最终画面。

### 13.3 人物和服装

- 模特身份通过人工审核和可选人脸特征阈值；
- 姿态和主体锚点稳定；
- 服装颜色、轮廓、领口、袖口、长度、主要印花和 Logo 单独检查；
- 手、腿、头发和衣服边缘不存在明显生成错误；
- 相邻帧无明显人物漂移和闪烁。

### 13.4 标识清除

当前 0.4 不使用 OCR 或自动 platform UI 语义检测。结合已知区域的人工
检查、contact sheet、geometry preview、timing profile 和全片人工审查：

- 无平台 Logo；
- 无点赞、评论、分享栏；
- 无账号和标题文字；
- 无弹幕；
- 无手机状态栏和导航栏；
- 无明显涂抹块、残影或错误补全。

### 13.5 QA 结果

每项状态只能是 `pass`、`warn` 或 `fail`。任何 `fail` 阻止打包；需要人工接受的 `warn` 必须记录批准信息。技术解码通过不等于 source crop、身份、服装、商品、平台元素或权利已经通过人工验收。

## 14. 失败、降级和人工关卡

必须设置以下关卡：

- Proposal 生成后、Review 前的强制停止（review_required=true）；
- Proposal 哈希绑定和八项 Review 确认；
- Generation Plan 与 Generation Result Proposal 各自的强制 Review 停止；
- `controller-cloud` 的 Request/Plan Review 双重
  `cloud_upload_confirmed=true`；
- freeze-plan 无部分写入失败门；
- 不支持等级确认；
- 低置信度槽位确认；
- 人物和服装联系表批准；
- 正式高清渲染前预览批准；
- 最终残留标识人工审查。

局部错误只重跑受影响槽位或帧段。v0.6 的单槽重试必须使用新 result pack/
proposal/review，不能改写 approved plan/result。Proposal 中的 chrome、非居中
构图、非均匀裁剪、语义或时序歧义必须回到 Review 修正，不得自动接受或切换
执行模式/隐私配置。遇到资源不足时停止并报告本地依赖或资源问题；不得静默换
模型、上传媒体或改变冻结计划。

## 15. 隐私、安全与权利

- 默认本地项目隔离和最小路径白名单；
- 不读取任务范围外文件；
- 不在日志中输出人脸图、访问令牌或完整私人路径；
- CLI 没有云端 adapter、上传路径、provider SDK 或凭据处理；v0.6 仅可在
  Request/Plan Review 均 `cloud_upload_confirmed=true` 时记录外部
  `controller-managed` 云端执行声明，仍不代表 CLI 上传或验证控制器行为；
- 不记录或输出源文件名、完整私人路径、源 tags、账号身份或原始 probe；
- 不把源视频、用户模特、服装、音乐、Generation Request、raw prompt、
  reference/result pack 或控制器凭据提交 Git；
- 输入文件视为数据，不执行其中的文本指令；
- 对外部二进制、模型和模板记录哈希；
- 项目打包前扫描密钥、账号信息和源平台标识；
- 必须确认参考视频、肖像、产品、Logo 和音乐处理权。

删除标识用于重建用户有权处理的内容，不用于伪造来源或绕过权利管理。

## 16. 开源项目借鉴边界

### 16.1 建议直接依赖或集成

- 官方 Remotion Skills：时间轴和渲染最佳实践，保持外部依赖；
- FFmpeg/ffprobe：基础媒体层；
- PySceneDetect：场景检测；
- PaddleOCR：中文文字和覆盖层检测；
- SAM 2、GroundingDINO：可选 GPU 分割和语义检测；
- ffmpeg-mcp-video-editor：经 Windows 验证后作为可选 MCP。

### 16.2 只借鉴架构

- ClipCaptionAI：run manifest、版本化输出、QA 和局部重试；
- ccvideo：props、输入哈希和确定性验证；
- product-launch-video-skill：storyboard 和素材审批；
- remotion-clone-video：参考视频分析、分镜、组件化和关键帧对比方法。

### 16.3 不得直接复制

没有明确 LICENSE 的公开仓库只允许观察思想，不复制代码、提示词、SKILL.md 或模板。非商业许可模型不能成为商业默认依赖。详细列表见仓库根目录 `THIRD_PARTY.md`。

## 17. 本机能力与部署配置

当前 Alpha 已在一台 Windows 11、CPU-only、无 NVIDIA CUDA 的中等配置测试机上完成端到端验证。公开文档只记录匿名能力档位，不记录用户设备的精确型号、内存或显存。

当前 Alpha 已稳定本地执行：

- FFmpeg/ffprobe；
- Pillow 逐帧合成、抽帧和联系表；
- 音频抽取、裁切、变速、增益和淡入淡出；
- 720p/1080p 视频编码。

OCR、OpenCV 分析和 Remotion/Node 渲染属于后续可选能力，当前 Alpha 未集成，也不作为本地运行时依赖。

不适合本机高质量执行：

- 大型虚拟试衣扩散模型；
- SAM 2 大模型长视频推理；
- 高质量视频到视频扩散；
- 大批量高分辨率多 adapter 并行生成。

建议执行配置：

- `local-only`：CLI 严格离线；v0.6 可记录外部本地 CUDA/试衣工具产生的
  `local-file-drop` 结果，但不会自行调用或安装该工具；
- `controller-cloud`：仅是经 Request 与 Plan Review 双重
  `cloud_upload_confirmed=true` 后的外部控制器声明，不是当前 CLI 的上传、API 或
  provider runtime 路径；
- `gpu-worker`：可由用户自有 NVIDIA 工作站在 CLI 外执行，并将结果作为新的
  result pack 交回；CLI 不发现 GPU、不下载权重、不运行 worker。

## 18. 测试与评测体系

### 18.0 0.4 Proposal/Review/Freeze 回归

新增回归必须覆盖：

- Proposal schema 0.4.0 和嵌套 plan；
- 精确 CFR、零旋转、60 秒上限、工具缺失、路径逃逸和原子性；
- 允许的安全技术 source fingerprint 与禁止的文件名、绝对路径、tags、账号身份、raw probe 泄露；
- evidence 数量/尺寸边界和 overview/geometry/timing 工件；
- Proposal SHA-256 与 Review 的精确绑定；
- 八项确认、approved_plan 修正与拒绝/pending Review；
- freeze-plan 的失败无写入、成功冻结为 Compiler Plan 0.3.0；
- 既有 compile、render、Template IR 0.2.0 与技术 QA 不变；
- 人工视觉、裁剪和权利审核记录。

### 18.0.6 v0.6 Generation bridge 回归

新增回归必须覆盖：

- Request/Plan/Plan Review/Results Proposal/Results Review 的 schema、重复键、
  有限数字和 hash binding；
- `local-file-drop`/`controller-managed` 允许值，`adapter_id`/
  `adapter_version`/`controller_label` 的长度与 path/URL/凭据拒绝；
- `local-only` 与 `controller-cloud` 的
  `cloud_upload_confirmed` 双重确认、缺失/冲突拒绝，以及公共 CLI JSON 不泄露
  私有执行声明；
- rights flags 在读取 Request/reference/result pack 前 fail closed；
- direct-child/path escape/link/reparse/unknown/sidecar/动画和部分输出拒绝；
- 每个 rejected slot 以新 result pack/proposal/review 重试，不能覆盖已批准证据；
- 静态图 EXIF 转正、metadata-free PNG 重编码、reference pack audio
  passthrough、纯媒体
  assembled pack；
- assembled pack 必须能进入既有 v0.5 propose-assets/review/freeze-assets，且
  仍会因不正确的 exact mapping 或未审核资产而被拒绝；
- 在不含任何模型、shell、网络或权重的干净环境中运行全部 bridge 测试。

### 18.1 单元测试

- manifest schema；
- 帧和秒换算；
- 槽位唯一性和数量；
- 资产哈希；
- 路径白名单；
- 状态转换；
- 错误码；
- 缓存键。

### 18.2 集成测试

- FFmpeg 探测与抽帧；
- 有音频和无音频视频；
- VFR、旋转元数据和奇数尺寸输入；
- Remotion 预览和正式渲染；
- 中途取消和恢复；
- 单槽位重试；
- 双分辨率一致性。

### 18.3 Skill 行为测试

使用独立任务验证 Codex 是否：

- 正确触发 Skill；
- 先 doctor、再分析和分类；
- 在低置信度或云端上传前暂停确认；
- 不对 S3/S4 承诺像素级复刻；
- 不把平台 UI 当成要保留的创意图层；
- 缺少第 12 套服装时阻止正式渲染；
- 能从项目状态恢复；
- 只读取需要的参考文件和摘要。

### 18.4 基准集

阶段一：当前视频 + 第二组完全不同的12套素材。  
阶段二：10–20条 S1 视频，覆盖不同滚轮位置、数量、切点和背景。  
阶段三：20–50条 S1/S2 视频，包含慢运镜、遮挡和不同产品类型。  
阶段四：建立 S3/S4 负例集，重点检测错误承诺。

## 19. 开发路线图

### Phase 0：设计仓库与 Alpha 执行底座（已完成）

- 完成 Skill、设计、schema、示例 manifest、许可证和 GitHub 元信息；
- 建立 Template IR/资产校验、路径/哈希约束与公开 CLI；
- 实现本地 probe/survey、S1 Pillow/FFmpeg 确定性渲染和逐输出技术 QA；
- 不实现语义模板编译、换装资产生成或视觉语义验收。

### Phase 1：当前视频专用金标准（已完成本地验收）

- 精确提取 11.58 秒、30fps、12套切点和顶部滚轮；
- 完成 clean-room 模板；
- 生成12套当前模特穿搭资产；
- 输出720×1280和1080×1920；
- 清除全部平台和弹幕元素；
- 保存 run manifest 和 QA。

### Phase 2：真正可复用模板（Manifest 复用已验证，其余继续建设）

- 已使用第二组完整素材完成双分辨率端到端回归；
- 已验证模板字节不变、只改 manifest 即可重新渲染；
- 支持背景和音频替换；
- 支持局部重试；
- 完成失败恢复。

### Phase 3：有界计划提议与审核（v0.4，已实现）

- 对已授权、本地 fixed-subject-carousel S1 输入提供 propose -> review -> freeze-plan；
- 自动提出最大居中 9:16 source crop、carousel/subject 区域、slot_count、时序、比例布局和背景色，并生成 contact sheet、geometry preview、timing profile；
- 强制 review_required=true、Proposal 哈希绑定、八项显式确认和可修正的 approved_plan；
- 冻结为兼容 v0.3 的 Compiler Plan schema 0.3.0，保持 Template IR 0.2.0 与既有 compile/render/QA 不变。

本阶段不声明从任意视频自动发现 family、主体、服装、商品、platform UI 或语义槽位；也不包含 OCR、云端、资产生成、自动批准或隐藏像素恢复。

### Phase 3.5：外部生成资产桥（v0.6，已实现）

- 对现有 Template IR 支持 Generation Request -> prepare-generation -> Plan
  Review -> 外部 controller/file-drop -> Result Review ->
  assemble-generation-pack；
- 仅记录 `local-file-drop`/`controller-managed` 和
  `local-only`/`controller-cloud`；后者要求 Request 与 Plan Review 均
  `cloud_upload_confirmed=true`；
- 支持逐槽局部重试的不可变 result pack/proposal/review；
- assembly 静态图 EXIF 转正、去 metadata PNG 重编码、reference pack audio
  透传、纯媒体
  exact-slot 输出，并强制回到 v0.5 资产审核和冻结；
- 不内置模型、虚拟试衣、CUDA、shell、网络、上传、权重下载或 provider SDK。

### Phase 4：S1 通用模板族

- 商品展示、图片卡片、固定口播、简单字幕和2D动效；
- 模板家族分类；
- 人工 JSON override；
- 20条以上回归基准。

### Phase 5：S2 跟踪合成

- 动态蒙版、物体跟踪、透视、遮挡和颜色匹配；
- 可视化修正关键帧；
- GPU worker 或批准的云端 adapter。

### Phase 6：本地 MCP 与 Plugin

- 长任务队列、进度、取消、并发和模型驻留；
- 安装器、依赖 doctor 和版本迁移；
- 将 Skill、MCP 配置和模板作为 Codex Plugin 分发。

## 20. 模型路由、质量治理与 Token 预算

### 20.1 基本原则

采用“controller_current 负责语义与最终接受，gpt-5.6-terra max 负责已冻结实现，确定性工具负责证明”的三层体系。降低模型成本只能发生在已经冻结、可测试、可回滚的任务上，不能降低人物一致性、服装准确度、平台标识清理或最终视频验收标准。

“当前主模型”是运行时正在主持项目的高能力模型，不在 Skill 中写死具体名称。每次运行必须记录实际模型。`gpt-5.6-terra` 的 `max` 是 reasoning effort，不是另一个模型名称。

编排层使用逻辑配置名而不是在业务代码中散落模型名：`controller_current`（当前主模型）、`builder_quality`（Terra Max）、`builder_standard`（Terra High）和 `mechanical_worker`（通过同类评测后才可启用的较低配置）。新任务族默认从 `builder_quality` 起步。

### 20.2 任务路由矩阵

| 工作 | 默认模型/执行者 | 是否允许降级 | 强制复核 |
|---|---|---|---|
| 用户意图、未知条件、产品边界 | 当前主模型 | 否 | 用户确认重大歧义 |
| Proposal 的受限语义审查、family 接受、裁剪/时序歧义 | controller_current | 否 | 本地工件、显式 Review 和人工确认 |
| Generation Request/Plan、adapter_id/version、controller_label、隐私配置与云上传确认 | controller_current + 人工 | 否 | Request/Plan Review；`controller-cloud` 双重 `cloud_upload_confirmed=true` |
| 生成结果的身份/服装/商品/Logo/背景/手部判断和局部重试 | controller_current + 人工 | 否 | Result contact sheet、逐槽 Review、新 result pack |
| 删除、保留和替换边界 | 当前主模型 | 否 | 最终残留检查 |
| Template IR 架构或不兼容 Schema 变更 | 当前主模型主导；Terra Max可实现 | 否 | 当前主模型审查 + Schema测试 |
| 已冻结接口的非平凡代码实现 | `gpt-5.6-terra` + `max` | 通过评测后可降至 high/xhigh | 测试 + controller_current 代码审查 |
| Remotion组件、FFmpeg参数生成 | Terra Max | 仅限已有模板族 | 帧级测试 + 预览审查 |
| 单元测试、fixture、重复adapter骨架 | Terra high/medium | 可以 | CI和覆盖率要求 |
| 文件清单、JSON机械转换、日志摘要 | Terra较低配置或更低成本模型 | 可以 | Schema或哈希检查 |
| 模特身份、服装、商品准确度判断 | 当前主模型 + 人工 | 否 | 联系表和最终视频 |
| 隐私、路径安全、授权、许可证和云上传 | 当前主模型 | 否 | 安全测试和人工批准 |
| 最终成片验收和发布判断 | controller_current + 人工 | 否 | 所有自动QA通过 |

### 20.3 下放前合同

主模型在把任务交给 Terra Max 或更低模型前，必须冻结：

- 目标、非目标和允许修改的文件；
- 输入输出 Schema；
- 不变量、禁改项和风险；
- 必须执行的测试；
- 视觉和人工验收标准；
- 失败与升级条件。

对 v0.6，冻结项还必须包含执行模式、privacy profile、受限
`adapter_id`/`adapter_version`、可选 `controller_label`、何时允许
`cloud_upload_confirmed=true`、CLI 不运行模型/shell/CUDA/网络的边界、局部重试
目录策略、去 metadata PNG 组装规则，以及 v0.5 仍为最终资产冻结关卡。

低级模型不得通过“写代码”暗中重新做产品决策。长任务必须拆成有界工作包；一旦跨模块、改变 Schema 语义或出现新的视觉判断，worker 停止并升级，不能自行扩展范围。

### 20.4 降级与升级

默认代码 worker 使用 Terra Max。只有某个任务族在代表性样本上通过 A/B 评测后，才允许降到 Terra high、medium 或更低。不得仅因为任务耗时或成本高而自动降级。

以下情况立即升级回当前主模型：

- 参考视频语义或图层归属不清；
- 涉及身份、服装、商品、遮挡、蒙版或帧时序；
- 涉及路径白名单、隐私、授权、许可证、缓存隔离或云端上传；
- Schema 变化会影响渲染语义；
- 自动指标与人眼结论冲突；
- 同一 worker 连续失败两次；
- worker 修改了冻结需求。

### 20.5 八级质量门

1. **G0 契约冻结**：主模型冻结范围、接口、不变量、测试、金标准和验收标准；
2. **G1 worker 自检**：返回改动文件、测试、假设、警告和实际解析到的模型配置；
3. **G2 确定性验证**：独立执行 lint、类型、Schema、单元和集成测试；
4. **G3 范围检查**：检查 diff 未越界、未改变冻结决策；
5. **G4 主模型代码审查**：所有实质代码都由当前主模型复核，而不只审查安全和渲染代码；
6. **G5 金标准回归**：当前参考视频和第二套完整素材都跑端到端回归；
7. **G6 视觉验收**：检查联系表、人物、服装、商品、残留标识、节奏和完整视频；
8. **G7 发布签字**：记录哈希、测试证据、警告、审批与最终结论后才能打包。

worker 不能只依靠自己在同一改动中新写的测试证明正确，至少还要有既有回归、独立验证器或已批准的金标准。任何模型都不能自行批准自己的输出。端到端或最终审查失败时，保留上一版已批准模板和 renderer，不得因单元测试通过而发布。

### 20.6 运行记录

每个模型任务记录：`task_id`、父运行、任务类型、风险等级、请求模型、实际解析模型、provider、reasoning effort/mode、路由策略、prompt/schema/评测基线版本、允许修改的文件、输入输出哈希、diff或commit、测试命令与结果、QA指标、输入/缓存/输出Token、延迟、估算成本、重试次数、升级原因、复核模型、批准引用和最终结论。运行时不提供的用量字段必须标为 unknown，不能猜测。

如果当前运行环境不支持按请求或按子代理切换模型，则保持当前模型执行，不得虚构已切换；模型路由策略仍作为未来 API/MCP 编排合同使用。

### 20.7 Token 预算

以下是工程规划范围，不是模型账单承诺；图像/视频生成另计：

| 目标 | Codex 文本 Token 估计 | 说明 |
|---|---:|---|
| 从当前状态完成第一条成片、模板和可执行 Skill | 30万–100万 | 主要是12套素材、时间轴、渲染、QC和返工 |
| 稳定支持同类固定镜头换装视频 | 累计110万–380万 | 包含第二素材集、恢复、缓存、验证和多样本测试 |
| 定义范围内多类参考视频 MVP | 累计650万–2000万 | 包含 Template IR 编译器、模板族、人工修正和基准集 |
| 较稳定的 S1/S2 通用系统 | 累计1650万–6000万 | 包含跟踪、动态蒙版、队列、质量评分和大量回归 |

控制 Token 的措施：

- Codex 只看关键帧、联系表和异常；
- 不回传 base64、逐帧 JSON 和完整 FFmpeg 日志；
- manifest 和脚本存盘，按需读取；
- 重试具体槽位，不重建全片；
- 固定模板和公共代码利用缓存；
- 将确定性判断放进脚本。

FFmpeg、Remotion、本地 OpenCV 和编码本身不消耗 Codex Token。

## 21. 发布标准

### 0.1.0-design

- Skill 元数据通过校验；
- 文档、schema、示例 manifest 和 validator 完整；
- 无私有视频、图片、音频、路径或密钥；
- 第三方许可证策略明确。

### 0.2.0-mvp

- 当前金标准视频完成；
- 12套和12商品映射正确；
- 双分辨率输出；
- 平台标识清除通过人工全片检查；
- 可从缓存恢复。

### 0.3.0-reusable

- 第二组素材无需改代码即可完成；
- 关键帧审核、预览和局部重试完整；
- Windows 一键 doctor；
- 公开安装说明可复现。

### 0.4.0-alpha

- propose、validate-proposal、validate-review 和 freeze-plan 完成有界 S1 的本地计划提议/审核/冻结；
- Proposal 固定为 review_required=true，并输出受限 contact sheet、geometry preview、timing profile 与安全技术 source fingerprint；
- Review 对精确 Proposal 哈希和八项确认 fail closed，审核者可明确修正 approved_plan；
- freeze-plan 失败无写入，成功输出兼容的 Compiler Plan schema 0.3.0；
- Template IR 0.2.0、既有 deterministic compile/render 与技术 QA 回归不变；
- 不声明任意视频 family 发现、OCR、云端、资产生成、自动批准或隐藏像素恢复。

### 0.5.0-alpha

- 新增严格本地 asset-pack 的 propose-assets、显式 Review 与 freeze-assets；
- exact filename stem 是唯一候选规则，missing、ambiguous、incompatible 与
  unresolved 都不能冻结；
- 发布 Asset Manifest 0.2.0、opaque flat copies 与 freeze report，renderer
  从哈希绑定 snapshot 读取图片并以 pipe:0 读取音频；
- Manifest/report 是本机审计记录而非可信签名，renderer 输出路径也不承诺
  抵抗恶意并发文件系统替换；
- Asset Manifest 0.1.0 仅保留 legacy 兼容。

### 0.6.0-alpha

- 新增 `validate-generation-request`、`prepare-generation`、Plan Review、
  `propose-generation-results`、Result Review 和
  `assemble-generation-pack`；
- 只允许 `local-file-drop`/`controller-managed`，并记录受限
  `adapter_id`/`adapter_version`/可选 `controller_label`；禁止路径、URL、凭据、
  `local-command`、任意 shell、CLI CUDA/模型、网络和权重下载；
- `controller-cloud` 必须在 Request 和 Plan Review 中均
  `cloud_upload_confirmed=true`；CLI 本身不上传且公共摘要不回显私有声明；
- 人工逐槽审核人物一致性、服装/商品/Logo/背景忠实度、姿态、手部与 artifact，
  局部重试不可改写已批准 plan/result；
- assembly 原子生成纯媒体 exact-slot pack：图片 EXIF 转正、去 metadata PNG
  重编码，reference pack 的 audio passthrough 透传；随后仍强制走 v0.5
  propose-assets/review/freeze-assets。

### 1.0.0

- 定义清楚的 S1 支持域；
- 至少20条参考视频回归；
- 自动模板编译成功率和误拒绝率达到设定阈值；
- 安全、隐私、许可证和错误处理完成审核；
- 版本迁移和兼容策略确定。

## 22. 已知未知与未知的未知

### 22.1 已知未知

- “本机操作”是否允许经过批准的云端生成；
- 外部 controller 是否能在不泄露 raw prompt/凭据的同时提供足够的版本、保留期和
  商用授权证据；
- `controller-cloud` 的最小上传集合、用户撤回/删除机制与 provider 变更后的重审
  策略；
- 未来是否允许他人商用本项目；
- 服装“相似”和“商品级精准”的验收阈值；
- 用户愿意接受多少模板确认和蒙版修正；
- 将来主要是换装、电商产品，还是覆盖所有短视频类型；
- 是否需要图形化模板编辑器；
- 未来 GPU worker 的硬件和部署地点。

### 22.2 未知的未知及防线

- 平台或编码器产生未预期的 VFR、HDR、旋转和色彩偏差：通过 preflight 和媒体基准测试防范；
- UI 遮挡了决定性动作或商品细节：标为不可恢复，进入重建或拒绝；
- 服装 Logo、文字或花纹被生成模型改写：独立商品 QA，关键细节使用原素材合成；
- 反射、透明、头发和手部导致蒙版错误：提高支持等级或人工关键帧修正；
- provider 更新导致同一提示输出变化：记录模型版本和种子，冻结批准资产；
- 外部 controller 报称 local-only 却转发了素材：v0.6 只能记录/审核声明，不能
  在 CLI 内证明其行为；敏感项目只能使用受信任的本地执行环境或额外审计；
- 第三方模型更改许可证或权重下架：adapter 隔离并维护依赖清单；
- 新参考效果无法映射现有 IR：保留扩展字段和模板版本迁移；
- 自动指标通过但人眼感到不自然：所有生成式人物最终保留人工批准；
- 用户把受保护平台 UI 当成“形式”要求复制：明确禁止层和重建规则；
- 缓存误用旧模特或旧服装：缓存键包含输入哈希、模板、adapter、版本和参数。

## 23. 当前视频的首个金标准

已知参考视频：

- 竖屏；
- 约11.58秒；
- 30fps、347帧；
- 单一固定主体和固定动作；
- 白色舞台；
- 顶部约200像素商品滚轮；
- 12套服装/造型切换；
- 原平台 UI 和弹幕全部排除；
- 原音乐保留或允许替换；
- 输出720×1280和1080×1920。

它属于 S1，是合适的首个 Template IR 基准。验收重点：切点误差≤1帧、12套无串位、人物锚点稳定、滚轮轨迹一致、无平台残留、双输出可播放。

## 24. 最终决策

0.4 的冻结决策是：对单一授权的 fixed-subject-carousel S1 家族，先以最大
居中 9:16 构图启发式和其他受限几何/时序候选执行 propose；始终停在
review_required；再由 controller_current/人工显式审核、哈希绑定并
freeze-plan。该启发式不等于 platform chrome/UI 语义检测或移除，不能替代
对 chrome、非居中内容、非均匀裁剪、语义、时序、视觉和权利的人工复核。

项目应以自研 `reference-video-rebuilder` Skill 为核心，而不是 fork 某一个现有视频仓库。官方 Remotion Skills 和 FFmpeg 作为外部基础层；分析、Template IR、槽位映射、生成路由、平台元素排除、身份/服装 QA、运行状态和可重复模板必须自行实现。

产品战略必须坚持“先模板、后编译器、再通用化”：先让当前视频和第二组素材达到可靠复用，再扩展同类型模板族，最后进入 S2 跟踪和生成式视频。这样第一阶段能产出真实可用结果，同时保留向通用 Skill 扩展的架构。

## 25. v0.5 严格本地资产包增量

### 25.1 范围与状态

本节是对保留的 v0.4 内容的增量，不替换其 reference Proposal、Review、
freeze-plan 或编译器合同。v0.5.0-alpha 只在一个已经验证的 Template IR 后，
增加本地素材包的 propose-assets -> 人工审核 -> freeze-assets -> render 路径。

它解决的是“把已提供的本地字节安全地冻结到模板槽位”，而不是识别素材含义。
当前实现没有 OCR、视觉识别、模糊文件名匹配、任意视频、动画支持、云端、
上传、生成资产或 GUI。asset-contact-sheet.png 加 JSON Review 是本地审核证据，
不是图形界面，也不是自动批准。

### 25.2 命令和路径边界

对 v0.5 使用下列稳定 JSON CLI：

~~~text
video-remix validate-asset-proposal <proposal.json> --json
video-remix validate-asset-review <review.json> --json
video-remix propose-assets <TEMPLATE> --project-root <project-dir> --asset-pack <pack-direct-child> --output-dir <output-direct-child> --asset-pack-rights-confirmed --ffprobe <path> --timeout <seconds> --json
video-remix freeze-assets <PROPOSAL> <REVIEW> --project-root <project-dir> --output-dir <output-direct-child> --ffprobe <path> --timeout <seconds> --json
~~~

TEMPLATE、PROPOSAL 和 REVIEW 必须是相对 project-root 的规范化路径；绝对路径、
盘符根、UNC、越界和不安全路径必须在读取 packet 之前失败。asset-pack 和
output-dir 只能命名 project-root 下的一级子目录；不得使用绝对路径、嵌套
路径、点段或已存在的输出目录。

propose-assets 在扫描 project 或 asset pack 前必须收到
asset-pack-rights-confirmed。该确认只表示用户已确认本地处理权利，绝不授权
云端上传、资产生成或自动批准。

### 25.3 扫描、候选和人工审核

asset pack 只扫描一级普通文件。允许的输入是静态 JPEG、PNG、WebP，以及可由
本地 ffprobe 通过 pipe:0 读取的 WAV、MP3、M4A、MKA 音频。任何未知文件、
视频、动画、sidecar、目录、链接、reparse point 或探测失败都使整个 pack
fail closed；不得静默跳过。

候选只能满足 filename stem 等于 Template slot_id，并且已探测媒体类型位于该
slot 的 accepted_media。零个、多个或不兼容的候选必须保留为 missing、
ambiguous 或 incompatible，不能由 OCR、视觉、关键词、相似文件名或人工
推断自动补齐。

propose-assets 发布三个本地审核工件：

- asset-pack-proposal.json；
- asset-review-decision.template.json；
- asset-contact-sheet.png。

Proposal 始终需要审核。审核者必须逐 slot 作出明确决定；每个 use 映射都要
确认内容、媒体兼容性、render-ready 状态和权利。仅可显式 omit 可选 slot；
required、unresolved、missing、ambiguous 或 incompatible 都不得冻结。

### 25.4 冻结、Manifest 和渲染

freeze-assets 绑定 Proposal、Template 和 inventory 的 SHA-256，并绑定已审核
Review；它随后安全重扫 pack，拒绝任何 inventory drift，并以原子方式发布：

~~~text
frozen-assets/
├── assets.json
├── asset-freeze-report.json
└── asset-0001.<canonical-extension>
~~~

assets.json 是 Asset Manifest schema 0.2.0：固定为 local-only，所有映射有
SHA-256，且引用不含来源语义的扁平副本。它不支持 provider asset 或云端
路径。Asset Manifest schema 没有 quality constraints 字段；质量阈值属于
Template IR 和人工 QA。

validate-assets 校验声明的 Manifest、slot 接受媒体、项目路径和适用哈希，
但不对媒体字节执行 sniff。严格的媒体识别只发生在 propose-assets 的扫描和
freeze-assets 的安全重扫。

Renderer 0.2.0 仅从经验证字节 snapshot 读取冻结图片，并将冻结音频只作为
FFmpeg 的 pipe:0 输入。Asset Manifest 0.1.0 的路径式渲染保留为 legacy
兼容，不构成 v0.5 的安全或可复现承诺。

Manifest 0.2.0 与 freeze report 是本机声明、哈希绑定的审计记录，不是审核者
签名，也不能证明工件一定由 freeze-assets 产生。拥有项目写权限的进程可以同时
改写 Proposal、Review、Manifest 和 report。受治理的工作流应保留整套工件；若
需要抵抗项目写入者并强制可信审批，必须另加可信签名或访问控制的不可变存储。

### 25.5 P0 和平台安全界限

在任何 render 之前，P0 至少要求：权利 flag 在分析前存在；路径和 direct-child
规则成立；完整 pack 仅含允许媒体；精确 stem 匹配；人工确认全部 use；Proposal/
Review/Template/inventory 绑定且重扫无漂移；冻结 Manifest 为 local-only
0.2.0 并包含所有 SHA-256；以及 renderer 使用 snapshot 和 pipe:0。任一失败
不得留下部分 frozen-assets 发布物。

Windows 强边界只覆盖 asset-pack 扫描、安全重扫和 frozen-assets 发布，包含
reparse-point 拒绝和 guarded snapshot/copy。Renderer 0.2 会把实际消费的资产
字节绑定到 Manifest 哈希，但帧目录和输出目录的 containment 假设渲染期间没有
恶意并发文件系统替换；本 Alpha 不是针对不可信本机写入者的沙箱。非 Windows
平台维持可观测、尽量 fail-closed 的检查，但不声称等价的 NT no-delete 保证。
validate-assets 只是声明式预检，不等同于 renderer 的运行时 link/reparse
边界。详细 P0 列表和最终媒体/人工验收见 QA gates；
controller_current 保留语义、权利和发布判断，Terra max 只能在合同冻结后
实施有界代码或测试任务。

## 26. v0.6 外部生成资产桥

### 26.1 产品定位

v0.6 解决的是“用户有模特/服装/商品/背景参考，但需要第三方图像生成或本地
CUDA 工具制作 render-ready 静态图”时的可审计交接，而不是把某个生成模型装进
Skill。它保持三件事分离：

- **控制平面**：Generation Request、Plan、两份 Review、哈希和同意记录；
- **外部执行平面**：Codex ImageGen 控制器、用户自有 CUDA 工作站或其他已批准
  工具；这些在 CLI 外运行；
- **确定性渲染平面**：v0.5 资产冻结与既有 Template IR/renderer。

因此“可以让外部控制器产出换装图”不等于“CLI 已经内置虚拟试衣”。一份通过的
Generation Plan 也不是自动批准；它只允许外部控制器按被审核的边界行动。

### 26.2 状态机与不可变性

```text
Template IR + Generation Request + reference pack
  -> prepare-generation
  -> generation-plan + pending plan review
  -> human/controller_current approval
  -> external controller or local file-drop
  -> new result pack
  -> propose-generation-results
  -> results proposal + pending result review
  -> human/controller_current approval
  -> assemble-generation-pack
  -> media-only exact-slot pack
  -> v0.5 propose-assets -> asset review -> freeze-assets -> render
```

Generation Plan 是已批准任务的冻结描述；它不能随着生成失败而被修改。某套结果
不合格时，创建一个**新的** result pack 和新的 results proposal/review。不得
就地覆盖 approved plan、approved result、已组装 pack 或 v0.5 frozen assets。
这样能把局部重试限定在某个槽位，同时保留输出来源和审批关系。

### 26.3 支持的执行声明

Request/Plan 只接受：

| 字段/枚举 | v0.6 允许值与规则 |
| --- | --- |
| 执行模式 | `local-file-drop` 或 `controller-managed`。前者由用户/本机工具把结果放入 pack；后者由外部控制器接收已批准计划后生成本地结果。 |
| 隐私配置 | `local-only` 或 `controller-cloud`。 |
| 外部标识 | 受限 `adapter_id`、`adapter_version`；`controller-managed` 再记录 `controller_label`。均不能是路径、URL 或凭据。 |
| 云端上传 | 仅 `controller-cloud`；Generation Request 与已批准 Plan Review 都必须 `cloud_upload_confirmed=true`。 |

`local-command`、自定义 shell 参数、自动 CUDA 探测、模型/权重安装下载、浏览器、
provider SDK、HTTP 调用和 token/密钥管理都不属于 v0.6。用户可以自己运行本地
CUDA，但其唯一回接方式是创建一个新的 result pack。

### 26.4 命令与工件

```text
validate-generation-request
prepare-generation
validate-generation-plan
validate-generation-plan-review
<外部执行，CLI 不参与>
propose-generation-results
validate-generation-results-proposal
validate-generation-results-review
assemble-generation-pack
propose-assets -> validate-asset-review -> freeze-assets -> render
```

`prepare-generation` 发布：

```text
generation-plan/
├── generation-plan.json
├── generation-plan-review.template.json
└── generation-input-contact-sheet.png
```

`propose-generation-results` 发布：

```text
generation-results-proposal/
├── generation-results-proposal.json
├── generation-results-review.template.json
└── generation-results-contact-sheet.png
```

`assemble-generation-pack` 只发布 exact-slot 媒体，不写任何 JSON、prompt、日志、
报告、provenance sidecar、私有控制器声明或凭据。它的目标是能安全交给 v0.5
strict asset scanner，而不是替代 Asset Manifest。

### 26.5 双重人工审核

**Plan Review** 逐槽确认：输入参考与目标槽位、保留/替换/删除意图、执行模式、
privacy profile、`adapter_id`/`adapter_version`、可选 `controller_label`、所有
相关权利；如果是云端，必须确认 `cloud_upload_confirmed=true`。确认权利 flag
不等于确认上传。

**Result Review** 逐槽确认：

- 模特身份、面部/发型、身材比例、姿态、构图和边缘；
- 服装的颜色、版型、领口、袖口、长度、主要印花、Logo/文字；
- 商品真实来源、可读标识和未被替换；
- 背景、道具与要求保留/删除的画面元素；
- 手、腿、头发、反光、透明区域和生成 artifact；
- render-ready 性和结果字节的处理权利。

联系表、文件 hash、尺寸、解码和 JSON schema 只提供技术证据；它们不证明“这
就是同一模特”“衣服准确”“logo 无误”或“没有平台残留”。这些结论必须由
controller_current 和人工确认。

### 26.6 组装与 v0.5 交接

当两份 Review 均通过，组装器重新检查绑定关系并发布纯媒体包：

- 静态图片应用 EXIF orientation，并重编码为不带来源 metadata 的 PNG；
- 仅被批准的 reference pack audio passthrough 按字节透传；
- 保留与 Template slot 兼容的 exact filename stem；
- 拒绝 sidecar、未知文件、动画/视频（合同未允许时）、目录、链接、reparse point
  和越界路径；
- 任一失败不留下部分 output pack。

这个包依然是可变用户数据。随后一定要执行 v0.5 `propose-assets`、审核
asset contact sheet、`freeze-assets` 和 renderer hash binding。v0.5 才把当前
bytes 复制成 Asset Manifest 0.2.0 的 opaque snapshot。

### 26.7 隐私、权利与公开仓库规则

Generation Request、reference/result pack、prompt、联系表、外部控制器输出、
私有审核记录、下载权重和凭据全部属于项目数据，不得进入 Git。`.gitignore` 必须
覆盖这些目录/文件名，但忽略规则不是权限控制。公开 CLI JSON 不应回显私有控制器
声明、绝对路径、URL、prompt 或凭据。

`controller-cloud` 只表示审批者同意由外部控制器上传最小批准素材。它不证明
控制器实际没有额外上传、不保证 provider 的删除/保留承诺、不替代模型/权重/
训练数据许可审查，也不让 CLI 获得上传权限。高敏感项目应选择可信的 local-only
执行环境或增加独立的签名、访问控制和 provider 审计。

### 26.8 验收与下一步边界

v0.6 的完成标准是：一条已审核 Template IR 可以从用户提供的参考资产经过两份
审核，得到 metadata-clean、纯媒体、exact-slot 的 pack，并继续由 v0.5 冻结后
渲染；任意被拒槽位可单独重试且不污染已批准证据。它不承诺具体图像模型质量、
速度、成本、商用资格或 API 可用性。

后续若要把某个本地 CUDA 试衣模型或云 provider 做成真正 adapter，必须另开版本
并先冻结：许可证/权重来源、安装与哈希、GPU 资源、网络与密钥隔离、最小上传、
删除/保留、种子/版本、故障恢复、独立评测集、身份/服装/Logo 指标及人工验收。
在这些条件具备前，保持 v0.6 的 file-drop/controller bridge 边界。
