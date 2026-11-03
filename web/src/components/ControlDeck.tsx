import { AlertCircle, AppWindow, ArrowRight, AudioLines, ChevronDown, CirclePause, CloudUpload, Cpu, LoaderCircle, Mic, Play, RefreshCw, ShieldCheck, Square } from 'lucide-react';
import type { AppConfig, ApplicationAudioInfo, AudioInput, AudioPermission, AudioSourceType, Health, RecorderState, TranslationProvider } from '../types';
import { LANGUAGES, TARGET_LANGUAGES } from '../types';

interface ControlDeckProps {
  sourceType: AudioSourceType;
  devices: AudioInput[];
  deviceId: string;
  applications: ApplicationAudioInfo[];
  applicationKey: string;
  audioPermission: AudioPermission | null;
  captureLevel: number;
  sourceLanguage: string;
  targetLanguage: string;
  config: AppConfig | null;
  configLoading: boolean;
  translationProvider: TranslationProvider;
  translationModel: string;
  canStart: boolean;
  recorderState: RecorderState;
  liveMessage: string;
  health: Health | null;
  translationQueue: number;
  asrLag: number | null;
  onSourceType: (type: AudioSourceType) => void;
  onDevice: (id: string) => void;
  onDiscoverDevices: () => void;
  onApplication: (key: string) => void;
  onRefreshApplications: () => void;
  onRequestAudioPermissions: () => void;
  onSourceLanguage: (code: string) => void;
  onTargetLanguage: (code: string) => void;
  onTranslationProvider: (provider: TranslationProvider) => void;
  onTranslationModel: (model: string) => void;
  onStart: () => void;
  onPause: () => void;
  onStop: () => void;
}

function healthLabel(status: string | undefined): string {
  if (!status) return '检查中';
  const labels: Record<string, string> = {
    ready: '就绪', online: '在线', ok: '正常', healthy: '正常', starting: '启动中',
    loading: '加载中', unavailable: '不可用', offline: '离线', error: '异常',
    unverified: '上游未验证', unauthorized: '上游未授权',
  };
  return labels[status] ?? status;
}

function isHealthy(status: string | undefined): boolean {
  return ['ready', 'online', 'ok', 'healthy'].includes(status ?? '');
}

export function ControlDeck(props: ControlDeckProps) {
  const { recorderState, devices, health, sourceType } = props;
  const active = ['starting', 'live', 'paused', 'stopping'].includes(recorderState);
  const isLive = recorderState === 'live';
  const isPaused = recorderState === 'paused';
  const applicationMode = sourceType === 'application';
  const provider = props.config?.translation_providers.find((item) => item.id === props.translationProvider);
  const modelOptions = provider
    ? Array.from(new Set([provider.default_model, ...provider.models].filter((model) => model.length > 0)))
    : [];
  if (props.translationModel && !modelOptions.includes(props.translationModel)) {
    modelOptions.push(props.translationModel);
  }
  const unavailableReason = provider?.error ?? (provider && !provider.enabled
    ? '此翻译来源未启用。'
    : provider && !provider.available
      ? '此翻译来源当前不可用。'
      : null);
  const cloudTranslation = provider?.id === 'cc_switch' || provider?.privacy === 'cloud';
  const translationHealth = props.translationProvider === 'cc_switch' ? health?.cc_switch : health?.ollama;
  const translationHealthTitle = [
    props.translationProvider === 'cc_switch' ? 'CC Switch' : 'Ollama',
    translationHealth?.model,
    translationHealth?.status === 'unverified' ? '路由在线；真实翻译成功后才确认上游模型与授权可用' : null,
    translationHealth?.error,
  ].filter(Boolean).join(' · ') || undefined;

  return (
    <section className="control-area" aria-label="听写控制">
      <div className="control-deck">
        <div className="source-controls">
          <div className="source-mode" role="group" aria-label="声音来源类型">
            <button type="button" className={!applicationMode ? 'is-active' : ''} onClick={() => props.onSourceType('microphone')} disabled={active}>
              <Mic size={15} />麦克风
            </button>
            <button type="button" className={applicationMode ? 'is-active' : ''} onClick={() => props.onSourceType('application')} disabled={active}>
              <AppWindow size={15} />应用音频
            </button>
          </div>

          <div className="control-field device-field">
            <label htmlFor="audio-device">{applicationMode ? '目标应用' : '输入设备'}</label>
            <div className="select-with-icon">
              {applicationMode ? <AppWindow size={16} /> : <AudioLines size={16} />}
              {applicationMode ? (
                <select id="audio-device" value={props.applicationKey} onChange={(event) => props.onApplication(event.target.value)} disabled={active || !props.audioPermission?.screen_recording_granted}>
                  {props.applications.length === 0 && <option value="">{props.audioPermission?.screen_recording_granted ? '没有可选择的应用' : '需要先授权'}</option>}
                  {props.applications.map((application) => {
                    const key = `${application.bundle_id}:${application.process_id}`;
                    return <option key={key} value={key}>{application.name} · {application.process_id}</option>;
                  })}
                </select>
              ) : (
                <select id="audio-device" value={props.deviceId} onChange={(event) => props.onDevice(event.target.value)} disabled={active}>
                  {devices.length === 0 && <option value="">系统默认麦克风</option>}
                  {devices.map((device) => <option key={device.deviceId} value={device.deviceId}>{device.label}</option>)}
                </select>
              )}
              <ChevronDown size={14} className="select-chevron" />
              <button type="button" className="device-refresh" onClick={applicationMode ? props.onRefreshApplications : props.onDiscoverDevices} disabled={active} title={applicationMode ? '刷新运行应用' : '刷新音频设备'}>
                <RefreshCw size={14} />
              </button>
            </div>
            {applicationMode && !active && (
              <div className="source-hint">
                {props.audioPermission?.screen_recording_granted ? (
                  <span>捕获整个 App；浏览器不区分标签页，不保存画面。</span>
                ) : (
                  <>
                    <span>需要“屏幕与系统音频录制”权限，仅处理声音。</span>
                    <button type="button" className="inline-action" onClick={props.onRequestAudioPermissions}>
                      <ShieldCheck size={12} />{props.audioPermission?.requested ? '重新检查权限' : '请求权限'}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="language-controls">
          <div className="control-field">
            <label htmlFor="source-language">识别语言</label>
            <div className="plain-select">
              <select id="source-language" value={props.sourceLanguage} onChange={(event) => props.onSourceLanguage(event.target.value)} disabled={active}>
                {LANGUAGES.map((language) => <option key={language.code} value={language.code}>{language.name}</option>)}
              </select>
              <ChevronDown size={14} />
            </div>
          </div>
          <ArrowRight size={17} className="language-arrow" />
          <div className="control-field">
            <label htmlFor="target-language">翻译为</label>
            <div className="plain-select">
              <select id="target-language" value={props.targetLanguage} onChange={(event) => props.onTargetLanguage(event.target.value)} disabled={active}>
                {TARGET_LANGUAGES.map((language) => <option key={language.code} value={language.code}>{language.name}</option>)}
              </select>
              <ChevronDown size={14} />
            </div>
          </div>
        </div>

        <div className="translation-settings">
          <div className="translation-controls">
            <div className="control-field">
              <label htmlFor="translation-provider">翻译来源</label>
              <div className="plain-select">
                <select
                  id="translation-provider"
                  value={props.translationProvider}
                  onChange={(event) => props.onTranslationProvider(event.target.value as TranslationProvider)}
                  disabled={active || props.configLoading || !props.config}
                  aria-describedby="translation-provider-status"
                >
                  {(!props.config || props.config.translation_providers.length === 0) && <option value="ollama">Ollama</option>}
                  {props.config?.translation_providers.map((item) => (
                    <option key={item.id} value={item.id}>{item.label}</option>
                  ))}
                </select>
                <ChevronDown size={14} />
              </div>
            </div>
            <div className="control-field">
              <label htmlFor="translation-model">模型</label>
              <div className="plain-select">
                <select
                  id="translation-model"
                  value={props.translationModel}
                  onChange={(event) => props.onTranslationModel(event.target.value)}
                  disabled={active || props.configLoading || !provider || modelOptions.length === 0}
                  aria-describedby="translation-provider-status"
                >
                  {modelOptions.length === 0 && <option value="">{props.configLoading ? '正在加载…' : '没有可用模型'}</option>}
                  {modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
                </select>
                <ChevronDown size={14} />
              </div>
            </div>
          </div>
          <div className="translation-notices" id="translation-provider-status" aria-live="polite">
            {props.configLoading && <span className="provider-hint">正在加载翻译配置…</span>}
            {!props.configLoading && !props.config && <span className="provider-error"><AlertCircle size={12} />翻译配置未加载，请新建会话重试。</span>}
            {unavailableReason && <span className="provider-error"><AlertCircle size={12} />{unavailableReason}</span>}
            {!props.configLoading && provider && modelOptions.length === 0 && <span className="provider-error"><AlertCircle size={12} />此来源未配置可用模型。</span>}
            {cloudTranslation && (
              <span className="cloud-warning"><CloudUpload size={12} />CC Switch：音频在本机识别，原文会经本机路由发送到云上游。</span>
            )}
          </div>
        </div>

        <div className="record-controls">
          {!active && (
            <button type="button" className="primary-button record-button" onClick={props.onStart} disabled={!props.canStart}>
              {applicationMode ? <AppWindow size={17} /> : <Mic size={17} />}开始听写
            </button>
          )}
          {recorderState === 'starting' && (
            <button type="button" className="primary-button record-button" disabled>
              <LoaderCircle size={17} className="is-spinning" />正在准备
            </button>
          )}
          {(isLive || isPaused) && (
            <>
              <button type="button" className="secondary-button pause-button" onClick={props.onPause}>
                {isLive ? <CirclePause size={17} /> : <Play size={17} />}
                {isLive ? '暂停' : '继续'}
              </button>
              <button type="button" className="stop-button" onClick={props.onStop}>
                <Square size={15} fill="currentColor" />结束
              </button>
            </>
          )}
          {recorderState === 'stopping' && (
            <button type="button" className="secondary-button" disabled>
              <LoaderCircle size={17} className="is-spinning" />正在完成
            </button>
          )}
        </div>
      </div>

      <div className="system-statusbar">
        <div className={`live-status ${isLive ? 'is-live' : ''}`}>
          <span className="status-dot" />
          <span>{props.liveMessage}</span>
        </div>
        <div className="service-statuses">
          {applicationMode && active && (
            <span className="capture-meter">
              <span>应用音量</span>
              <meter min={0} max={1} value={props.captureLevel} aria-label="应用音频音量" aria-valuetext={props.captureLevel > 0.001 ? '有声音' : '静音'} />
              <span>{props.captureLevel > 0.001 ? '有声音' : '静音'}</span>
            </span>
          )}
          {props.translationQueue > 0 && <span className="queue-status">翻译队列 {props.translationQueue}</span>}
          {props.asrLag !== null && props.asrLag > 0.05 && <span>识别积压 {props.asrLag.toFixed(1)} 秒</span>}
          <span className={isHealthy(health?.asr.status) ? 'service-ok' : ''} title={health?.asr.error ?? undefined}>
            <AudioLines size={13} />听写{healthLabel(health?.asr.status)}
          </span>
          <span className={isHealthy(translationHealth?.status) ? 'service-ok' : ''} title={translationHealthTitle}>
            <Cpu size={13} />翻译{healthLabel(translationHealth?.status)}
          </span>
        </div>
      </div>
    </section>
  );
}
