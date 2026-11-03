# 按应用捕获视频与会议音频：扩展实施计划

> 日期：2026-10-31  
> 状态：阶段 B–C 已实现；阶段 D 的权限与 Chrome 本地音频全链路已通过，QuickTime、真实浏览器视频/会议及长会话验收进行中  
> 前置条件：现有麦克风、BlackHole、WhisperLiveKit、Ollama、SQLite 和双语界面继续作为基线，不在本扩展中替换。

## 0. 当前实施状态

已落地 `native/AppAudioHelper/`、`app/audio_capture.py`、来源数据迁移、权限/应用列表 API、WebSocket 原生 PCM 输入，以及 React 来源选择、权限引导、音量和捕获状态。模拟 helper 集成测试、17 项后端测试、Ruff、前端生产构建和 Swift PCM 自检均通过；回归覆盖 full 快照累计重复、暂停零帧和停止排空。

阶段 A 中 helper 构建、权限命令、应用枚举、单应用 PCM 和受控 440 Hz 隔离验证已有真实结果；真实会议与完整浏览器视频尚未验收。实际实现使用系统回调提供的 16 kHz 单声道 Float32 PCM，再转换为 little-endian Int16，没有另加 AVAudioConverter 重采样层；Python 直接读取并转发 stdout，没有额外的无限音频队列。

实际启动顺序为 ASR ready 后启动 helper，收到首个真实 PCM 块才发送 `capture_ready`。阶段 B、C 的代码接入已完成，但输出设备变化、休眠唤醒和 30～60 分钟长会话仍属于阶段 D 待验收项。

累计重复问题已修复：WhisperLiveKit full 快照只提交已完成的 speech lines，最后一条仍可能增长的 speech line 保留为临时文本，并在完成或停止时落盘。修复后的 30 秒真实回放从旧实现的 19 条累计片段降为 2 个自然分段，2 条译文全部完成。

当前机器的 TCC 权限已通过并在后端重启后保持。Chrome 播放本地 MP3 的应用选择、捕获、暂停/恢复、停止、翻译和 DOCX 导出已完整通过；QuickTime 持续捕获仍在后端和直接 helper 路径出现 ScreenCaptureKit 连接中断。真实浏览器视频、Zoom/Teams/腾讯会议、输出设备变化、休眠唤醒和 30～60 分钟长会话仍未验收。详细数据见 [验证记录](VALIDATION.md)。

## 1. 目标与用户场景

在现有“声音来源”中增加 **应用音频** 模式，让用户从当前运行的应用中选择目标，例如：

- 正在播放课程或视频的播放器。
- Zoom、Teams、腾讯会议等会议应用。
- 正在播放视频或会议的 Safari、Chrome、Edge。

选择后，不修改 macOS 默认输出设备，也不要求配置 BlackHole；应用只把目标 App 的声音送入现有本地听写翻译流水线。其他应用的提示音和音乐不应进入记录。

### 首版明确支持

- macOS 13 或更高版本。
- 每次选择一个运行中的应用。
- 开始、暂停、继续、停止和尾段排空。
- 目标应用退出、权限拒绝、无声音和捕获异常的可读提示。
- 与麦克风模式共用现有原文、译文、历史、Markdown/TXT/DOCX 导出。

### 首版不承诺

- 不区分同一浏览器中的不同标签页；选择浏览器时，捕获的是该浏览器应用产生的音频。
- 不同时选择多个应用，不自动识别会议开始。
- 不同时录制麦克风；会议双方“我 / 对方”双通道作为后续增强。
- 不绕过 DRM、系统隐私保护或应用自身的防录制限制。
- 不保存视频画面，不默认保存原始音频，不引入云捕获或云翻译。

## 2. 推荐方案

增加一个独立的 **Swift 原生音频捕获辅助进程**，使用 ScreenCaptureKit 按应用过滤音频，再把统一格式的 PCM 交给 FastAPI。

```text
React：麦克风 / 应用音频 / BlackHole
                    │ 选择运行中的 App
                    ▼
FastAPI：会话配置、权限状态、原生子进程生命周期
                    │ 启动
                    ▼
Swift app-audio-helper
  ├─ SCShareableContent：列出应用
  ├─ SCContentFilter：只包含目标 App
  ├─ SCStream：capturesAudio = true
  ├─ 视频帧直接丢弃，不写入磁盘、不发送到浏览器
  └─ 转换 PCM s16le / 16 kHz / 单声道
                    │ stdout 二进制流，日志只走 stderr
                    ▼
FastAPI：有界读取、暂停静音、音量指标、异常处理
                    │ 与浏览器麦克风相同的 PCM 接口
                    ▼
WhisperLiveKit → 稳定原文 → Ollama qwen3.5:9b-mlx
                    │
                    └─ SQLite / Markdown / 聊天 / 文档 / 导出
```

### 为什么选择原生辅助进程

- 浏览器麦克风 API 不能直接捕获任意 macOS 应用音频。
- ScreenCaptureKit 能按 `SCRunningApplication` 过滤，符合“指定视频或会议 App”的目标。
- Swift 进程只依赖 macOS 系统框架，正式运行不需要增加 Node.js 服务。
- 原生进程与 Python 后端隔离，捕获崩溃不会破坏数据库或翻译状态。
- 后端继续接收现有 PCM 格式，Whisper、Ollama、存储和导出无需重写。

BlackHole 保留为兼容和回退方案，不删除现有功能。

## 3. 原生辅助进程设计

建议目录：`native/AppAudioHelper/`，使用 Swift Package Manager 构建 executable。

### 3.1 命令边界

规划三个只读/捕获命令，正式参数在实现时确定：

```text
app-audio-helper permissions --json
app-audio-helper list-apps --json
app-audio-helper capture --bundle-id <id> --pid <pid>
```

- `permissions`：返回 Screen Recording / System Audio Recording 状态和是否可请求权限。
- `list-apps`：返回应用名称、bundle ID、PID；由后端转换为前端选择项。
- `capture`：捕获模式 stdout 只输出二进制 PCM，诊断信息只写 stderr，避免污染音频流。
- 后端使用参数数组启动进程，不经过 shell，不接受前端传入任意可执行路径或命令。

### 3.2 按应用过滤

- 使用 `SCShareableContent` 获取运行应用和显示器。
- 用 bundle ID 选择应用，同时记录 PID，避免同一应用多实例或应用重启后误选。
- 使用包含目标应用的 `SCContentFilter` 创建 `SCStream`。
- 设置 `SCStreamConfiguration.capturesAudio = true`。
- 视频帧率和分辨率使用系统允许的较低配置；收到的 `.screen` 样本直接丢弃。
- 不把“列出权限成功”当作“音频捕获成功”，必须等到实际音频回调或明确错误后再报告 ready。

### 3.3 音频转换与背压

- 原生侧接收系统提供的音频格式，转换为 signed 16-bit little-endian、16 kHz、单声道。
- 优先使用 `AVAudioConverter` 做重采样和声道下混，不用简单每隔几个样本抽值。
- stdout 以连续二进制 PCM 输出；Python 按有界块读取并转发 WhisperLiveKit。
- 原生和 Python 队列都必须有上限，并暴露捕获滞后；持续溢出时明确报错并结束会话，不能静默丢音或无上限增长内存。
- Python 可从 PCM 块计算 RMS，向界面发送低频音量指示，帮助用户确认选中的应用正在发声。

### 3.4 生命周期

- **开始**：校验权限和目标应用仍存在，启动 helper，等待捕获 ready，再连接 ASR。
- **暂停**：保持捕获进程和 ASR 连接；丢弃目标音频块，并按相同持续时间发送零值 PCM。恢复时不能补发暂停期间积压的声音。
- **停止**：先停止 helper 和 PCM 输入，再向 ASR 发送空帧排空，等待剩余翻译和文档落盘。
- **目标应用退出**：停止捕获，保留已有原文，排空最后片段并提示“目标应用已退出”。
- **异常退出**：保存已有片段，将未完成翻译标记为可重试，记录 helper stderr 尾部。
- **输出设备变化、休眠唤醒**：重建捕获流；不能恢复时给出明确错误，而不是无限等待静音。

### 3.5 权限与签名

- 只使用 macOS 正常授权的 ScreenCaptureKit，不使用私有 API、驱动或权限绕过。
- 界面写清“系统设置 → 隐私与安全性 → 屏幕与系统音频录制”的授权路径。
- 开发期固定 helper 的路径和签名方式；验证重新构建后权限是否失效，并把需要重新授权的情况写入 README。
- 如果后续包装桌面应用，将 helper 放入稳定 bundle 并采用稳定签名，减少权限重复授权。

## 4. 后端扩展

### 4.1 新模块

建议增加 `app/audio_capture.py`：

- `AppAudioCaptureManager`：查询应用、检查权限、启动/停止 helper。
- `NativeAppAudioSource`：向实时会话提供 PCM 读取、暂停、恢复和停止接口。
- 捕获日志写到 `data/logs/app-audio.log`，不包含音频内容。
- 配置项只保留必要项，例如 `APP_AUDIO_HELPER_PATH`、捕获队列大小和启动超时。

`app/live.py` 将浏览器 PCM 和原生 PCM 适配为同一输入抽象；翻译队列、稳定片段去重和停止排空逻辑继续复用。

### 4.2 API 与会话数据

规划接口：

```text
GET /api/audio-sources/permissions
GET /api/audio-sources/applications
POST /api/sessions
WS /ws/live?session_id=...
```

创建会话的请求增加：

```json
{
  "audio_source": {
    "type": "application",
    "bundle_id": "目标应用的 bundle ID",
    "process_id": 1234,
    "display_name": "目标应用名称"
  }
}
```

- 麦克风请求保持向后兼容，缺省仍为 `microphone`。
- `sessions` 表以 additive migration 增加音频来源类型、bundle ID 和显示名称；已有历史记录默认标记为 `microphone`，不清空数据库。
- 导出文档可显示来源名称，例如“应用音频：Safari”。
- 首版一个会话只能有一个来源，开始后不能在麦克风与应用之间切换；需要切换时新建会话。

### 4.3 实时事件

在现有状态事件上补充：

- `capture_starting / capture_ready`：权限、应用和原生进程状态。
- `capture_level`：低频音量指标，不包含原始音频。
- `capture_warning`：设备变化、短时无回调、捕获积压。
- `capture_ended / capture_error`：目标应用退出、权限撤销或 helper 异常。

## 5. 前端扩展

将当前单一设备选择器改为两组来源：

1. **麦克风**：保留现有设备列表与 AudioWorklet。
2. **应用音频**：显示运行应用的名称、图标（可选）、bundle ID 和刷新按钮。

交互要求：

- 选择应用音频后，不请求浏览器麦克风权限，也不启动 `PcmRecorder`。
- 未授权时先显示权限说明和重新检查按钮，不显示假“就绪”。
- 开始捕获后显示目标应用名称和音量；目标应用退出时给出可见提示。
- 活跃会话中禁用来源切换，暂停/停止行为与麦克风一致。
- 对浏览器来源显示辅助说明：“捕获整个浏览器应用的声音，不区分标签页”。
- 移动端仍只允许选择本机可支持的来源；应用音频是 macOS 后端能力，不伪装成手机浏览器能力。

## 6. 实施顺序

### 阶段 A：最小技术验证

- 构建只含系统框架的 Swift helper。
- 完成权限检查、应用枚举、单应用过滤、PCM 转换和 stdout 输出。
- 用命令行捕获一个视频应用，同时让另一个应用播放声音，验证隔离性。
- 验证 Zoom/Teams/腾讯会议中的至少一种真实会议，以及一种浏览器视频。
- 记录权限、签名、重建后授权和系统版本行为。

**决策门：**只有确认目标 App 音频能被隔离、PCM 质量正常、权限流程可接受，才接入完整 UI。如果当前系统上的 ScreenCaptureKit 路径不达标，再评估 Core Audio process taps（macOS 14.2+）或已有 Node 原生模块，不提前更换整个应用架构。

### 阶段 B：后端接入

- 实现 helper 生命周期、应用列表 API、PCM 输入抽象和数据库迁移。
- 接入暂停零帧、停止排空、目标应用退出和错误日志。
- 用模拟 helper 完成不依赖权限的自动化测试。

### 阶段 C：界面接入

- 实现来源切换、应用选择、权限引导、音量和捕获状态。
- 保持聊天、文档、历史和导出完全复用。
- 完成生产构建和桌面/窄屏布局检查。

### 阶段 D：真实验收与文档

- 执行下面的真实应用矩阵和长时间会话。
- README 增加构建要求、权限、使用步骤、浏览器标签页限制和故障排查。
- 验证记录写明 macOS 版本、测试应用、延迟和未通过项目；未实测的应用不宣称全部兼容。

### 后续独立增强：会议双方双通道

- macOS 15+ 可评估 ScreenCaptureKit 麦克风捕获，或复用浏览器/Core Audio 麦克风。
- “应用声音 = 对方”和“麦克风 = 我”使用两个独立音轨与时间戳，不先做简单混音。
- 数据库片段增加通道/角色，界面显示“我 / 对方”，导出保留角色。
- 处理扬声器回声、耳机建议、独立暂停和回声消除；这部分不与首版按应用捕获同时交付。

## 7. 测试与验收标准

### 自动化测试

- PCM 重采样、立体声下混、字节对齐和队列上限。
- 模拟 helper 输出音频、退出、报错和持续无回调。
- 暂停期间只发送零值 PCM，恢复时不补发旧音频。
- 停止时 ASR 和翻译队列完整排空。
- 旧数据库迁移后历史会话不变。
- 应用音频模式不请求浏览器麦克风；现有麦克风测试全部回归。

### 真实应用验收矩阵

| 场景 | 必须验证 |
|---|---|
| 视频 App + 另一个发声 App | 记录只包含目标视频声音，其他应用声音不进入 |
| 真实会议远端发言 | 远端声音持续听写和翻译，已有记录可导出 |
| 浏览器视频/会议 | 明确验证整个浏览器捕获行为，不误称按标签页隔离 |
| 权限拒绝或撤销 | 界面显示可操作错误，不出现“成功但一直静音” |
| 暂停/继续 | 暂停内容不记录，恢复无突发积压 |
| 停止 | 最后一段原文和剩余翻译完整落盘 |
| 目标 App 退出/重启 | 会话安全结束并提示；不误连到同名新进程 |
| 输出设备切换、休眠唤醒 | 能恢复或明确失败，不无限产生假静音 |
| 30～60 分钟会话 | 内存和队列不持续增长，文档与 UI 一致 |
| 麦克风与 BlackHole 回归 | 原有来源行为不受影响 |

### 首版完成定义

- 用户无需修改系统输出设备即可选择一个 App 并开始翻译。
- 其他 App 的数字音频不会进入目标会话；同应用多标签页/窗口的限制有明确说明。
- 捕获新增延迟、长会话资源和真实兼容性均有实测记录。
- 不保存画面，不默认保存音频，服务仍只监听 `127.0.0.1`。
- 所有异常路径都保留已听写原文，并允许重试未完成翻译。

## 8. 主要风险与应对

1. **权限授予到错误的终端或路径**：固定 helper 路径与签名，使用独立权限检查命令，做真实授权测试。
2. **只有屏幕权限提示，用户担心隐私**：界面解释为什么音频捕获需要该权限；原生层丢弃画面，并提供不保存画面的代码审查点。
3. **浏览器标签页不能隔离**：首版明确按应用，不承诺按标签页；单标签页需求另行使用浏览器标签页音频捕获方案。
4. **DRM 或受保护内容静音**：如实提示系统限制，不尝试绕过。
5. **应用重启导致 PID 变化**：首版安全结束会话，由用户重新选择，不自动捕获未知新进程。
6. **采样率和输出设备变化**：统一经过 `AVAudioConverter`，监听失败并重建或明确退出。
7. **第三方原生依赖成熟度**：首版只依赖 Apple 系统框架；开源实现先审代码和许可证，不把低成熟度模块直接变成核心运行时依赖。

## 9. 开源参考与复用边界

- [Apple：Take ScreenCaptureKit to the next level](https://developer.apple.com/videos/play/wwdc2022/10155/)：内容过滤和音频捕获的官方 API 参考。
- [Apple ScreenCaptureKit 文档](https://developer.apple.com/documentation/screencapturekit)：系统能力与 API 以官方文档为准。
- [MrLionware/screencapturekit-audio-capture](https://github.com/MrLionware/screencapturekit-audio-capture)：MIT，提供按应用捕获、Int16 单声道和多进程服务，可用于阶段 A 对照验证；当前采用者与验证证据较少，暂不直接作为生产依赖。
- [contorch/meeting-capture](https://github.com/contorch/meeting-capture)：Apache-2.0，其 Swift `sysaudio`、stdout PCM、权限诊断和后续双通道设计可作参考；其云转写和常驻会议监控不属于本项目复用范围。

如复制任何开源代码，保留对应 LICENSE 和必要 NOTICE；未实际复制时只记录架构参考。

## 10. 默认实施决策

- 首版采用 **Swift helper + ScreenCaptureKit + stdout PCM**，不增加常驻 Node 服务。
- 先交付“选择一个 App 的声音”，再做“我 + 对方”双通道。
- 不替换 WhisperLiveKit、Ollama 9B、SQLite、React 或现有导出模块。
- 不把计划中的 API、权限或应用兼容性提前写成 README 已实现功能。
