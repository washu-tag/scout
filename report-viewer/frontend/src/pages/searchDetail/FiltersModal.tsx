import { useState, type CSSProperties, type ReactNode } from 'react';
import { activeFilterCount, type FilterState } from '../../api/client';
import { Modal } from '../../Modal';
import { paginationBtn } from './styles';

const SEX_OPTIONS = ['M', 'F', 'U'] as const;
const MODALITY_OPTIONS = [
  '3D',
  'CT',
  'CTA',
  'DXA',
  'ECH',
  'FL',
  'IR',
  'MG',
  'MR',
  'MRA',
  'NM',
  'PET',
  'US',
  'XR',
] as const;

export function FiltersModal(props: {
  initial: FilterState;
  availableColumns: string[];
  onApply: (next: FilterState) => void;
  onRefineInChat: (next: FilterState) => void;
  onClose: () => void;
}) {
  const [staged, setStaged] = useState<FilterState>(props.initial);
  const [needFilters, setNeedFilters] = useState(false);
  const available = new Set(props.availableColumns);
  const has = (col: string) => available.has(col);

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
    col: 'service_name' | 'epic_mrn' | 'accession_number' | 'sending_facility',
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

        {has('modality') && (
          <FieldRow label="Modality">
            <CheckboxRow
              options={MODALITY_OPTIONS as readonly string[]}
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
          <FieldRow label="MRN">
            <TextInput
              value={staged.epic_mrn ?? ''}
              onChange={(v) => setStringField('epic_mrn', v)}
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

        <div
          style={{
            marginTop: '1rem',
            paddingTop: '0.75rem',
            borderTop: '1px solid var(--rv-border)',
          }}
        >
          {needFilters && activeFilterCount(staged) === 0 && (
            <div style={{ fontSize: '0.72rem', color: 'var(--rv-danger)', marginBottom: '0.5rem' }}>
              Select at least one filter to send to chat.
            </div>
          )}
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

function FieldRow(props: { label: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.75rem',
        padding: '0.4rem 0',
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

function TextInput(props: { value: string; onChange: (value: string) => void }) {
  return (
    <input
      type="text"
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
      placeholder="contains…"
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
