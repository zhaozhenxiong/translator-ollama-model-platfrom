import type {
  AppConfig,
  ApplicationAudioInfo,
  AudioPermission,
  AudioSourceSelection,
  Health,
  Segment,
  Session,
  SessionDetail,
  TranslationProvider,
} from './types';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      detail = body.detail ?? body.message ?? detail;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function getConfig(): Promise<AppConfig> {
  return request<AppConfig>('/api/config');
}

export function listSessions(): Promise<Session[]> {
  return request<Session[]>('/api/sessions');
}

export function getSession(id: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/api/sessions/${id}`);
}

export function deleteSession(id: string): Promise<void> {
  return request<void>(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function createSession(
  sourceLanguage: string,
  targetLanguage: string,
  audioSource: AudioSourceSelection = { type: 'microphone' },
  translationProvider: TranslationProvider,
  translationModel: string,
): Promise<Session> {
  return request<Session>('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_language: sourceLanguage,
      target_language: targetLanguage,
      audio_source: audioSource,
      translation_provider: translationProvider,
      translation_model: translationModel,
    }),
  });
}

export function getAudioPermissions(): Promise<AudioPermission> {
  return request<AudioPermission>('/api/audio-sources/permissions');
}

export function requestAudioPermissions(): Promise<AudioPermission> {
  return request<AudioPermission>('/api/audio-sources/permissions/request', { method: 'POST' });
}

export function listAudioApplications(): Promise<ApplicationAudioInfo[]> {
  return request<ApplicationAudioInfo[]>('/api/audio-sources/applications');
}

export function retrySegment(id: string): Promise<Segment> {
  return request<Segment>(`/api/segments/${id}/retry`, { method: 'POST' });
}

export function getHealth(): Promise<Health> {
  return request<Health>('/api/health');
}

export function liveSocket(sessionId: string): Promise<WebSocket> {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/live?session_id=${encodeURIComponent(sessionId)}`);

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      socket.close();
      reject(new Error('连接本地听写服务超时，请确认后端已经启动。'));
    }, 15_000);

    socket.addEventListener('open', () => {
      window.clearTimeout(timeout);
      resolve(socket);
    }, { once: true });
    socket.addEventListener('error', () => {
      window.clearTimeout(timeout);
      reject(new Error('无法连接本地听写服务，请确认后端已经启动。'));
    }, { once: true });
  });
}

export function exportUrl(sessionId: string, format: 'md' | 'txt' | 'docx', view: string): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}/export?format=${format}&view=${view}`;
}
