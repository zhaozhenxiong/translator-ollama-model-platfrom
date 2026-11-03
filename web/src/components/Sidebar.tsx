import { AudioLines, CloudUpload, LoaderCircle, MessageSquareText, Plus, ShieldCheck, Trash2, X } from 'lucide-react';
import type { Session, TranslationProvider } from '../types';
import { languageName } from '../types';

interface SidebarProps {
  sessions: Session[];
  selectedId: string | null;
  locked: boolean;
  deletingId: string | null;
  privacyProvider: TranslationProvider;
  open: boolean;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

function formatSessionDate(value: string): string {
  const date = new Date(value);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return new Intl.DateTimeFormat('zh-CN', sameDay
    ? { hour: '2-digit', minute: '2-digit' }
    : { month: 'short', day: 'numeric' }).format(date);
}

export function Sidebar({ sessions, selectedId, locked, deletingId, privacyProvider, open, onClose, onNew, onSelect, onDelete }: SidebarProps) {
  const cloudTranslation = privacyProvider === 'cc_switch';

  return (
    <>
      <button
        type="button"
        className={`sidebar-backdrop ${open ? 'is-visible' : ''}`}
        aria-label="关闭会话列表"
        onClick={onClose}
      />
      <aside className={`sidebar ${open ? 'is-open' : ''}`}>
        <div className="brand">
          <span className="brand-mark"><AudioLines size={23} strokeWidth={1.8} /></span>
          <div>
            <strong>声译</strong>
            <span>本地语音工作台</span>
          </div>
          <button type="button" className="icon-button sidebar-close" onClick={onClose} aria-label="关闭侧栏">
            <X size={19} />
          </button>
        </div>

        <button type="button" className="new-session-button" onClick={onNew} disabled={locked}>
          <Plus size={18} />
          新建听写
        </button>

        <div className="session-list-heading">
          <span>会话记录</span>
          <span className="session-count">{sessions.length}</span>
        </div>

        <nav className="session-list" aria-label="历史会话">
          {sessions.length === 0 ? (
            <div className="sidebar-empty">
              <MessageSquareText size={22} strokeWidth={1.5} />
              <p>完成的听写会保存在这里。</p>
            </div>
          ) : sessions.map((session) => {
            const isDeleting = deletingId === session.id;
            const deleteDisabled = locked || deletingId !== null;
            const deleteTitle = locked
              ? '请先结束当前听写'
              : deletingId !== null
                ? '请等待当前删除完成'
                : '删除会话（不可恢复）';

            return (
              <div className={`session-item-container ${isDeleting ? 'is-deleting' : ''}`} key={session.id}>
                <button
                  type="button"
                  className={`session-item ${selectedId === session.id ? 'is-selected' : ''}`}
                  onClick={() => onSelect(session.id)}
                  disabled={locked && selectedId !== session.id}
                  title={locked && selectedId !== session.id ? '请先结束当前听写' : session.title}
                >
                  <span className="session-item-topline">
                    <strong>{session.title}</strong>
                    <time dateTime={session.created_at}>{formatSessionDate(session.created_at)}</time>
                  </span>
                  <span className="session-meta">
                    {languageName(session.source_language)} → {languageName(session.target_language)}
                  </span>
                  {session.status === 'active' && <span className="session-live-dot">进行中</span>}
                </button>
                <button
                  type="button"
                  className={`session-delete ${isDeleting ? 'is-deleting' : ''}`}
                  onClick={() => onDelete(session.id)}
                  disabled={deleteDisabled}
                  aria-label={isDeleting ? `正在删除会话 ${session.title}` : `删除会话 ${session.title}，此操作不可恢复`}
                  aria-busy={isDeleting}
                  title={deleteTitle}
                >
                  {isDeleting ? <LoaderCircle size={15} className="is-spinning" /> : <Trash2 size={15} />}
                </button>
              </div>
            );
          })}
        </nav>

        <div className={`privacy-note ${cloudTranslation ? 'is-cloud' : ''}`}>
          {cloudTranslation ? <CloudUpload size={18} strokeWidth={1.7} /> : <ShieldCheck size={18} strokeWidth={1.7} />}
          <div>
            <strong>{cloudTranslation ? '音频在本机识别' : '音频与翻译在本机处理'}</strong>
            <span>{cloudTranslation ? '原文会经本机路由发送到云上游' : '原文由本机 Ollama 翻译'}</span>
          </div>
        </div>
      </aside>
    </>
  );
}
