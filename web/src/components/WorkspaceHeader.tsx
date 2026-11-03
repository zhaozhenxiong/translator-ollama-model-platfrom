import { AlertCircle, Check, ChevronDown, Download, Eye, EyeOff, FileText, Menu, MessageSquareText } from 'lucide-react';
import type { DocumentMode, Session, ViewMode } from '../types';
import { translationProviderLabel } from '../types';

interface WorkspaceHeaderProps {
  session: Session | null;
  viewMode: ViewMode;
  documentMode: DocumentMode;
  showTimestamps: boolean;
  onViewMode: (mode: ViewMode) => void;
  onDocumentMode: (mode: DocumentMode) => void;
  onShowTimestamps: (show: boolean) => void;
  onOpenSidebar: () => void;
}

export function WorkspaceHeader(props: WorkspaceHeaderProps) {
  const { session, viewMode, documentMode, showTimestamps } = props;
  const exportView = viewMode === 'document' ? documentMode : 'bilingual';
  const provider = session?.translation_provider ?? 'ollama';
  const model = session?.translation_model || session?.ollama_model || '';

  return (
    <header className="workspace-header">
      <div className="workspace-title">
        <button type="button" className="icon-button mobile-menu" onClick={props.onOpenSidebar} aria-label="打开会话列表">
          <Menu size={20} />
        </button>
        <div>
          <h1>{session?.title ?? '新的听写会话'}</h1>
          <p>{session ? `${session.audio_source_type === 'application' ? `应用：${session.audio_source_display_name}` : '麦克风'} · ${session.asr_model} · ${translationProviderLabel(provider)} · ${model}` : '本地实时听写与翻译'}</p>
        </div>
      </div>

      <div className="workspace-actions">
        <div className="view-switch" aria-label="显示方式">
          <button type="button" className={viewMode === 'chat' ? 'is-active' : ''} onClick={() => props.onViewMode('chat')}>
            <MessageSquareText size={16} />聊天
          </button>
          <button type="button" className={viewMode === 'document' ? 'is-active' : ''} onClick={() => props.onViewMode('document')}>
            <FileText size={16} />文档
          </button>
        </div>

        {session && viewMode === 'document' && (
          <>
            <div className="document-mode-select">
              <select value={documentMode} onChange={(event) => props.onDocumentMode(event.target.value as DocumentMode)} aria-label="文档内容">
                <option value="bilingual">双语对照</option>
                <option value="source">仅原文</option>
                <option value="translation">仅译文</option>
              </select>
              <ChevronDown size={14} />
            </div>
            <button type="button" className="icon-button timestamp-button" onClick={() => props.onShowTimestamps(!showTimestamps)} title={showTimestamps ? '隐藏时间戳' : '显示时间戳'}>
              {showTimestamps ? <Eye size={17} /> : <EyeOff size={17} />}
            </button>
          </>
        )}

        {session && (
          <div className="export-menu">
            <button type="button" className="secondary-button export-trigger">
              <Download size={16} />导出<ChevronDown size={14} />
            </button>
            <div className="export-popover">
              <span>下载当前会话</span>
              {(['md', 'txt', 'docx'] as const).map((format) => (
                <a key={format} href={`/api/sessions/${session.id}/export?format=${format}&view=${exportView}`} download>
                  <FileText size={15} />
                  <strong>{format === 'md' ? 'Markdown' : format === 'txt' ? '纯文本' : 'Word 文档'}</strong>
                  <small>.{format}</small>
                </a>
              ))}
              <p><Check size={13} />按当前阅读方式导出</p>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}

export function ErrorBanner({ message, onClose }: { message: string; onClose: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      <button type="button" onClick={onClose}>知道了</button>
    </div>
  );
}
