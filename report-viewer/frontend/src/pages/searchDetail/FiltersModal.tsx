import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { activeFilterCount, type EvCategory, type FilterState } from '../../api/client';
import { Modal } from '../../Modal';
import { paginationBtn } from './styles';

const SEX_OPTIONS = ['M', 'F', 'U'] as const;

const EV_SOURCE_LABEL: Record<EvCategory, string> = {
  text_and_code: 'text + code',
  text: 'text only',
  diagnosis_code: 'code only',
  unknown: 'unexplained',
};

export function FiltersModal(props: {
  initial: FilterState;
  availableColumns: string[];
  modalityOptions?: string[];
  evidenceOptions?: string[];
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

  const evidenceChoices = useMemo<string[]>(() => {
    const base = props.evidenceOptions ?? [];
    return Array.from(new Set([...base, ...(staged.ev_source ?? [])]));
  }, [props.evidenceOptions, staged.ev_source]);

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
  const toggleEnum = (col: 'sex' | 'modality' | 'ev_source', value: string) =>
    setStaged((s) => {
      const cur = new Set(s[col] ?? []);
      if (cur.has(value)) cur.delete(value);
      else cur.add(value);
      const next = Array.from(cur);
      return { ...s, [col]: next.length > 0 ? next : undefined };
    });
  const setStringField = (
    col:
      | 'service_name'
      | 'epic_mrn'
      | 'patient_mpi'
      | 'accession_number'
      | 'sending_facility'
      | 'ev_dx_codes',
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

        {has('modality') && modalityChoices.length > 0 && (
          <FieldRow label="Modality">
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
            <FieldRow label="Matched on">
              <CheckboxRow
                options={evidenceChoices}
                selected={staged.ev_source ?? []}
                onToggle={(v) => toggleEnum('ev_source', v)}
                label={(v) => EV_SOURCE_LABEL[v as EvCategory] ?? v}
              />
            </FieldRow>
            <FieldRow label="Dx code">
              <TextInput
                value={staged.ev_dx_codes ?? ''}
                onChange={(v) => setStringField('ev_dx_codes', v)}
              />
            </FieldRow>
            <FieldRow label="Negation">
              <select
                value={staged.ev_has_negative === undefined ? '' : String(staged.ev_has_negative)}
                onChange={(e) =>
                  setStaged((s) => ({
                    ...s,
                    ev_has_negative: e.target.value === '' ? undefined : e.target.value === 'true',
                  }))
                }
                style={{ fontSize: '0.85rem', padding: '0.25rem' }}
              >
                <option value="">Any</option>
                <option value="true">Report text rules it out</option>
                <option value="false">No contradicting text</option>
              </select>
            </FieldRow>
          </>
        )}

        <div style={{ marginTop: '1rem' }}>
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
  label?: (value: string) => string;
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
          {props.label ? props.label(opt) : opt}
        </label>
      ))}
    </div>
  );
}
