# Codex Reference Video Rebuilder 完整设计方案

版本：0.2.0-alpha
日期：2026-08-22  
目标仓库：`LYCMYT/reference-video-rebuilder`
Skill 名称：`rebuild-reference-video`

当前实现状态：本地 Alpha 已具备 FFmpeg/ffprobe 媒体探测、受限参考调查、Template IR/资产合同校验、S1 确定性合成和逐输出技术 QA。参考视频的语义槽位判定、换装/模特图生成、残留平台元素的人眼审查仍由 Codex/人工完成；本版本不承诺任意视频或被遮挡像素的复原。

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

### 4.1 Compile：新参考视频编译模式

适用于第一次上传的参考视频。

流程：探测媒体 → 抽帧和音频分析 → 识别镜头和覆盖层 → 估计人物、物体和镜头运动 → 提出槽位 → 评估支持等级 → 人工确认低置信度项 → 生成 Template IR → 渲染结构预览 → 冻结模板版本。

Compile 模式的产物必须是可重复使用的模板，而不是一次性脚本。

### 4.2 Remix：已审核模板复用模式

适用于已经冻结的 Template IR。

流程：校验素材合同 → 显式映射槽位 → 生成或标准化替换资产 → 关键帧审核 → 低清预览 → 正式渲染 → QA → 打包。

Remix 模式的验收要求是：更换第二组完整素材时不修改程序，只修改资产和映射文件即可生成。

## 5. 支持等级

| 等级 | 视频特征 | 自动化承诺 | 失败策略 |
|---|---|---|---|
| S1 确定性模板 | 单主体、固定镜头、简单背景、规律硬切、2D 叠加、轻微动作 | 高；布局和切点可以逐帧重建 | 自动生成模板，低置信度槽位仍需确认 |
| S2 跟踪合成 | 单主体中等运动、缓慢运镜、可跟踪遮挡、动态背景 | 中；结构和运动可保持，需动态蒙版 | 请求修正关键帧、轨迹或蒙版 |
| S3 生成式修改 | 快速运动、转身、复杂衣服动态、强运镜、较大遮挡 | 低到中；只保证整体效果和节奏相似 | 分段生成、局部重试、明确实验性 |
| S4 不支持精确模式 | 多人紧密交互、镜面、透明物、严重遮挡、极快混剪、输入损坏 | 不承诺 | 输出分析报告并建议拆分、简化或人工模板 |

分类必须保守：错误地拒绝精确模式优于静默输出错误商品、错误人物或残留平台标识。

## 6. 总体架构

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
  "support": {"level": "S1", "confidence": 0.96, "warnings": []},
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

所有自动推断槽位必须包含：

- `confidence`：0 到 1；
- `evidence`：关键帧、框、蒙版或检测来源；
- `review_required`；
- `reason`；
- `allowed_processors`。

低于阈值时不得静默进入正式渲染。

## 8. 完整工作流

### 8.1 Preflight

1. 检查 FFmpeg/ffprobe、Python、Node 和可选 GPU。
2. 检查磁盘空间、可写目录和输入可读性。
3. 检查 VFR、HDR、旋转元数据、损坏帧和音轨。
4. 确定 `local-only` 或 `cloud-assisted`。
5. 确认媒体、肖像、商标和音频权限。
6. 建立项目 ID，复制或链接素材到项目隔离目录。

### 8.2 Analyze

1. 输出媒体探测 JSON。
2. 生成低频 survey、镜头关键帧和联系表。
3. 识别镜头、硬切、淡变、速度变化和重复帧。
4. 分析音频节拍、响度、静音和波形。
5. OCR 检测字幕、弹幕、账号、平台 UI 和水印候选。
6. 检测主体、服装、商品、Logo、背景和道具候选。
7. 估计主体姿态、轨迹、缩放、遮挡和镜头运动。
8. 区分持久 UI、创意内容和未知区域。
9. 输出机器 JSON、联系表、异常帧，不向 Codex返回逐帧大日志。

### 8.3 Classify

根据镜头数量、主体数量、动作速度、相机运动、遮挡、反射、透明度、UI 遮挡和输入质量计算支持等级。超出等级时给出可完成的降级范围。

### 8.4 Propose and confirm slots

生成槽位候选表，包含时间范围、图层、处理方式和置信度。平台元素只能成为删除层，不能成为待复刻槽位。用户只需确认低置信度或会显著改变结果的决策。

### 8.5 Build and freeze template

将确认结果编译为 Template IR，渲染带边框和槽位编号的结构调试版。通过后生成不可变模板版本，例如 `outfit-reel-001@1.0.0`。

### 8.6 Validate replacement assets

- 检查必需槽位是否齐全；
- 检查数量、编号和映射重复；
- 检查像素、主体面积、透明度、压缩和清晰度；
- 检查服装图属于平铺、人台、真人上身或截图；
- 检查背景、Logo、文字安全区和音频时长；
- 不默认生成笛卡尔积，所有映射必须显式。

### 8.7 Prepare derived assets

根据输入类型选择 adapter：直接贴图、抠图、对齐、虚拟试衣、受控图像生成、背景补全、颜色匹配、音频裁切或循环。当前固定动作视频优先生成定姿穿搭图，不生成整段 AI 视频。

### 8.8 Review looks

先输出人物和服装联系表，检查身份、身材、姿态、服装版型、颜色、图案、Logo、手部和异常肢体。未批准的单套只重试该槽位。

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

统一接口：

```text
prepare_identity(input, policy) -> identity_asset
prepare_outfit(identity, garment, pose, policy) -> look_asset
prepare_product(input, policy) -> product_asset
prepare_background(input, geometry, policy) -> background_asset
modify_video(segment, references, policy) -> video_asset
```

每个 adapter 声明：输入类型、是否云端、许可证、成本估计、GPU 要求、可重复参数、内容限制和质量指标。

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

`0.2.0-alpha` 已提供以下稳定 JSON CLI；Codex 不应依赖自然语言日志：

```text
video-remix doctor [--ffmpeg <path>] [--ffprobe <path>] --json
video-remix probe <reference> [--ffmpeg <path>] [--ffprobe <path>] --json
video-remix survey <reference> --project-root <project-dir> [--output-dir reference-survey] [--frame <n> ...] [--samples <n>] [--ffmpeg <path>] --json
video-remix validate-template <template.ir.json> --json
video-remix validate-assets <template.ir.json> <assets.json> --project-root <project-dir> --json
video-remix render <template.ir.json> <assets.json> --project-root <project-dir> [--frame-directory render/master-frames] [--debug-bounds] [--summary <root-contained.json>] [--ffmpeg <path>] --json
video-remix qa <delivery.mp4> [--width <n>] [--height <n>] [--fps <n>] [--frames <n>] [--expect-audio|--expect-no-audio] [--ffmpeg <path>] --json
```

Alpha 通用规则：

- 标准输出返回稳定 JSON 摘要；`survey` 和可选 `render --summary` 才写受 project root 约束的工件；
- 运行错误返回有界错误码；技术 QA 不通过返回码为 `1`，运行错误为 `2`；
- `render` 写入任何帧前必须完成模板与文件资产校验，之后必须对每个输出执行 QA；
- 禁止 shell 字符串拼接执行 FFmpeg，使用参数数组；
- 不自动分类语义槽位、不自动生成换装资产，也不把技术解码 QA 误称为视觉/权利验收。

后续可增加 `init`、`classify`、`propose-slots`、`freeze-template`、`prepare-assets`、`preview`、`package`、`status`、断点恢复和本地 MCP；这些不是当前 Alpha 命令。

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

源素材默认保持在项目隔离目录，不进入 Skill 目录和 Git 历史。

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

结合 OCR、已知平台区域检测、联系表和全片人工审查：

- 无平台 Logo；
- 无点赞、评论、分享栏；
- 无账号和标题文字；
- 无弹幕；
- 无手机状态栏和导航栏；
- 无明显涂抹块、残影或错误补全。

### 13.5 QA 结果

每项状态只能是 `pass`、`warn` 或 `fail`。任何 `fail` 阻止打包；需要人工接受的 `warn` 必须记录批准信息。

## 14. 失败、降级和人工关卡

必须设置以下关卡：

- 不支持等级确认；
- 低置信度槽位确认；
- 云端上传许可；
- 人物和服装联系表批准；
- 正式高清渲染前预览批准；
- 最终残留标识人工审查。

局部错误只重跑受影响槽位或帧段。遇到显存不足时依次降低 batch、分辨率、模型规模或转为批准的云端 adapter；不得静默换模型导致风格变化。

## 15. 隐私、安全与权利

- 默认本地项目隔离和最小路径白名单；
- 不读取任务范围外文件；
- 不在日志中输出人脸图、访问令牌或完整私人路径；
- 云端 adapter 默认关闭，逐项目授权；
- 记录上传了什么、传给哪个 provider、何时删除；
- 不把源视频、用户模特、服装或音乐提交 Git；
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

- `local-only`：严格离线；只使用已安装的本地处理器，生成质量受硬件限制；
- `cloud-assisted`：分析、素材、状态、渲染均在本机，只有明确批准的派生资产发送至生成 provider；
- `gpu-worker`：未来连接用户自有 NVIDIA 工作站，但通过同一 adapter 协议调用。

## 18. 测试与评测体系

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

### Phase 3：同类型模板编译器

- 自动发现固定镜头换装类视频；
- 自动检测换装切点和商品滚轮；
- 支持不同槽位数量、位置和时长；
- 生成用户确认的 Template IR。

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

采用“当前主模型负责判断，Terra负责受约束执行，确定性工具负责证明”的三层体系。降低模型成本只能发生在已经冻结、可测试、可回滚的任务上，不能降低人物一致性、服装准确度、平台标识清理或最终视频验收标准。

“当前主模型”是运行时正在主持项目的高能力模型，不在 Skill 中写死具体名称。每次运行必须记录实际模型。`gpt-5.6-terra` 的 `max` 是 reasoning effort，不是另一个模型名称。

编排层使用逻辑配置名而不是在业务代码中散落模型名：`controller_current`（当前主模型）、`builder_quality`（Terra Max）、`builder_standard`（Terra High）和 `mechanical_worker`（通过同类评测后才可启用的较低配置）。新任务族默认从 `builder_quality` 起步。

### 20.2 任务路由矩阵

| 工作 | 默认模型/执行者 | 是否允许降级 | 强制复核 |
|---|---|---|---|
| 用户意图、未知条件、产品边界 | 当前主模型 | 否 | 用户确认重大歧义 |
| 参考视频语义、S1–S4分类、槽位推断 | 当前主模型 | 否 | 关键帧证据和人工确认低置信度项 |
| 删除、保留和替换边界 | 当前主模型 | 否 | 最终残留检查 |
| Template IR 架构或不兼容 Schema 变更 | 当前主模型主导；Terra Max可实现 | 否 | 当前主模型审查 + Schema测试 |
| 已冻结接口的非平凡代码实现 | `gpt-5.6-terra` + `max` | 通过评测后可降至 high/xhigh | 测试 + 当前主模型代码审查 |
| Remotion组件、FFmpeg参数生成 | Terra Max | 仅限已有模板族 | 帧级测试 + 预览审查 |
| 单元测试、fixture、重复adapter骨架 | Terra high/medium | 可以 | CI和覆盖率要求 |
| 文件清单、JSON机械转换、日志摘要 | Terra较低配置或更低成本模型 | 可以 | Schema或哈希检查 |
| 模特身份、服装、商品准确度判断 | 当前主模型 + 人工 | 否 | 联系表和最终视频 |
| 隐私、路径安全、授权、许可证和云上传 | 当前主模型 | 否 | 安全测试和人工批准 |
| 最终成片验收和发布判断 | 当前主模型 + 人工 | 否 | 所有自动QA通过 |

### 20.3 下放前合同

主模型在把任务交给 Terra Max 或更低模型前，必须冻结：

- 目标、非目标和允许修改的文件；
- 输入输出 Schema；
- 不变量、禁改项和风险；
- 必须执行的测试；
- 视觉和人工验收标准；
- 失败与升级条件。

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

### 1.0.0

- 定义清楚的 S1 支持域；
- 至少20条参考视频回归；
- 自动模板编译成功率和误拒绝率达到设定阈值；
- 安全、隐私、许可证和错误处理完成审核；
- 版本迁移和兼容策略确定。

## 22. 已知未知与未知的未知

### 22.1 已知未知

- “本机操作”是否允许经过批准的云端生成；
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

项目应以自研 `rebuild-reference-video` Skill 为核心，而不是 fork 某一个现有视频仓库。官方 Remotion Skills 和 FFmpeg 作为外部基础层；分析、Template IR、槽位映射、生成路由、平台元素排除、身份/服装 QA、运行状态和可重复模板必须自行实现。

产品战略必须坚持“先模板、后编译器、再通用化”：先让当前视频和第二组素材达到可靠复用，再扩展同类型模板族，最后进入 S2 跟踪和生成式视频。这样第一阶段能产出真实可用结果，同时保留向通用 Skill 扩展的架构。
