import { useState } from 'react';
import { Modal } from '../../Modal';
import { paginationBtn } from './styles';

// XNAT push not wired yet, tracked in ADR 26.
export function SendToXnatModal(props: { searchId: string; total: number; onClose: () => void }) {
  const [project, setProject] = useState('');
  const [irb, setIrb] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const canSubmit = !!project && !!irb && confirmed;
  const onSend = () => {
    alert(
      `TBD - XNAT push not wired yet.\n\nWould send ${props.total} accessions from ${props.searchId} to project "${project}" under IRB ${irb}.`,
    );
    props.onClose();
  };
  return (
    <Modal onClose={props.onClose} minWidth={380} maxWidth={520}>
      <div style={{ fontSize: '0.9rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '1rem' }}>Send to XNAT</h3>
        <p style={{ color: '#555', margin: '0 0 1rem', fontSize: '0.85rem' }}>
          This will push <strong>{props.total.toLocaleString()}</strong> accessions from{' '}
          <code>{props.searchId}</code> to an XNAT project. Walk through each step before sending.
        </p>

        <label style={{ display: 'block', marginBottom: '0.4rem', fontWeight: 600 }}>
          1. XNAT project
        </label>
        <select
          value={project}
          onChange={(e) => setProject(e.target.value)}
          style={{ width: '100%', padding: '0.4rem', marginBottom: '0.75rem' }}
        >
          <option value="">- select project -</option>
          <option value="SCOUT_DEMO">SCOUT_DEMO</option>
          <option value="RADIOLOGY_RES">RADIOLOGY_RES</option>
        </select>

        <label style={{ display: 'block', marginBottom: '0.4rem', fontWeight: 600 }}>
          2. IRB / protocol number
        </label>
        <input
          type="text"
          value={irb}
          onChange={(e) => setIrb(e.target.value)}
          placeholder="e.g. IRB-2024-1234"
          style={{
            width: '100%',
            padding: '0.4rem',
            marginBottom: '0.75rem',
            boxSizing: 'border-box',
          }}
        />

        <label style={{ display: 'block', marginBottom: '0.75rem' }}>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />{' '}
          3. I confirm these reports are covered by the IRB above and that I am authorized to export
          them to XNAT.
        </label>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '0.5rem',
            marginTop: '1rem',
          }}
        >
          <button type="button" onClick={props.onClose} style={paginationBtn}>
            Cancel
          </button>
          <button
            type="button"
            onClick={onSend}
            disabled={!canSubmit}
            style={{
              ...paginationBtn,
              background: canSubmit ? '#4477AA' : '#bbb',
              color: '#fff',
              borderColor: canSubmit ? '#4477AA' : '#bbb',
              cursor: canSubmit ? 'pointer' : 'not-allowed',
            }}
          >
            Send
          </button>
        </div>
      </div>
    </Modal>
  );
}
