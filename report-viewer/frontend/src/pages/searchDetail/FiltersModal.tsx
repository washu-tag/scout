import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { activeFilterCount, type FilterState } from '../../api/client';
import { Modal } from '../../Modal';
import { paginationBtn } from './styles';

const SEX_OPTIONS = ['M', 'F', 'U'] as const;

export function FiltersModal(props: {
  initial: FilterState;
  availableColumns: string[];
  modalityOptions?: string[];
  onApply: (next: FilterState) => void;
  onRefineInChat: (next: FilterState) => void;
  onClose: () => void;
}) {
  const [staged, setStaged] = useState<FilterState>(props.initial);
  const [needFilters, setNeedFilters] = useState(false);
  const available = new Set(props.availableColumns);
  const has = (col: string) => available.has(col);

  // Modalities present in the cohort; union with the selection so a checked value can't vanish.
  const modalityChoices = useMemo<string[]>(() => {
    const base = props.modalityOptions ?? [];
    return Array.from(new Set([...base, ...(staged.modality ?? [])])).sort();
  }, [props.modalityOptions, staged.modality]);

  const setAgeBound = (which: 'min' | 'max', value: string) =>
    setStaged((s) => ({
      ...s,
      patient_age: { ...s.patient_age, [which]: value || undefined },
    }));
  const setDateBound = (which: 'min' | 'max', value: string) =>
    setStaged((s) => ({
      ...s,
      message_dt: { ...s.message_dt, [which]: value || undefined },
    }));
  const toggleEnum = (col: 'sex' | 'modality', value: string) =>
    setStaged((s) => {
      const cur = new Set(s[col] ?? []);
      if (cur.has(value)) cur.delete(value);
      else cur.add(value);
      const next = Array.from(cur);
      return { ...s, [col]: next.length > 0 ? next : undefined };
    });
  const setStringField = (
    col: 'service_name' | 'epic_mrn' | 'patient_mpi' | 'accession_number' | 'sending_facility',
    value: string,
  ) => setStaged((s) => ({ ...s, [col]: value || undefined }));

  return (
    <Modal
      onClose={props.onClose}
      ariaLabel="Filter rows"
      minWidth={420}
      maxWidth={560}
      maxHeight="calc(100vh - 40px)"
      showClose
    >
      <div style={{ fontSize: '0.85rem' }}>
        <h3 style={{ margin: '0 2rem 0.75rem 0', fontSize: '1rem' }}>Filter rows</h3>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))',
            columnGap: '1.25rem',
          }}
        >
          {has('patient_age') && (
            <FieldRow label="Age">
              <RangeInputs
                min={staged.patient_age?.min ?? ''}
                max={staged.patient_age?.max ?? ''}
                inputType="number"
                placeholder={{ min: 'min', max: 'max' }}
                onChange={setAgeBound}
              />
            </FieldRow>
          )}

          {has('sex') && (
            <FieldRow label="Sex">
              <CheckboxRow
                options={SEX_OPTIONS as readonly string[]}
                selected={staged.sex ?? []}
                onToggle={(v) => toggleEnum('sex', v)}
              />
            </FieldRow>
          )}

          {has('modality') && modalityChoices.length > 0 && (
            <FieldRow label="Modality" span>
              <CheckboxRow
                options={modalityChoices}
                selected={staged.modality ?? []}
                onToggle={(v) => toggleEnum('modality', v)}
              />
            </FieldRow>
          )}

          {has('message_dt') && (
            <FieldRow label="Date">
              <RangeInputs
                min={staged.message_dt?.min ?? ''}
                max={staged.message_dt?.max ?? ''}
                inputType="date"
                placeholder={{ min: 'from', max: 'to' }}
                onChange={setDateBound}
              />
            </FieldRow>
          )}

          {has('service_name') && (
            <FieldRow label="Service">
              <TextInput
                value={staged.service_name ?? ''}
                onChange={(v) => setStringField('service_name', v)}
              />
            </FieldRow>
          )}

          {has('epic_mrn') && (
            <FieldRow label="Epic MRN">
              <TextInput
                value={staged.epic_mrn ?? ''}
                onChange={(v) => setStringField('epic_mrn', v)}
              />
            </FieldRow>
          )}

          {has('patient_mpi') && (
            <FieldRow label="Patient MPI">
              <TextInput
                value={staged.patient_mpi ?? ''}
                onChange={(v) => setStringField('patient_mpi', v)}
              />
            </FieldRow>
          )}

          {has('accession_number') && (
            <FieldRow label="Accession">
              <TextInput
                value={staged.accession_number ?? ''}
                onChange={(v) => setStringField('accession_number', v)}
              />
            </FieldRow>
          )}

          {has('sending_facility') && (
            <FieldRow label="Facility">
              <TextInput
                value={staged.sending_facility ?? ''}
                onChange={(v) => setStringField('sending_facility', v)}
              />
            </FieldRow>
          )}

          {has('ev_source') && (
            <>
              <FieldRow label="Dx code">
                <TextInput
                  value={(staged.ev_dx_codes ?? []).join(', ')}
                  placeholder="contains, comma for any of…"
                  onChange={(v) =>
                    setStaged((s) => {
                      const list = v
                        .split(',')
                        .map((c) => c.trim())
                        .filter(Boolean);
                      return { ...s, ev_dx_codes: list.length > 0 ? list : undefined };
                    })
                  }
                />
              </FieldRow>
              {(staged.ev_positive_span || staged.ev_negative_span) && (
                <FieldRow label="Phrase" span>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                    {staged.ev_positive_span && (
                      <SpanChip
                        text={staged.ev_positive_span}
                        onClear={() => setStaged((s) => ({ ...s, ev_positive_span: undefined }))}
                      />
                    )}
                    {staged.ev_negative_span && (
                      <SpanChip
                        negative
                        text={staged.ev_negative_span}
                        onClear={() => setStaged((s) => ({ ...s, ev_negative_span: undefined }))}
                      />
                    )}
                  </div>
                </FieldRow>
              )}
            </>
          )}
        </div>

        <div
          style={{
            marginTop: '1rem',
            // Pinned so Apply stays reachable without scrolling the fields.
            position: 'sticky',
            bottom: '-1.25rem',
            background: 'var(--rv-surface)',
            paddingBottom: '0.25rem',
            boxShadow: '0 -8px 8px -8px rgba(0,0,0,0.15)',
          }}
        >
          <div style={{ fontSize: '0.72rem', color: 'var(--rv-danger)', margin: '0 0 0.5rem' }}>
            {needFilters && activeFilterCount(staged) === 0
              ? 'Select at least one filter first.'
              : ' '}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button type="button" onClick={() => setStaged({})} style={paginationBtn}>
              Reset
            </button>
            <span style={{ flex: 1 }} />
            <button type="button" onClick={props.onClose} style={paginationBtn}>
              Cancel
            </button>
            <button
              type="button"
              onClick={() => {
                if (activeFilterCount(staged) === 0) setNeedFilters(true);
                else props.onRefineInChat(staged);
              }}
              style={paginationBtn}
            >
              Filter in Chat
            </button>
            <button
              type="button"
              onClick={() => props.onApply(staged)}
              style={{
                ...paginationBtn,
                background: 'var(--rv-accent)',
                color: '#fff',
                borderColor: 'var(--rv-accent)',
              }}
            >
              Apply
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

/** Set by clicking a phrase in the stats panel, and matched exactly, so it is
 *  shown to be removed rather than typed. */
function SpanChip(props: { text: string; negative?: boolean; onClear: () => void }) {
  const tone = props.negative ? 'var(--rv-danger)' : 'var(--rv-ev-positive)';
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '1px 4px 1px 8px',
        borderRadius: 999,
        border: `1px solid ${tone}`,
        color: tone,
        fontSize: '0.72rem',
        maxWidth: '100%',
      }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{props.text}</span>
      <button
        type="button"
        onClick={props.onClear}
        aria-label={`Remove phrase filter ${props.text}`}
        style={{
          border: 'none',
          background: 'transparent',
          color: 'inherit',
          cursor: 'pointer',
          padding: '0 2px',
          fontSize: '0.85rem',
          lineHeight: 1,
        }}
      >
        x
      </button>
    </span>
  );
}

function FieldRow(props: { label: string; children: ReactNode; span?: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.75rem',
        padding: '0.4rem 0',
        // Wide controls read badly in half a column.
        ...(props.span ? { gridColumn: '1 / -1' } : {}),
      }}
    >
      <div style={{ width: 80, color: 'var(--rv-muted)', fontWeight: 600, paddingTop: '0.25rem' }}>
        {props.label}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>{props.children}</div>
    </div>
  );
}

function RangeInputs(props: {
  min: string;
  max: string;
  inputType: 'number' | 'date';
  placeholder: { min: string; max: string };
  onChange: (which: 'min' | 'max', value: string) => void;
}) {
  const style: CSSProperties = {
    flex: 1,
    minWidth: 0,
    fontSize: '0.85rem',
    padding: '0.3rem 0.45rem',
    border: '1px solid var(--rv-border)',
    borderRadius: 3,
    boxSizing: 'border-box',
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
      <input
        type={props.inputType}
        value={props.min}
        onChange={(e) => props.onChange('min', e.target.value)}
        placeholder={props.placeholder.min}
        style={style}
      />
      <span style={{ color: 'var(--rv-muted)' }}>-</span>
      <input
        type={props.inputType}
        value={props.max}
        onChange={(e) => props.onChange('max', e.target.value)}
        placeholder={props.placeholder.max}
        style={style}
      />
    </div>
  );
}

function TextInput(props: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
      placeholder={props.placeholder ?? 'contains…'}
      style={{
        width: '100%',
        fontSize: '0.85rem',
        padding: '0.3rem 0.45rem',
        border: '1px solid var(--rv-border)',
        borderRadius: 3,
        boxSizing: 'border-box',
      }}
    />
  );
}

function CheckboxRow(props: {
  options: readonly string[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  const set = new Set(props.selected);
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem 0.75rem' }}>
      {props.options.map((opt) => (
        <label
          key={opt}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem',
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          <input type="checkbox" checked={set.has(opt)} onChange={() => props.onToggle(opt)} />
          {opt}
        </label>
      ))}
    </div>
  );
}
