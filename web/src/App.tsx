import { useCallback, useEffect, useRef, useState } from 'react';
import { createSession, deleteSession, getAudioPermissions, getConfig, getHealth, getSession, listAudioApplications, listSessions, liveSocket, requestAudioPermissions, retrySegment } from './api';
import { discoverAudioInputs, PcmRecorder } from './audio';
import { ControlDeck } from './components/ControlDeck';
import { Sidebar } from './components/Sidebar';
import { Transcript } from './components/Transcript';
import { ErrorBanner, WorkspaceHeader } from './components/WorkspaceHeader';
import type {
  AppConfig,
  ApplicationAudioInfo,
  AudioInput,
  AudioPermission,
  AudioSourceType,
  DocumentMode,
  Health,
  RecorderState,
  Segment,
  ServerEvent,
  Session,
  TranslationProvider,
  TranslationProviderConfig,
  ViewMode,
} from './types';

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'NotAllowedError') {
    return '麦克风权限未开启。请在浏览器地址栏的网站设置中允许麦克风访问。';
  }
  if (error instanceof DOMException && error.name === 'NotFoundError') {
    return '没有找到可用的音频输入，请检查麦克风或 BlackHole 设置。';
  }
  return error instanceof Error ? error.message : '发生了未知错误，请重试。';
}

function upsertSegment(segments: Segment[], incoming: Segment): Segment[] {
  const exists = segments.some((segment) => segment.id === incoming.id);
  const next = exists
    ? segments.map((segment) => segment.id === incoming.id ? incoming : segment)
    : [...segments, incoming];
  return next.sort((a, b) => a.sequence - b.sequence);
}

function defaultTranslationModel(provider: TranslationProviderConfig | undefined): string {
  return provider?.default_model || provider?.models[0] || '';
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    connecting: '正在连接本地模型…',
    asr_starting: '正在准备听写模型…',
    ready: '正在聆听',
    listening: '正在聆听',
    transcribing: '正在听写并翻译…',
    finishing: '正在完成剩余翻译…',
    draining: '正在整理最后一段听写…',
    completed: '会话已完成',
    paused: '听写已暂停',
  };
  return labels[status] ?? status;
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [devices, setDevices] = useState<AudioInput[]>([]);
  const [deviceId, setDeviceId] = useState('');
  const [sourceType, setSourceType] = useState<AudioSourceType>('microphone');
  const [applications, setApplications] = useState<ApplicationAudioInfo[]>([]);
  const [applicationKey, setApplicationKey] = useState('');
  const [audioPermission, setAudioPermission] = useState<AudioPermission | null>(null);
  const [captureLevel, setCaptureLevel] = useState(0);
  const [sourceLanguage, setSourceLanguage] = useState('auto');
  const [targetLanguage, setTargetLanguage] = useState('zh');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configLoading, setConfigLoading] = useState(true);
  const [translationProvider, setTranslationProvider] = useState<TranslationProvider>('ollama');
  const [translationModel, setTranslationModel] = useState('');
  const [recorderState, setRecorderState] = useState<RecorderState>('idle');
  const [partial, setPartial] = useState('');
  const [liveMessage, setLiveMessage] = useState('准备就绪');
  const [translationQueue, setTranslationQueue] = useState(0);
  const [asrLag, setAsrLag] = useState<number | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('chat');
  const [documentMode, setDocumentMode] = useState<DocumentMode>('bilingual');
  const [showTimestamps, setShowTimestamps] = useState(true);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const selectedIdRef = useRef<string | null>(null);
  const activeSessionRef = useRef<string | null>(null);
  const recorderRef = useRef<PcmRecorder | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const intentionalCloseRef = useRef(false);
  const configRequestRef = useRef(0);

  const isActive = ['starting', 'live', 'paused', 'stopping'].includes(recorderState);
  const selectedProvider = config?.translation_providers.find((provider) => provider.id === translationProvider);
  const translationReady = !configLoading
    && Boolean(selectedProvider?.enabled && selectedProvider.available && translationModel);
  const audioReady = sourceType === 'microphone'
    || Boolean(audioPermission?.screen_recording_granted && applicationKey);
  const canStart = !isActive && translationReady && audioReady;

  const selectSession = useCallback(async (id: string) => {
    try {
      setError(null);
      const detail = await getSession(id);
      selectedIdRef.current = id;
      setSelectedSession(detail);
      setSegments(detail.segments);
      setPartial('');
      setSidebarOpen(false);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, []);

  const loadConfig = useCallback(async () => {
    const requestId = ++configRequestRef.current;
    setConfigLoading(true);
    try {
      const nextConfig = await getConfig();
      if (requestId !== configRequestRef.current) return;
      setConfig(nextConfig);
      const ollama = nextConfig.translation_providers.find((provider) => provider.id === 'ollama');
      setTranslationProvider('ollama');
      setTranslationModel(defaultTranslationModel(ollama));
    } catch (reason) {
      if (requestId !== configRequestRef.current) return;
      setConfig(null);
      setTranslationProvider('ollama');
      setTranslationModel('');
      setError(errorMessage(reason));
    } finally {
      if (requestId === configRequestRef.current) setConfigLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
    void (async () => {
      try {
        const list = await listSessions();
        setSessions(list);
        if (list[0]) await selectSession(list[0].id);
      } catch (reason) {
        setError(errorMessage(reason));
      }
    })();
  }, [loadConfig, selectSession]);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        setHealth(await getHealth());
      } catch {
        setHealth({
          status: 'offline',
          asr: { status: 'offline', error: '无法连接应用后端' },
          ollama: { status: 'offline', model: '', error: '无法连接应用后端' },
          cc_switch: { status: 'offline', model: '', error: '无法连接应用后端' },
        });
      }
    };
    void checkHealth();
    const interval = window.setInterval(checkHealth, 10_000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => () => {
    intentionalCloseRef.current = true;
    socketRef.current?.close();
    void recorderRef.current?.stop();
  }, []);

  const handleServerEvent = useCallback((event: ServerEvent) => {
    if (event.type === 'status') {
      setLiveMessage(event.message || statusText(event.status));
      if (event.translation_queue !== undefined) setTranslationQueue(event.translation_queue);
      if (event.asr_lag !== undefined) setAsrLag(event.asr_lag);
      return;
    }
    if (event.type === 'partial') {
      setPartial(event.text);
      if (event.asr_lag !== undefined) setAsrLag(event.asr_lag);
      return;
    }
    if (event.type === 'segment') {
      if (event.segment.session_id === selectedIdRef.current) {
        setSegments((current) => upsertSegment(current, event.segment));
      }
      setPartial('');
      return;
    }
    if (event.type === 'translation_update') {
      setSegments((current) => current.map((segment) => segment.id === event.segment_id
        ? { ...segment, translated_text: event.text, translation_status: 'translating', error_message: null }
        : segment));
      return;
    }
    if (event.type === 'translation_final') {
      setSegments((current) => upsertSegment(current, event.segment));
      return;
    }
    if (event.type === 'translation_error') {
      setSegments((current) => current.map((segment) => segment.id === event.segment_id
        ? { ...segment, translation_status: 'failed', error_message: event.message }
        : segment));
      return;
    }
    if (event.type === 'capture_starting') {
      setLiveMessage(`正在启动${event.display_name ? ` ${event.display_name}` : '应用'}音频捕获…`);
      return;
    }
    if (event.type === 'capture_ready') {
      setLiveMessage(`正在捕获${event.display_name ? ` ${event.display_name}` : '应用'}的声音`);
      return;
    }
    if (event.type === 'capture_level') {
      setCaptureLevel(event.paused ? 0 : event.level);
      return;
    }
    if (event.type === 'capture_warning' || event.type === 'capture_error') {
      setError(event.message);
      setLiveMessage(event.type === 'capture_error' ? '应用音频捕获已中断' : '应用音频捕获警告');
      return;
    }
    if (event.type === 'capture_ended') {
      setCaptureLevel(0);
      return;
    }
    if (event.type === 'session_completed') {
      intentionalCloseRef.current = true;
      socketRef.current?.close();
      socketRef.current = null;
      activeSessionRef.current = null;
      setRecorderState('completed');
      setLiveMessage('会话已完成');
      setPartial('');
      setTranslationQueue(0);
      setAsrLag(null);
      setCaptureLevel(0);
      void Promise.all([getSession(event.session_id), listSessions()]).then(([detail, list]) => {
        if (selectedIdRef.current === event.session_id) {
          setSelectedSession(detail);
          setSegments(detail.segments);
        }
        setSessions(list);
      }).catch((reason) => setError(errorMessage(reason)));
      return;
    }
    if (event.type === 'error') {
      setError(event.message);
      setLiveMessage('运行异常');
    }
  }, []);

  const handleApplicationRefresh = async (requestPermission = false) => {
    try {
      setError(null);
      const permission = requestPermission
        ? await requestAudioPermissions()
        : await getAudioPermissions();
      setAudioPermission(permission);
      if (!permission.screen_recording_granted) {
        setApplications([]);
        setApplicationKey('');
        if (requestPermission) {
          setLiveMessage('请在系统设置中授权后，点击“重新检查权限”');
        }
        return;
      }
      const list = await listAudioApplications();
      setApplications(list);
      const selected = list.some((item) => `${item.bundle_id}:${item.process_id}` === applicationKey);
      if (!selected) {
        const first = list[0];
        setApplicationKey(first ? `${first.bundle_id}:${first.process_id}` : '');
      }
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const handleSourceType = (type: AudioSourceType) => {
    setSourceType(type);
    setCaptureLevel(0);
    setError(null);
    if (type === 'application') void handleApplicationRefresh();
  };

  const handleDiscoverDevices = async () => {
    try {
      setError(null);
      const inputs = await discoverAudioInputs();
      setDevices(inputs);
      if (!deviceId && inputs[0]) setDeviceId(inputs[0].deviceId);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const handleTranslationProvider = (providerId: TranslationProvider) => {
    setTranslationProvider(providerId);
    const provider = config?.translation_providers.find((item) => item.id === providerId);
    setTranslationModel(defaultTranslationModel(provider));
  };

  const handleStart = async () => {
    if (isActive) return;
    if (!translationReady || !selectedProvider) {
      setError(configLoading
        ? '翻译配置仍在加载，请稍候。'
        : selectedProvider?.error || '请选择可用的翻译来源和模型。');
      return;
    }
    const translationProviderSnapshot = selectedProvider.id;
    const translationModelSnapshot = translationModel;
    const selectedApplication = applications.find(
      (item) => `${item.bundle_id}:${item.process_id}` === applicationKey,
    );
    if (sourceType === 'application'
      && (!audioPermission?.screen_recording_granted || !selectedApplication)) {
      setError('请先授权并选择一个正在运行的应用。');
      return;
    }
    setError(null);
    setCaptureLevel(0);
    setRecorderState('starting');
    setLiveMessage(sourceType === 'application' ? '正在准备应用音频…' : '正在请求麦克风权限…');
    let recorder: PcmRecorder | null = null;
    let socket: WebSocket | null = null;

    try {
      let availableDevices = devices;
      if (sourceType === 'microphone' && availableDevices.length === 0) {
        availableDevices = await discoverAudioInputs();
        setDevices(availableDevices);
      }
      const selectedDevice = deviceId || availableDevices[0]?.deviceId || '';
      if (sourceType === 'microphone' && !deviceId && selectedDevice) setDeviceId(selectedDevice);

      setLiveMessage('正在创建本地会话…');
      const session = await createSession(
        sourceLanguage,
        targetLanguage,
        sourceType === 'application'
          ? {
              type: 'application',
              bundle_id: selectedApplication!.bundle_id,
              process_id: selectedApplication!.process_id,
              display_name: selectedApplication!.name,
            }
          : { type: 'microphone' },
        translationProviderSnapshot,
        translationModelSnapshot,
      );
      selectedIdRef.current = session.id;
      activeSessionRef.current = session.id;
      setSelectedSession(session);
      setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
      setSegments([]);
      setPartial('');
      setTranslationQueue(0);
      setAsrLag(null);
      intentionalCloseRef.current = false;

      setLiveMessage('正在连接本地模型…');
      socket = await liveSocket(session.id);
      socketRef.current = socket;
      socket.addEventListener('message', (message) => {
        try {
          handleServerEvent(JSON.parse(String(message.data)) as ServerEvent);
        } catch {
          setError('收到无法识别的本地服务消息。');
        }
      });
      socket.addEventListener('close', () => {
        if (!intentionalCloseRef.current && activeSessionRef.current === session.id) {
          activeSessionRef.current = null;
          setRecorderState('error');
          setLiveMessage('连接已断开');
          setError('实时连接中断。已确认的原文仍会保留，请重新打开会话查看。');
          void recorderRef.current?.stop();
          void refreshSessions();
        }
      });
      socket.addEventListener('error', () => {
        setError('实时连接出现异常，请检查本地后端日志。');
      });

      setLiveMessage(sourceType === 'application' ? '正在启动应用音频捕获…' : '正在启动音频采集…');
      if (sourceType === 'microphone') {
        recorder = new PcmRecorder();
        recorderRef.current = recorder;
        await recorder.start(selectedDevice, (buffer) => {
          if (socket?.readyState === WebSocket.OPEN) socket.send(buffer);
        });
      }
      setRecorderState('live');
      setLiveMessage(sourceType === 'application' ? '正在等待应用音频…' : '正在等待听写模型就绪…');
    } catch (reason) {
      intentionalCloseRef.current = true;
      socket?.close();
      socketRef.current = null;
      activeSessionRef.current = null;
      if (recorder) await recorder.stop();
      recorderRef.current = null;
      setRecorderState('error');
      setLiveMessage('启动失败');
      setError(errorMessage(reason));
      void refreshSessions();
    }
  };

  const handlePause = () => {
    const pausing = recorderState === 'live';
    if (!['live', 'paused'].includes(recorderState)) return;
    if (sourceType === 'application') {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        setError('实时连接已经断开，无法控制应用音频。');
        return;
      }
      socket.send(JSON.stringify({ type: pausing ? 'pause' : 'resume' }));
    } else {
      recorderRef.current?.setPaused(pausing);
    }
    setRecorderState(pausing ? 'paused' : 'live');
    setLiveMessage(pausing ? '听写已暂停' : '正在聆听');
    if (pausing) {
      setPartial('');
      setCaptureLevel(0);
    }
  };

  const handleStop = async () => {
    if (!['live', 'paused'].includes(recorderState)) return;
    setRecorderState('stopping');
    setLiveMessage('正在整理最后一段听写…');
    setPartial('');
    try {
      await recorderRef.current?.stop();
      recorderRef.current = null;
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        throw new Error('实时连接已经断开，无法完成最后的识别。');
      }
      socket.send(JSON.stringify({ type: 'stop' }));
    } catch (reason) {
      setError(errorMessage(reason));
      setRecorderState('error');
      activeSessionRef.current = null;
      void refreshSessions();
    }
  };

  const handleNew = () => {
    if (isActive) return;
    selectedIdRef.current = null;
    setSelectedSession(null);
    setSegments([]);
    setPartial('');
    setError(null);
    setRecorderState('idle');
    setCaptureLevel(0);
    setTranslationQueue(0);
    setAsrLag(null);
    setLiveMessage('准备就绪');
    setTranslationProvider('ollama');
    setTranslationModel('');
    setSidebarOpen(false);
    void loadConfig();
  };

  const handleDelete = async (id: string) => {
    if (isActive || deletingId) return;
    const session = sessions.find((item) => item.id === id);
    if (!session || session.status === 'active') return;
    const confirmed = window.confirm(
      `确定永久删除会话“${session.title}”吗？\n\n该会话的全部原文、译文和 Markdown 记录都会被删除，且无法恢复。`,
    );
    if (!confirmed) return;

    setDeletingId(id);
    setError(null);
    try {
      await deleteSession(id);
      setSessions((current) => current.filter((item) => item.id !== id));
      if (selectedIdRef.current === id) {
        selectedIdRef.current = null;
        setSelectedSession(null);
        setSegments([]);
        setPartial('');
        setRecorderState('idle');
        setCaptureLevel(0);
        setTranslationQueue(0);
        setAsrLag(null);
        setLiveMessage('准备就绪');
        setTranslationProvider('ollama');
        setTranslationModel('');
        void loadConfig();
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setDeletingId(null);
    }
  };

  const handleRetry = async (id: string) => {
    setRetryingId(id);
    setError(null);
    setSegments((current) => current.map((segment) => segment.id === id
      ? { ...segment, translation_status: 'translating', error_message: null }
      : segment));
    try {
      const updated = await retrySegment(id);
      setSegments((current) => upsertSegment(current, updated));
    } catch (reason) {
      const message = errorMessage(reason);
      setSegments((current) => current.map((segment) => segment.id === id
        ? { ...segment, translation_status: 'failed', error_message: message }
        : segment));
      setError(message);
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <div className="app-shell">
      <Sidebar
        sessions={sessions}
        selectedId={selectedSession?.id ?? null}
        locked={isActive}
        deletingId={deletingId}
        privacyProvider={selectedSession ? (selectedSession.translation_provider ?? 'ollama') : translationProvider}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNew={handleNew}
        onSelect={(id) => { if (!isActive) void selectSession(id); }}
        onDelete={(id) => void handleDelete(id)}
      />
      <main className="workspace">
        <WorkspaceHeader
          session={selectedSession}
          viewMode={viewMode}
          documentMode={documentMode}
          showTimestamps={showTimestamps}
          onViewMode={setViewMode}
          onDocumentMode={setDocumentMode}
          onShowTimestamps={setShowTimestamps}
          onOpenSidebar={() => setSidebarOpen(true)}
        />
        {error && <ErrorBanner message={error} onClose={() => setError(null)} />}
        <ControlDeck
          sourceType={sourceType}
          devices={devices}
          deviceId={deviceId}
          applications={applications}
          applicationKey={applicationKey}
          audioPermission={audioPermission}
          captureLevel={captureLevel}
          sourceLanguage={sourceLanguage}
          targetLanguage={targetLanguage}
          config={config}
          configLoading={configLoading}
          translationProvider={translationProvider}
          translationModel={translationModel}
          canStart={canStart}
          recorderState={recorderState}
          liveMessage={liveMessage}
          health={health}
          translationQueue={translationQueue}
          asrLag={asrLag}
          onSourceType={handleSourceType}
          onDevice={setDeviceId}
          onDiscoverDevices={() => void handleDiscoverDevices()}
          onApplication={setApplicationKey}
          onRefreshApplications={() => void handleApplicationRefresh()}
          onRequestAudioPermissions={() => void handleApplicationRefresh(true)}
          onSourceLanguage={setSourceLanguage}
          onTargetLanguage={setTargetLanguage}
          onTranslationProvider={handleTranslationProvider}
          onTranslationModel={setTranslationModel}
          onStart={() => void handleStart()}
          onPause={handlePause}
          onStop={() => void handleStop()}
        />
        <Transcript
          session={selectedSession}
          segments={segments}
          partial={partial}
          viewMode={viewMode}
          documentMode={documentMode}
          showTimestamps={showTimestamps}
          recorderState={recorderState}
          retryingId={retryingId}
          translationProvider={translationProvider}
          startDisabled={!canStart}
          onRetry={(id) => void handleRetry(id)}
          onStart={() => void handleStart()}
        />
      </main>
    </div>
  );
}
