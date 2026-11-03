import { useEffect, useRef } from 'react';
import { AudioLines, FileText, Mic, RefreshCw } from 'lucide-react';
import type { DocumentMode, RecorderState, Segment, Session, TranslationProvider, ViewMode } from '../types';
import { languageName } from '../types';

interface TranscriptProps {
  session: Session | null;
  segments: Segment[];
  partial: string;
  viewMode: ViewMode;
  documentMode: DocumentMode;
  showTimestamps: boolean;
  recorderState: RecorderState;
  retryingId: string | null;
  translationProvider: TranslationProvider;
  startDisabled: boolean;
  onRetry: (id: string) => void;
  onStart: () => void;
}

export function formatTimestamp(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);
  return hours > 0
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function TranslationState({ segment, retryingId, onRetry }: Pick<TranscriptProps, 'retryingId' | 'onRetry'> & { segment: Segment }) {
  if (segment.translation_status === 'failed') {
    return (
      <span className="translation-error">
        <span>{segment.error_message || '这段译文未能生成。'}</span>
        <button type="button" onClick={() => onRetry(segment.id)} disabled={retryingId === segment.id}>
          <RefreshCw size={13} className={retryingId === segment.id ? 'is-spinning' : ''} />
          {retryingId === segment.id ? '正在重试' : '重新翻译'}
        </button>
      </span>
    );
  }
  if (segment.translation_status === 'pending') return <span className="translation-placeholder">等待翻译…</span>;
  if (segment.translation_status === 'translating' && !segment.translated_text) {
    return <span className="translation-placeholder"><i className="typing-dot" />正在翻译…</span>;
  }
  return <>{segment.translated_text || <span className="translation-placeholder">译文尚未完成</span>}</>;
}

export function Transcript(props: TranscriptProps) {
  const { session, segments, partial, viewMode, documentMode, showTimestamps, recorderState, onStart } = props;
  const scrollRef = useRef<HTMLDivElement>(null);
  const wasNearBottom = useRef(true);
  const isListening = recorderState === 'live' || recorderState === 'paused' || recorderState === 'stopping';

  useEffect(() => {
    if (viewMode === 'chat' && wasNearBottom.current) {
      const element = scrollRef.current;
      if (element) element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
    }
  }, [segments, partial, viewMode]);

  if (!session) {
    return (
      <div className="empty-workspace">
        <div className="empty-symbol" aria-hidden="true">
          <AudioLines size={42} strokeWidth={1.25} />
        </div>
        <h1>让每一句话，都有迹可循。</h1>
        <p>选择音频来源、语言和翻译模型，开始实时听写、翻译与记录。</p>
        <button type="button" className="primary-button empty-start" onClick={onStart} disabled={props.startDisabled}>
          <Mic size={17} />
          {recorderState === 'starting' ? '正在准备…' : '开始第一次听写'}
        </button>
        <span className="empty-footnote">
          {props.translationProvider === 'cc_switch'
            ? '音频在本机识别；原文会发送到 CC Switch 的云上游'
            : '音频与翻译均在本机完成，无需云端账号'}
        </span>
      </div>
    );
  }

  return (
    <div
      className={`transcript-scroll ${viewMode === 'document' ? 'document-scroll' : ''}`}
      ref={scrollRef}
      onScroll={(event) => {
        const element = event.currentTarget;
        wasNearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 160;
      }}
    >
      {viewMode === 'document' ? (
        <article className="document-page">
          <header className="document-header">
            <span className="document-kicker"><FileText size={15} />听写文档</span>
            <h1>{session.title}</h1>
            <p>
              {new Intl.DateTimeFormat('zh-CN', { dateStyle: 'long', timeStyle: 'short' }).format(new Date(session.created_at))}
              <span> / </span>
              {languageName(session.source_language)} → {languageName(session.target_language)}
            </p>
          </header>
          {segments.length === 0 ? (
            <div className="document-empty">这次会话还没有可显示的听写内容。</div>
          ) : segments.map((segment) => (
            <section className="document-segment" key={segment.id}>
              {showTimestamps && <time>{formatTimestamp(segment.start_ms)}</time>}
              {documentMode !== 'translation' && <p className="document-source">{segment.source_text}</p>}
              {documentMode !== 'source' && (
                <p className="document-translation">
                  <TranslationState segment={segment} retryingId={props.retryingId} onRetry={props.onRetry} />
                </p>
              )}
            </section>
          ))}
        </article>
      ) : (
        <div className="chat-transcript">
          {segments.length === 0 && (
            <div className="listening-empty">
              {isListening ? (
                <>
                  <span className="listening-bars" aria-hidden="true"><i /><i /><i /><i /><i /></span>
                  <h2>{recorderState === 'paused' ? '听写已暂停' : '正在聆听'}</h2>
                  <p>{recorderState === 'paused' ? '继续后，新的语音会出现在这里。' : '开口说话，稳定后的原文和译文会逐段显示。'}</p>
                </>
              ) : (
                <>
                  <AudioLines size={30} strokeWidth={1.4} />
                  <h2>还没有听写内容</h2>
                  <p>这次会话中的稳定原文和译文会显示在这里。</p>
                </>
              )}
            </div>
          )}

          {segments.map((segment) => (
            <article className="transcript-entry" key={segment.id}>
              <div className="entry-rail">
                <time>{formatTimestamp(segment.start_ms)}</time>
                <span>{String(segment.sequence).padStart(2, '0')}</span>
              </div>
              <div className="entry-content">
                <p className="source-text">{segment.source_text}</p>
                <div className={`translation-text status-${segment.translation_status}`}>
                  <span className="translation-marker" aria-hidden="true" />
                  <p><TranslationState segment={segment} retryingId={props.retryingId} onRetry={props.onRetry} /></p>
                </div>
              </div>
            </article>
          ))}

          {partial && isListening && (
            <div className="partial-entry">
              <span className="partial-label">正在识别</span>
              <p>{partial}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
