import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { Modal } from './Modal';
import { submitChatPrompt } from './chat';
import { paginationBtn } from './pages/searchDetail/styles';

// Sending a prompt to chat overwrites whatever the user has typed but not
// sent, and the iframe is cross-origin so we can't tell whether a draft
// exists, hence a blind confirm before every send.
type RequestPrompt = (text: string | null) => void;

const ChatPromptContext = createContext<RequestPrompt | null>(null);

export function useChatPrompt(): RequestPrompt {
  const ctx = useContext(ChatPromptContext);
  if (!ctx) throw new Error('useChatPrompt must be used within ChatPromptProvider');
  return ctx;
}

export function ChatPromptProvider(props: { children: ReactNode }) {
  const [pending, setPending] = useState<string | null>(null);

  const requestPrompt = useCallback<RequestPrompt>((text) => {
    if (text) setPending(text);
  }, []);

  const send = () => {
    if (pending) submitChatPrompt(pending);
    setPending(null);
  };

  return (
    <ChatPromptContext.Provider value={requestPrompt}>
      {props.children}
      {pending !== null && (
        <Modal onClose={() => setPending(null)} ariaLabel="Send to chat" maxWidth={360}>
          <p style={{ margin: '0 0 0.5rem', fontWeight: 600 }}>Send to chat?</p>
          <p style={{ margin: '0 0 1rem', fontSize: '0.85rem' }}>
            This replaces anything typed in the chat box but not yet sent.
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
              Send
            </button>
          </div>
        </Modal>
      )}
    </ChatPromptContext.Provider>
  );
}
