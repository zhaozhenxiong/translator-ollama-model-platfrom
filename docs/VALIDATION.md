# 验证记录

日期：2026-10-31  
设备：MacBook Pro / Apple M3 Max / 36 GB 统一内存  
系统与工具：macOS 27.2、Swift 6.2 Command Line Tools、Python 3.12.11、Node.js 22.22.0、Ollama 0.40.13

本文区分自动化测试、真实模型链路、原生捕获技术验证和尚未通过的真实应用验收，不把模拟 helper 或本地音频播放测试当作会议兼容性证明。

## 本轮自动化回归

- Ruff：`app/`、`scripts/`、`tests/` 全部通过。从父目录用绝对路径调用会把 `app` 的导入分组误分类；从项目根目录执行为 `All checks passed!`。
- Pytest：`35 passed`。
- React TypeScript 与 Vite 生产构建通过，1586 个模块完成转换。
- Swift release 构建和 ad-hoc 签名成功，内置 PCM 自检通过：
  - clipping 与 little-endian Int16 字节值。
  - 空输入产生零字节。
- 375 × 812 移动视口实测 `documentWidth = 375`、`bodyWidth = 375`，1280 × 900 桌面视口实测 `documentWidth = 1280`，均无横向溢出；检查后已恢复桌面视口。

35 项后端测试覆盖 SQLite、导出、Ollama NDJSON、CC Switch SSE、稳定片段处理、模拟实时 WebSocket、会话删除、遗留状态清理和模型快照，以及应用音频扩展的以下行为：

- fake helper 权限和应用列表 JSON。
- PCM 读取及子进程停止生命周期。
- 缺少应用 bundle ID / PID 时拒绝启动。
- 应用来源数据库 round trip；旧数据库迁移后默认麦克风。
- 暂停期间向 ASR 发送零值 PCM，恢复时不补发旧音频。
- 停止时排空 ASR 和翻译；helper 出错后仍保留已有原文和译文。
- WhisperLiveKit full 快照重复发送历史时，不产生累计前缀片段；增长中的末行只在完成或停止时提交。
- CC Switch 初始为上游未验证，真实 401/403 后为未授权，修复后成功重试可转为就绪；路由在线与上游推理状态分开。
- 会话创建保存 provider/model，实时翻译和手动重试使用同一快照；安全目录不包含路由地址或本地凭证。
- 删除返回 204/404，进行中会话返回 409；数据库级联与 Markdown 同步清理，镜像删除失败时数据库保持不变。

## 累计重复修复与真实 Whisper + Ollama 回放

问题根因是 WhisperLiveKit `mode=full` 每次发送整份 `lines` 快照，而不是只发送新增句子。旧逻辑把文本不断增长的同一行当作新片段，导致一句话的多个累计前缀被重复保存和翻译。

修复后只提交快照中已经完成的 speech lines；最后一个仍可能增长的 speech line 与 `buffer_transcription` 只用于临时显示，并在该行完成、`ready_to_stop` 或正常 ASR 流结束时提交。修复不是简单按字符串去重，也不会把每个新增后缀拆成独立片段。

使用现有中文音频，通过运行中的 REST + WebSocket 应用执行 30 秒实时回放：

- ASR：WhisperLiveKit / `mlx-whisper` / `large-v3-turbo` / LocalAgreement。
- 翻译：Ollama / `qwen3.5:9b-mlx` / `think: false`。
- 修复后会话 ID：`79228c2c9a774f73a7c9cf53e5c8490e`。
- 最终会话状态：`completed`。
- 稳定原文：2 个自然分段；完成译文：2 段；失败翻译：0；无累计前缀重复。
- 从创建会话到停止、尾段听写和翻译全部完成：49.84 秒。
- 自动 Markdown 已逐段核对，实际 DOCX 导出为 37,484 bytes，ZIP 结构及全部原文、译文均已验证。
- 修复前同类回放曾产生 19 条累计片段；旧会话 `29086110ffcb404596d233415f4e3885` 仅作为缺陷对照，不再代表当前行为。

此前测试脚本停止时调用了不存在的 `websockets.ClientConnection.send_json()`，改为发送 JSON 字符串后通过；该问题位于测试脚本，不是应用停止流程。

本次会话删除与 CC Switch 功能修改后，又使用同一 MP3 做了 30 秒默认 Ollama 真实回归：

- 会话 ID：`b78bb5e5fc8a419d8135438043697659`，来源和模型快照为 `ollama / qwen3.5:9b-mlx`。
- 最终状态 `completed`；稳定原文 1 个自然分段，完成译文 1 段，失败翻译 0，无累计前缀重复。
- 从创建到停止、尾段听写和翻译完成共 51.01 秒。
- 自动 Markdown 为 1,092 bytes；实际 DOCX 导出为 37,528 bytes，已核对全部原文、译文以及 `Ollama / qwen3.5:9b-mlx` 元数据。

这些结果证明真实模型、重复修复、停止排空、存储和导出链路可用，但不是识别准确率或延迟基准。样本中的专有名词仍有误识别，不能据此宣称翻译内容完全准确。

## 会话删除与 CC Switch 来源验收

删除按钮已在真实浏览器中分别验证取消和确认：

- 取消确认时，目标测试会话、全部记录和会话总数保持不变。
- 确认永久删除 `7ead8d5ac910442ea8e961b21386fb08` 后，列表和数据库会话数均从 11 变为 10；目标会话 API 返回 404，数据库会话及片段均不存在，对应 Markdown 已删除，其余 10 个会话保留。
- 进行中的会话 409、重复删除 404、片段级联和镜像失败保护由集成测试覆盖。

2026-11-03 补充遗留状态修复验收：会话 `9583dc1752484b6abf7b5940812823b0` 因异常退出保留 `active`、无结束时间且片段为 0。应用重启后自动转为 `interrupted` 并写入结束时间，删除按钮恢复可用；在真实浏览器中确认删除后返回 204，再次查询为 404，对应 Markdown 已清除。真实活跃连接仍由后端 409 保护。

CC Switch 真实授权链路尚未通过，不能宣称路由模型翻译成功：

- 新启动的应用只探测到本机路由在线时，`/api/health` 返回 `unverified`，界面显示“翻译上游未验证”，没有将其误报为上游就绪。
- 使用非敏感文本 `Hello from the local authorization check.` 创建临时 CC Switch 会话并执行真实手动重试，返回 HTTP 502；错误明确指向当前 Provider 授权，没有转发上游响应体或复制 Codex 令牌。
- 重试后健康状态为 `unauthorized`、`router_available = true`，界面显示“翻译上游未授权”；安全目录仍允许再次尝试，修复授权后不会被缓存状态永久阻止。
- 原文完整保留，片段标记为失败；临时会话 `8d6a9230118e44c8b62318238ac6cd16` 随后通过 API 删除，返回 204，Markdown 同步清除。
- 默认 Ollama 的真实完整链路不受此授权问题影响，结果见上一节。

## Ollama 单句冒烟

默认 `qwen3.5:9b-mlx` 已安装并由完整应用识别为 `ready`，请求均设置 `think: false`：

| 场景 | 输入 | 首 token | 总耗时 | 结果 |
|---|---|---:|---:|---|
| 首次验证请求 | `你好，本地实时翻译已经切换到这个模型。` | 2,332 ms | 2,599 ms | `Hello, local real-time translation has switched to this model.` |
| 紧接着第二次请求 | `The local translator is now running with the nine billion parameter model.` | 292 ms | 570 ms | `本地翻译器现已使用九十亿参数模型运行。` |

此前使用已有 `qwen3.8:27b-mlx` 的对照结果：

| 场景 | 输入 | 首 token | 总耗时 | 结果 |
|---|---|---:|---:|---|
| 首次请求，包含模型加载 | `Hello, how are you today?` | 9,570 ms | 9,753 ms | `你好，你今天怎么样？` |
| 热加载 | `Please translate this short sentence into natural Chinese.` | 1,058 ms | 1,382 ms | `请将这个短句翻译成自然的中文。` |

这些是单句冒烟结果，用于验证 API 和基本延迟，不代表长句或持续会话的 P50/P95 性能。

## 原生按应用音频技术验证

Swift helper 已实现 `self-test`、`permissions`、`list-apps` 和 `capture`，使用 ScreenCaptureKit 按应用过滤，并输出 PCM signed 16-bit little-endian / 16 kHz / 单声道。stdout 只承载 PCM，诊断只写 stderr；视频样本直接丢弃。

已验证：

- release 构建、固定 identifier 的 ad-hoc 签名和 PCM 自检通过。
- 可枚举真实运行应用；多个 Chrome 实例能通过 PID 区分。
- 使用 Preview 播放受控 440 Hz 测试音时，捕获约 4.78 秒 PCM，RMS 926.17，估算频率 440.2 Hz；stderr 确认为 `16000 Hz, mono, s16le`。
- 随后用 Chrome 捕获同类测试音期间，约 4.78 秒 PCM 的 RMS 为 0，非零样本数为 0。
- 直接通过 release helper 连续捕获 Chrome 音频 110 秒，进程保持运行，首个音频块为 `16000 Hz, mono, s16le`，未发生连接中断。

两个应用的受控音测试是顺序捕获得到的隔离证据。第一次并发双 helper 尝试出现连接中断且未进入 ready，不能把该次结果解释为隔离成功或失败；这些结果也不等同于真实视频或会议兼容性测试。

## 真实应用内权限与 Chrome 全链路验收

macOS TCC 启动宿主授权已完成。直接执行 helper 和通过 FastAPI 查询均返回 `screen_recording_granted = true`，后端重启后权限仍保持，应用列表和真实 App 选择可用。未使用私有 TCC API、权限数据库修改或授权绕过。

使用隔离 Chrome profile，在本地 HTML 页面的 `<audio>` 元素中播放与回放测试相同的 MP3，经独立后端端口完成真实应用音频全链路。最终干净重跑的脚本退出码为 0：

- 会话 ID：`f4d8065696714c199d79fb27772f59d7`。
- 最终状态：`completed`；稳定原文 2 段，完成译文 2 段，失败翻译 0。
- 无累计前缀重复，`capture_errors = []`。
- 最大归一化捕获 RMS：`0.369133`，确认不是全程静音。
- 收到首个真实 PCM 块后才进入 `capture_ready`；暂停、恢复、停止和尾段排空均实际执行。
- 应用暂停期间 Chrome 播放时间继续前进 5.0 秒，验证测试确实覆盖了“目标 App 继续发声而记录暂停”的路径。
- 实际 DOCX 导出为 37,422 bytes；已检查“应用音频：Google Chrome（隔离测试）”来源标签以及全部原文和译文。
- 从会话创建到听写、翻译和导出检查完成：54.91 秒。

第一次 Chrome 全链路会话 `d27c4a8e406548af84517f50f8204741` 的所有功能断言也已通过，但临时脚本在清理阶段重复暂停已关闭的 CDP 连接而退出 1。随后一次重跑因 CDP 没有页面而未开始应用测试。改用独立端口和持久 HTML 播放器后得到上述完整退出 0 结果，不把测试脚本问题误记为 ASR、翻译或捕获失败。

此验收只证明 **Chrome 播放本地音频** 的应用捕获链路，不证明浏览器内真实视频网站、浏览器会议或按标签页隔离能力。

## QuickTime 持续捕获限制

QuickTime Player 已能开始捕获并收到首个 `16000 Hz, mono, s16le` 音频块，但持续运行时，后端路径和直接执行 release helper 都出现：

```text
ScreenCaptureKit stopped: 流播放期间，应用程序连接中断而导致失败
```

直接 helper 测试以代码 2 退出。FastAPI 不是唯一触发条件；Chrome 直接 helper 又能连续运行 110 秒，因此当前记录为 QuickTime 特定的 ScreenCaptureKit 兼容性/稳定性限制。没有据此修改系统服务、权限数据库或宣称 QuickTime 已通过。

## 尚未完成或尚未实测

- QuickTime 持续捕获的连接中断仍未解决。
- 真实浏览器视频网站和浏览器会议；当前 Chrome 全链路使用本地 MP3，不是视频网站兼容性证明。
- Zoom、Teams、腾讯会议的真实会议兼容性。
- 权限撤销、目标 App 退出/重启的真实端到端异常路径。
- 输出设备切换、休眠唤醒和 30～60 分钟长会话。
- 真实物理麦克风的开始、暂停、停止和尾段排空；当前完整模型链路使用固定音频回放。
- 会议双方“我 / 对方”双通道；这是后续独立增强，不属于当前实现。
