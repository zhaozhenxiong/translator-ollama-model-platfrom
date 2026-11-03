# Local Live Translator

本地实时听写翻译应用。使用 [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) 和 Apple Silicon MLX Whisper 在本机识别语音，默认通过本地 Ollama 模型翻译，也可显式选择本机 CC Switch 的 OpenAI 兼容路由。提供双语聊天、文档阅读、历史保存、会话删除和文件导出。

> 当前为 MVP 开发版。默认配置面向 macOS Apple Silicon；真实模型延迟取决于音频、模型和系统负载。真实验证结果与尚未通过的验收项见 [验证记录](docs/VALIDATION.md)。

## 已实现功能

- 浏览器麦克风采集，PCM 16-bit / 16 kHz / 单声道实时传输。
- macOS 13+ 按应用捕获音频：选择一个运行中的 App，不改变系统输出设备，其他 App 的数字音频不进入该会话。
- WhisperLiveKit 独立本地进程，默认 `mlx-whisper + large-v3-turbo + LocalAgreement`。
- 临时原文与稳定原文分离，只翻译稳定片段。
- Ollama `/api/chat` 流式翻译，默认 `qwen3.5:9b-mlx`，显式关闭 thinking。
- 可选 CC Switch `/v1/chat/completions` SSE 翻译；每个会话固定保存创建时的来源和模型，实时翻译与手动重试使用同一快照。
- 两种来源共享全局串行翻译限制、有界队列、有限重试和同语言跳过。
- 双语聊天视图、文档视图、原文/译文/双语显示。
- SQLite 会话与片段存储，Markdown 自动镜像。
- Markdown、TXT、DOCX 导出。
- 翻译失败保留原文，支持手动重试。
- 历史会话查看与永久删除；删除同时清除数据库片段和对应 Markdown。
- 麦克风/应用来源选择、暂停和停止时排空。
- 应用音频音量指示、权限引导、目标应用异常后的记录保存。
- 服务默认只监听 `127.0.0.1`，不默认保存原始音频，不保存 ScreenCaptureKit 视频画面。

## 工作流程

```text
麦克风 / BlackHole
    → 浏览器 AudioWorklet ─┐
                          ├→ FastAPI WebSocket
所选 App → Swift helper ──┘        ↓
             （仅音频 PCM）   WhisperLiveKit（稳定听写）
                               → SQLite + Markdown（先保存原文）
                               → Ollama 或 CC Switch（流式翻译）
                               → 更新同一聊天片段和文档
```

临时听写只用于界面反馈，不入库、不调用 LLM。WhisperLiveKit full 模式会重复发送整份 `lines` 快照，因此后端只提交其中已经完成的 speech lines；最后一个仍可能增长的 speech line 与 `buffer_transcription` 一起作为临时文本，在行完成或停止排空时再入库。这样既避免识别文本变化时重复翻译和译文闪烁，也不会把同一句话的累计前缀保存成多段。

## 环境要求

- macOS Apple Silicon，建议 32 GB 或以上统一内存。
- Python 3.12、Node.js 22+、npm、uv、FFmpeg。
- Ollama 0.40+。
- 默认翻译模型 `qwen3.5:9b-mlx`；可通过配置换成其他 Ollama 文本模型。
- 可选：CC Switch 4.x 本机代理服务及其已授权的上游。通用路由默认不需要本地密钥，实际上游凭证由 CC Switch 管理。
- 按应用捕获需要 macOS 13+、Swift 5.9+ 工具链和 ScreenCaptureKit 权限；当前使用 Command Line Tools 即可构建，无第三方 Swift 依赖。
- 首次运行需要下载 Whisper 模型；之后默认 Ollama 路径可离线使用，CC Switch 上游是否需要网络取决于其实际路由。

## 安装

在项目根目录执行：

```bash
uv sync
```

```bash
npm --prefix web install
```

```bash
npm --prefix web run build
```

```bash
ollama pull qwen3.5:9b-mlx
```

如需按应用捕获，构建并签名原生 helper；脚本也会运行 PCM 转换自检：

```bash
bash scripts/build_app_audio_helper.sh
```

如需修改端口或模型，复制配置模板：

```bash
cp config.example .env
```

`.env` 只保存本机配置，不应提交到 Git。通用 CC Switch 路由无需填写真实上游密钥；如以后确需本地凭证，也只放在后端配置中，不通过浏览器提交。

## 启动

确保 Ollama 已启动，然后运行：

```bash
uv run python -m app.main
```

打开 <http://127.0.0.1:8000>。应用会启动并监控本地 WhisperLiveKit 进程；首次准备模型可能需要较长时间。运行状态可在 <http://127.0.0.1:8000/api/health> 查看，API 文档位于 <http://127.0.0.1:8000/docs>。

### 前端开发模式

后端保持运行，再执行：

```bash
npm --prefix web run dev
```

打开 <http://127.0.0.1:5173>。Vite 会将 API 和 WebSocket 转发到 `127.0.0.1:8000`。

## 选择翻译来源

每次新建会话默认使用 Ollama。模型来源与模型 ID 在创建时保存，录制中不能切换；查看历史或重试失败译文时，仍使用该会话原来保存的来源和模型，不会被后续全局配置悄悄替换。

如需 CC Switch，在本机 `.env` 显式启用并配置允许选择的路由模型：

```bash
CC_SWITCH_ENABLED=true
CC_SWITCH_BASE_URL=http://127.0.0.1:15721
CC_SWITCH_MODELS=gpt-5.5
```

当前 CC Switch 的公开 `/v1/models` 目录可能为空，因此应用使用这里的静态模型列表，不把空目录误判为路由离线。地址仅允许本机 HTTP 回环源；通用 OpenAI 路由默认不发送本地 Authorization，只有已确认需要时才配置 `CC_SWITCH_API_KEY`。

**OpenAI Official 授权例外：**`codex-official` 会在转发前检查官方 Codex 登录上下文，并非普通“本机无认证”路由。若返回 401/403，请在 CC Switch 中检查当前 Provider 的登录或 API Key；OpenAI Official 需要在官方 Codex 客户端完成原生登录，`CC_SWITCH_API_KEY` 不是上游 API Key，不能代替或修复该登录，`PROXY_MANAGED` 也不适用于 OpenAI Official。不要把 Codex 令牌复制到本应用。

**隐私边界：**音频始终由本机 ASR 识别；选择 CC Switch 后，稳定原文会发送给本机 CC Switch，再由其转发到实际上游。当前本机配置的上游为云服务，因此原文会离开这台 Mac。界面会显示云上游提示。路由 `/health` 只证明本机路由在线：初始状态为“上游未验证”，真实翻译成功后才变为“就绪”，401/403 后显示“上游未授权”；轮询不会发起收费推理。授权失败不会永久禁用该来源，修复 CC Switch 后可重新新建会话或重试。

## 使用麦克风

1. 点击新建会话，声音来源选择“麦克风”，再选择设备、源语言和目标语言。
2. 允许浏览器使用麦克风后开始说话。
3. 灰色临时文本会持续修订；稳定片段进入聊天记录并开始翻译。
4. 暂停期间发送静音帧以保持听写连接，不会发送麦克风内容。
5. 停止时等待最后一段听写和剩余翻译完成。
6. 在聊天/文档视图间切换，或导出 Markdown、TXT、DOCX。
7. 翻译失败的片段保留原文，可点击重试。

重要会议建议手动指定源语言；自动检测在很短或混合语言片段上可能不稳定。

## 删除历史会话

点击侧栏会话旁的删除按钮并确认后，会永久删除该数据库会话、全部原文/译文片段和 `data/transcripts/<session-id>.md`，无法恢复。进行中的会话不能删除，需先结束听写；是否仍活跃以后端的真实连接为准，历史 `active` 字段不会让删除按钮永久不可用。TXT/DOCX 原本就是按需生成，已另行下载的文件和全局运行日志不在删除范围内。

如果上次异常退出或应用被直接终止，启动时会将没有真实连接的遗留 `active` 会话改为 `interrupted` 并记录结束时间；其中未完成的翻译会标记为失败，可手动重试，Markdown 镜像也会同步更新。

## 按应用捕获音频（macOS 13+）

此模式直接捕获一个运行中 App 的声音，不需要修改 macOS 默认输出设备：

1. 先执行 `bash scripts/build_app_audio_helper.sh`，再启动后端。
2. 新建会话，声音来源选择“应用音频”。此模式不会请求浏览器麦克风权限。
3. 按界面提示请求权限。在“系统设置 → 隐私与安全性 → 屏幕与系统音频录制”中，允许系统实际提示的 helper 或启动宿主条目，然后点击“重新检查权限”。不要给无关应用盲目授权。
4. 刷新应用列表，按名称、bundle ID 和 PID 选择目标 App，再开始会话。
5. 收到首个真实 PCM 块后界面才显示捕获就绪；音量指示可帮助确认目标 App 正在发声。
6. 暂停时真实音频被丢弃，并向 ASR 发送等长静音；恢复不会补发暂停期间的内容。停止时等待最后听写、翻译和文档保存。

权限由 macOS TCC 按实际启动宿主管理。如果直接运行 helper 已授权、后端仍显示未授权，请检查系统设置中实际被提示的后端/终端/桌面宿主条目，授权后重新检查，必要时重启后端。应用不会使用私有 TCC API 或绕过系统权限。

当前边界：

- 每次只选择一个 App；选择浏览器时捕获整个浏览器，不区分标签页或窗口。
- 只捕获应用声音，不同时录制麦克风；会议双方“我 / 对方”双通道尚未实现。
- 不保存视频画面，不默认保存原始音频；受 DRM 或应用防录制机制保护的内容可能无法捕获。
- 本机捕获权限已通过，Chrome 播放本地 MP3 的“应用选择 → 捕获 → 听写 → 翻译 → 保存与导出”链路已验收；QuickTime 持续捕获仍会出现 ScreenCaptureKit 连接中断。真实浏览器视频、Zoom、Teams、腾讯会议尚未实测，不能据此宣称全部兼容。详见 [验证记录](docs/VALIDATION.md)。

## 使用 BlackHole 作为回退

BlackHole 仍可用于把系统输出路由成虚拟麦克风输入：

1. 安装 [BlackHole](https://github.com/ExistentialAudio/BlackHole) 2ch。
2. 按 BlackHole 文档将需要听到的应用/系统输出路由到 BlackHole；如需同时监听扬声器，可配置多输出设备。
3. 重新打开本应用的麦克风设备列表，选择 BlackHole 作为音频输入。

BlackHole 路由的是所配置设备上的混合声音，不提供按 App 隔离。当前版本不提供麦克风与系统声音的软件混音，也不提供原生悬浮字幕窗口。

## 配置

默认值见 `config.example`。常用项：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 应用监听地址 |
| `ASR_BACKEND` | `mlx-whisper` | WhisperLiveKit 后端 |
| `ASR_MODEL` | `large-v3-turbo` | 多语言 Whisper 模型 |
| `ASR_BACKEND_POLICY` | `localagreement` | 稳定文本提交策略 |
| `ASR_STARTUP_TIMEOUT` | `1800` | 首次模型下载与听写启动等待上限（秒） |
| `OLLAMA_MODEL` | `qwen3.5:9b-mlx` | 本地翻译模型 |
| `CC_SWITCH_ENABLED` | `false` | 是否允许选择 CC Switch；每次新建仍默认 Ollama |
| `CC_SWITCH_BASE_URL` | `http://127.0.0.1:15721` | 仅允许本机 HTTP 回环源，不跟随重定向 |
| `CC_SWITCH_MODELS` | `gpt-5.5` | 逗号分隔、允许选择的路由模型 ID |
| `CC_SWITCH_API_KEY` | 空 | 可选本地凭证；通用路由默认不发送 Authorization |
| `TRANSLATION_CONCURRENCY` | `1` | 两种来源共享的全局翻译并发 |
| `TRANSLATION_QUEUE_SIZE` | `8` | 每个实时会话的翻译队列上限 |
| `ASR_AUTO_START` | `true` | 是否由应用管理 WhisperLiveKit 进程 |
| `APP_AUDIO_HELPER_PATH` | 自动查找 | 可选的原生 helper 绝对路径；优先使用 release，其次 debug 构建 |
| `APP_AUDIO_READ_TIMEOUT` | `10` | 原生 PCM 读取超时（秒） |

如使用已有的外部 WhisperLiveKit 服务，将 `ASR_AUTO_START=false`，并确保地址和端口匹配。

## 验证与测试

详细结果见 [验证记录](docs/VALIDATION.md)。

运行自动化测试：

```bash
PYTHONPATH=. uv run python -m pytest tests
```

运行静态检查：

```bash
uvx ruff check app tests scripts
```

构建 helper 并运行 PCM 自检：

```bash
bash scripts/build_app_audio_helper.sh
```

检查 Ollama 模型、thinking 配置和真实翻译延迟：

```bash
uv run python -m scripts.smoke_ollama --text "Hello, how are you today?" --source en --target zh
```

独立回放音频测试 WhisperLiveKit（先停止应用，避免占用同一 ASR 端口）：

```bash
uv run python -m scripts.replay_audio "/absolute/path/audio.mp3" --language zh --speed 1
```

`--speed 0` 会尽快送入音频，用于吞吐量测试；`--speed 1` 模拟实时播放。

完整应用运行时，可通过 REST + WebSocket 回放一段音频，验证听写、翻译、存储和导出全链路：

```bash
uv run python -m scripts.replay_session "/absolute/path/audio.mp3" --source zh --target en --duration 30
```

脚本会输出新会话 ID、稳定原文、译文和 DOCX 导出地址。`--duration` 可省略，`--speed` 的含义与独立 ASR 回放相同。可用 `--provider cc_switch --model gpt-5.5` 验证已启用的路由来源，省略时仍为 Ollama。脚本输出导出地址后，还应实际下载并检查导出文件。

## 数据与隐私

- SQLite：`data/app.db`
- 自动 Markdown：`data/transcripts/<session-id>.md`
- WhisperLiveKit 日志：`data/logs/asr.log`
- 原生捕获诊断：`data/logs/app-audio.log`，只记录 stderr 尾部等诊断，不记录 PCM 内容。
- 原始音频默认不写入磁盘；ScreenCaptureKit 的视频样本直接丢弃，不发送给浏览器或写入磁盘。
- 默认 Ollama 路径在模型下载完成后不需要外网；选择 CC Switch 时，原文会由本机路由转发给实际上游，当前云上游会接收这些文本。
- 路由地址和可选本地凭证只存在后端配置，不写入会话、导出或安全目录响应；实际翻译错误不会转发上游响应体或请求头。
- 不要把服务改为监听公网地址，除非另外增加认证、TLS 和访问控制。

## llama.cpp 说明

当前默认翻译后端是 Ollama，不是 llama.cpp；另有可选 CC Switch 路由。`qwen3.5:9b-mlx` 属于 MLX 使用路线，llama.cpp 需要另行准备兼容的 GGUF 模型；换后端并不必然更快。翻译调用已隔离在 `app/translator.py`，后续可用同一组音频和提示词对 Ollama MLX 与 llama.cpp GGUF 做基准比较。

## 当前限制

- 尚未实现实时说话人分离、会议双通道、TTS、云同步和多用户服务。
- “实时”指低延迟分段听写和翻译，不承诺逐字同时完成整句口译。
- 27B 或更大模型、较大 Whisper 模型以及其他高负载任务会争用统一内存和 GPU。
- 浏览器设备权限、BlackHole 配置、macOS 捕获权限和 Ollama 模型未安装都会影响启动；请先检查 `/api/health`、`data/logs/asr.log` 和 `data/logs/app-audio.log`。
- CC Switch 模型目录由后端静态配置，不会自动枚举所有上游；路由在线不等于上游推理已验证，授权、额度和网络问题仍会保留原文并标记翻译失败。
- 应用音频不区分浏览器标签页，尚未验证输出设备切换、休眠唤醒和 30～60 分钟长会话。
- 历史中断片段会标记为失败，可重新翻译；当前不支持从同一音频位置续录。

## 项目结构

```text
app/                      FastAPI、ASR 进程、实时会话、翻译、存储、导出
app/audio_capture.py      原生应用音频进程管理和 PCM 输入
native/AppAudioHelper/    Swift + ScreenCaptureKit 按应用音频 helper
web/                      React + TypeScript 前端和 AudioWorklet
scripts/                  helper 构建、Ollama 验证与音频回放脚本
tests/                    单元、API 和模拟实时 WebSocket 测试
docs/                     扩展计划与真实验证记录
data/                     本地数据库、Markdown 和运行日志
```
