import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { Modal } from './Modal';
import { submitChatPrompt } from './chat';
import { paginationBtn } from './pages/searchDetail/styles';

// Cross-origin from the chat, so we can't detect an existing draft; confirm
// before overwriting it.
type PromptOptions = { title: string; onConfirm?: () => void };
type RequestPrompt = (text: string | null, opts: PromptOptions) => void;

const ChatPromptContext = createContext<RequestPrompt | null>(null);

export function useChatPrompt(): RequestPrompt {
  const ctx = useContext(ChatPromptContext);
  if (!ctx) throw new Error('useChatPrompt must be used within ChatPromptProvider');
  return ctx;
}

export function ChatPromptProvider(props: { children: ReactNode }) {
  const [pending, setPending] = useState<({ text: string } & PromptOptions) | null>(null);

  const requestPrompt = useCallback<RequestPrompt>((text, opts) => {
    if (text) setPending({ text, ...opts });
  }, []);

  const send = () => {
    if (pending) {
      submitChatPrompt(pending.text);
      pending.onConfirm?.();
    }
    setPending(null);
  };

  return (
    <ChatPromptContext.Provider value={requestPrompt}>
      {props.children}
      {pending !== null && (
        <Modal onClose={() => setPending(null)} ariaLabel={pending.title} maxWidth={360}>
          <p style={{ margin: '0 0 0.5rem', fontWeight: 600 }}>{pending.title}</p>
          <p style={{ margin: '0 0 1rem', fontSize: '0.85rem' }}>
            This replaces any unsent text in the chat box.
          </p>
          <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => setPending(null)} style={paginationBtn}>
              Cancel
            </button>
            <button
              type="button"
              onClick={send}
              style={{
                ...paginationBtn,
                background: 'var(--rv-accent)',
                color: '#fff',
                borderColor: 'var(--rv-accent)',
              }}
            >
              Continue
            </button>
          </div>
        </Modal>
      )}
    </ChatPromptContext.Provider>
  );
}
