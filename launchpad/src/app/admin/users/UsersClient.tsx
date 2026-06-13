'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useSession, signIn } from 'next-auth/react';
import TopBar from '@/components/TopBar';
import Brand from '@/components/Brand';
import { canManageUsers } from '@/lib/roles';
import { subdomainUrl } from '@/lib/subdomainUrl';
import {
  HiCheckCircle,
  HiChevronLeft,
  HiChevronRight,
  HiExternalLink,
  HiSearch,
  HiShieldCheck,
  HiTrash,
  HiUserGroup,
  HiX,
} from 'react-icons/hi';

interface AttrSchema {
  name: string;
  displayName: string;
  multivalued: boolean;
  inputType: string;
  options: string[] | null;
  defaultValue: string | null;
}

// Wire shapes from the SPI: /pending returns PendingUser (carries requestedAt);
// /users returns ScoutUser (carries status + isAdmin + current attributes). Both
// normalize into Row so one table + one drawer serve every tab.
interface PendingUser {
  id: string;
  username: string;
  email: string | null;
  name: string | null;
  requestedAt: string | null;
}

interface ScoutUser {
  id: string;
  username: string;
  email: string | null;
  name: string | null;
  status: string;
  isAdmin: boolean;
  isUserManager: boolean;
  attributes: Record<string, string[]> | null;
}

interface Row {
  id: string;
  username: string;
  email: string | null;
  name: string | null;
  requestedAt: string | null;
  status: string;
  isAdmin: boolean;
  isUserManager: boolean;
  attributes: Record<string, string[]>;
}

type Tab = 'pending' | 'active' | 'admin';

function pendingToRow(p: PendingUser): Row {
  return { ...p, status: 'pending', isAdmin: false, isUserManager: false, attributes: {} };
}

function scoutToRow(u: ScoutUser): Row {
  return {
    id: u.id,
    username: u.username,
    email: u.email,
    name: u.name,
    requestedAt: null,
    status: u.status,
    isAdmin: u.isAdmin,
    isUserManager: u.isUserManager,
    attributes: u.attributes ?? {},
  };
}

function isBoolean(attr: AttrSchema): boolean {
  const o = attr.options;
  return !attr.multivalued && !!o && o.length === 2 && o.includes('true') && o.includes('false');
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}

function requestedLabel(ms: string | null): string {
  if (!ms) return '—';
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return new Date(n).toLocaleDateString();
}

// A compact, glanceable summary of a user's data-access for the table, e.g.
// "all facilities · MR, CT · phi masked". Driven by the same schema as the
// editor. A short noun is derived from each attribute name because the configured
// displayName is verbose ("Scout Authz — allowed_facilities"). Wildcard "*" reads
// as "all" (not "1"), and an empty dimension reads as "no <noun>" — a zero-row
// state worth surfacing, not hiding.
function attrSummary(attributes: Record<string, string[]>, schema: AttrSchema[]): string {
  const parts: string[] = [];
  for (const a of schema) {
    const v = attributes[a.name];
    const noun = a.name.replace(/^allowed_/, '').replace(/_/g, ' ');
    if (isBoolean(a)) {
      if (v && v[0] === 'true') parts.push(noun);
    } else if (a.multivalued) {
      if (v && v.includes('*')) parts.push(`all ${noun}`);
      else if (!v || v.length === 0) parts.push(`no ${noun}`);
      else if (v.length <= 3) parts.push(v.join(', '));
      else parts.push(`${v.length} ${noun}`);
    } else if (v && v.length > 0) {
      parts.push(v[0]);
    }
  }
  return parts.join(' · ');
}

// Case-insensitive client-side filter for the search box (name / username / email).
function matchesQuery(r: Row, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [r.name, r.username, r.email].some((f) => f?.toLowerCase().includes(q));
}

function StatusBadge({ status, isAdmin }: { status: string; isAdmin: boolean }) {
  const [label, cls] =
    isAdmin || status === 'admin'
      ? ['Admin', 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300']
      : status === 'active'
        ? ['Active', 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300']
        : status === 'pending'
          ? ['Pending', 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300']
          : ['—', 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400'];
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>
      {label}
    </span>
  );
}

// Orthogonal marker beside the status badge: user-manager is a delegated role,
// not a status (a manager stays "Active").
function ManagerBadge() {
  return (
    <span className="inline-block rounded-full px-2.5 py-0.5 text-xs font-medium bg-teal-50 dark:bg-teal-950/40 text-teal-700 dark:text-teal-300">
      Manager
    </span>
  );
}

type SortCol = 'name' | 'requested';
type SortDir = 'asc' | 'desc';

// Sort by column/direction. Undated rows (no request timestamp) always sort
// last regardless of direction.
function sortRows(list: Row[], col: SortCol, dir: SortDir): Row[] {
  const sign = dir === 'asc' ? 1 : -1;
  return list.slice().sort((a, b) => {
    if (col === 'name') {
      return sign * (a.name || a.username).localeCompare(b.name || b.username);
    }
    const at = Number(a.requestedAt) || null;
    const bt = Number(b.requestedAt) || null;
    if (at === null && bt === null) return 0;
    if (at === null) return 1;
    if (bt === null) return -1;
    return sign * (at - bt);
  });
}

// --- Editor drawer (approve a pending user, or manage an existing one) -----

type Confirm = null | 'promote' | 'demote' | 'makeManager' | 'removeManager' | 'offboard';

interface DrawerProps {
  user: Row;
  schema: AttrSchema[];
  /** Whether the caller is a full admin; user managers see no admin actions and admins read-only. */
  callerIsAdmin: boolean;
  position?: { index: number; total: number };
  onNavigate?: (delta: number) => void;
  onClose: () => void;
  onDone: (message: string) => void;
  onAuthExpired: () => void;
}

// Seed each control from the user's current value if set, else the schema's
// server default. So approve mode starts at defaults (mask=true, bypass=false)
// and edit mode starts pre-filled with what the user already has.
function seedValues(user: Row, schema: AttrSchema[]): Record<string, string[]> {
  const init: Record<string, string[]> = {};
  for (const a of schema) {
    const current = user.attributes[a.name];
    if (current && current.length > 0) {
      init[a.name] = current;
    } else if (isBoolean(a)) {
      init[a.name] = [a.defaultValue === 'true' ? 'true' : 'false'];
    } else if (a.multivalued) {
      init[a.name] = [];
    } else if (a.options && a.options.length > 0) {
      init[a.name] = [a.defaultValue ?? a.options[0]];
    } else {
      init[a.name] = a.defaultValue ? [a.defaultValue] : [];
    }
  }
  return init;
}

// A 401 from the /api/users proxy means the SSO session backing our refresh
// token is gone (Keycloak redeploy, a logout elsewhere, idle/max timeout), so
// the proxy couldn't mint a token — distinct from a 403, which is a genuine
// "not scout-admin". Throw a sentinel so callers prompt re-login instead of
// surfacing the misleading authz message.
const SESSION_EXPIRED = 'SESSION_EXPIRED';

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(path, init);
  if (r.status === 401) throw new Error(SESSION_EXPIRED);
  return r;
}

function UserDrawer({
  user,
  schema,
  callerIsAdmin,
  position,
  onNavigate,
  onClose,
  onDone,
  onAuthExpired,
}: DrawerProps) {
  const mode: 'approve' | 'edit' = user.status === 'pending' ? 'approve' : 'edit';
  // Mirrors the server guard: a user manager can't modify an admin, so show
  // the admin's access read-only instead of surfacing a raw 403 on save.
  const readOnly = !callerIsAdmin && user.isAdmin;
  const [shown, setShown] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);

  const [values, setValues] = useState<Record<string, string[]>>(() => seedValues(user, schema));

  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);

  // Re-seed and reset transient state when navigating to a different user
  // (Prev/Next) or when the schema finishes loading — the drawer stays mounted
  // and swaps content in place rather than re-animating.
  useEffect(() => {
    setValues(seedValues(user, schema));
    setConfirm(null);
    setError(null);
    setSubmitting(false);
  }, [user, schema]);

  const close = useCallback(() => {
    setShown(false);
    setTimeout(onClose, 250);
  }, [onClose]);

  const toggleChip = (name: string, opt: string) => {
    setValues((v) => {
      const cur = v[name] ?? [];
      // "*" means All and is mutually exclusive with specific picks: the wildcard
      // dominates in OPA (a filter with "*" is disabled), so keeping both would be
      // meaningless. Toggling All on clears specifics; picking a specific clears All.
      if (opt === '*') {
        return { ...v, [name]: cur.includes('*') ? [] : ['*'] };
      }
      const base = cur.filter((x) => x !== '*');
      const next = base.includes(opt) ? base.filter((x) => x !== opt) : [...base, opt];
      return { ...v, [name]: next };
    });
  };

  // Run an action against the proxy; surface the server's message (e.g. the
  // self-offboard 409) in the error slot rather than assuming success.
  const run = async (method: string, path: string, successMsg: string, body?: object) => {
    setSubmitting(true);
    setError(null);
    try {
      const r = await apiFetch(path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) {
        throw new Error((await r.text()) || `${r.status} ${r.statusText}`);
      }
      onDone(successMsg);
    } catch (e) {
      if ((e as Error).message === SESSION_EXPIRED) {
        onAuthExpired();
        return;
      }
      setSubmitting(false);
      setConfirm(null);
      setError((e as Error).message);
    }
  };

  const collectAttributes = () => {
    const attributes: Record<string, string[]> = {};
    for (const a of schema) {
      const val = values[a.name] ?? [];
      // Approve sends only the dimensions the admin set; edit sends every key
      // (including emptied ones) so clearing a chip actually clears it server-side.
      if (mode === 'approve') {
        if (val.length > 0) attributes[a.name] = val;
      } else {
        attributes[a.name] = val;
      }
    }
    return attributes;
  };

  const display = user.name || user.username;

  const saveAttributes = () =>
    mode === 'approve'
      ? run('POST', '/api/users/approve', `Approved ${display}`, {
          userId: user.id,
          attributes: collectAttributes(),
        })
      : run('POST', `/api/users/users/${user.id}/attributes`, `Updated ${display}`, {
          attributes: collectAttributes(),
        });

  const confirmAction = () => {
    if (confirm === 'promote') {
      run('POST', `/api/users/users/${user.id}/admin`, `${display} is now an admin`);
    } else if (confirm === 'demote') {
      run('DELETE', `/api/users/users/${user.id}/admin`, `${display} removed from admins`);
    } else if (confirm === 'makeManager') {
      run('POST', `/api/users/users/${user.id}/manager`, `${display} is now a user manager`);
    } else if (confirm === 'removeManager') {
      run('DELETE', `/api/users/users/${user.id}/manager`, `${display} removed from user managers`);
    } else if (confirm === 'offboard') {
      run('DELETE', `/api/users/users/${user.id}/membership`, `Offboarded ${display}`);
    }
  };

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true">
      <div
        className={`absolute inset-0 bg-slate-900/30 backdrop-blur-[1px] transition-opacity duration-250 ${shown ? 'opacity-100' : 'opacity-0'}`}
        onClick={close}
      />
      <aside
        className={`absolute right-0 top-0 h-full w-full max-w-md bg-white dark:bg-slate-900 shadow-xl border-l border-slate-200 dark:border-slate-800 flex flex-col transition-transform duration-250 ${shown ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Identity header */}
        <div className="flex items-start gap-3 p-6 border-b border-slate-200 dark:border-slate-800">
          <div className="w-11 h-11 rounded-full bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400 font-semibold flex items-center justify-center flex-shrink-0">
            {initials(display)}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-slate-900 dark:text-white truncate flex items-center gap-2">
              {display}
              <StatusBadge status={user.status} isAdmin={user.isAdmin} />
              {user.isUserManager && <ManagerBadge />}
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 truncate">
              {[user.username, user.email].filter(Boolean).join(' · ')}
            </p>
            {mode === 'approve' && (
              <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">
                Requested {requestedLabel(user.requestedAt)}
              </p>
            )}
          </div>
          <button
            onClick={close}
            aria-label="Close"
            className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
          >
            <HiX className="text-xl" />
          </button>
        </div>

        {/* Body: attribute form + (edit mode) role & access actions */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          <div className={`space-y-6${readOnly ? ' opacity-60 pointer-events-none' : ''}`}>
            {schema.map((attr) => (
              <Control
                key={attr.name}
                attr={attr}
                value={values[attr.name] ?? []}
                onChipToggle={(opt) => toggleChip(attr.name, opt)}
                onValue={(val) => setValues((v) => ({ ...v, [attr.name]: val }))}
              />
            ))}
          </div>

          {readOnly && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Admins are managed by admins — ask a Scout admin to change this user.
            </p>
          )}

          {mode === 'edit' && !readOnly && (
            <div className="pt-2 border-t border-slate-200 dark:border-slate-800 space-y-3">
              <span className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                Role &amp; access
              </span>
              <div className="flex flex-wrap gap-2">
                {callerIsAdmin &&
                  (user.isAdmin ? (
                    <button
                      onClick={() => setConfirm('demote')}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-sm text-slate-700 dark:text-slate-200 hover:border-slate-300 dark:hover:border-slate-600 transition-colors"
                    >
                      <HiShieldCheck className="text-base" /> Demote from admin
                    </button>
                  ) : (
                    <button
                      onClick={() => setConfirm('promote')}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-sm text-slate-700 dark:text-slate-200 hover:border-slate-300 dark:hover:border-slate-600 transition-colors"
                    >
                      <HiShieldCheck className="text-base" /> Promote to admin
                    </button>
                  ))}
                {!user.isAdmin &&
                  (user.isUserManager ? (
                    <button
                      onClick={() => setConfirm('removeManager')}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-sm text-slate-700 dark:text-slate-200 hover:border-slate-300 dark:hover:border-slate-600 transition-colors"
                    >
                      <HiUserGroup className="text-base" /> Remove user manager
                    </button>
                  ) : (
                    <button
                      onClick={() => setConfirm('makeManager')}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-sm text-slate-700 dark:text-slate-200 hover:border-slate-300 dark:hover:border-slate-600 transition-colors"
                    >
                      <HiUserGroup className="text-base" /> Make user manager
                    </button>
                  ))}
                <button
                  onClick={() => setConfirm('offboard')}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 dark:border-rose-900/60 px-3 py-1.5 text-sm text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors"
                >
                  <HiTrash className="text-base" /> Remove access
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Footer: confirm prompt, or the primary save/approve action */}
        {confirm ? (
          <ConfirmFooter
            confirm={confirm}
            display={display}
            error={error}
            submitting={submitting}
            onCancel={() => {
              setConfirm(null);
              setError(null);
            }}
            onConfirm={confirmAction}
          />
        ) : (
          <div className="flex items-center gap-3 p-6 border-t border-slate-200 dark:border-slate-800">
            {position && onNavigate && (
              <div className="flex items-center gap-0.5 text-slate-400 dark:text-slate-500">
                <button
                  onClick={() => onNavigate(-1)}
                  disabled={position.index <= 0}
                  aria-label="Previous user"
                  className="p-1 rounded hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-30 disabled:cursor-default transition-colors"
                >
                  <HiChevronLeft className="text-lg" />
                </button>
                <span className="text-xs tabular-nums select-none">
                  {position.index + 1} / {position.total}
                </span>
                <button
                  onClick={() => onNavigate(1)}
                  disabled={position.index >= position.total - 1}
                  aria-label="Next user"
                  className="p-1 rounded hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-30 disabled:cursor-default transition-colors"
                >
                  <HiChevronRight className="text-lg" />
                </button>
              </div>
            )}
            {error ? (
              <span className="text-sm text-rose-600 dark:text-rose-400 flex-1">{error}</span>
            ) : (
              <span className="flex-1" />
            )}
            <button
              onClick={close}
              className="px-4 py-2 text-sm font-medium text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors"
            >
              {readOnly ? 'Close' : 'Cancel'}
            </button>
            {!readOnly && (
              <button
                onClick={saveAttributes}
                disabled={submitting}
                className="inline-flex items-center gap-2 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-semibold transition-colors"
              >
                <HiCheckCircle className="text-base" />
                {submitting
                  ? mode === 'approve'
                    ? 'Approving…'
                    : 'Saving…'
                  : mode === 'approve'
                    ? 'Approve'
                    : 'Save attributes'}
              </button>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

// Confirm prompt shown in the drawer footer for the privileged actions.
function ConfirmFooter({
  confirm,
  display,
  error,
  submitting,
  onCancel,
  onConfirm,
}: {
  confirm: Exclude<Confirm, null>;
  display: string;
  error: string | null;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const copy = {
    promote: {
      text: `Promote ${display} to admin? This grants full Keycloak realm-admin, not just Scout app admin.`,
      label: 'Promote',
      danger: false,
    },
    demote: {
      text: `Remove ${display}'s admin role? They keep their Scout access.`,
      label: 'Demote',
      danger: false,
    },
    makeManager: {
      text: `Make ${display} a user manager? They can approve users, edit data access, and manage other user managers — but not admins.`,
      label: 'Make manager',
      danger: false,
    },
    removeManager: {
      text: `Remove ${display}'s user manager role? They keep their Scout access.`,
      label: 'Remove manager',
      danger: false,
    },
    offboard: {
      text: `Remove all of ${display}'s Scout access? They return to the Pending list.`,
      label: 'Remove access',
      danger: true,
    },
  }[confirm];

  return (
    <div className="p-6 border-t border-slate-200 dark:border-slate-800 space-y-3">
      <p className="text-sm text-slate-600 dark:text-slate-300">{copy.text}</p>
      {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      <div className="flex items-center justify-end gap-3">
        <button
          onClick={onCancel}
          className="px-4 py-2 text-sm font-medium text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={submitting}
          className={`px-5 py-2 rounded-lg text-white text-sm font-semibold disabled:opacity-50 transition-colors ${
            copy.danger ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700'
          }`}
        >
          {submitting ? 'Working…' : copy.label}
        </button>
      </div>
    </div>
  );
}

// --- One form control per schema attribute --------------------------------

interface ControlProps {
  attr: AttrSchema;
  value: string[];
  onChipToggle: (opt: string) => void;
  onValue: (val: string[]) => void;
}

function Control({ attr, value, onChipToggle, onValue }: ControlProps) {
  const label = attr.displayName || attr.name;

  if (attr.multivalued && attr.options && attr.options.length > 0) {
    // "*" becomes an explicit "All" pill rather than a cryptic asterisk; the rest
    // are specific values. All and specifics are mutually exclusive (toggleChip
    // enforces it), and when All is on the specifics are dimmed since they're
    // subsumed. An empty selection is a real state — zero rows — so it's called out.
    const allOn = value.includes('*');
    const none = value.length === 0;
    const items = [
      ...(attr.options.includes('*') ? [{ opt: '*', text: 'All', dim: false }] : []),
      ...attr.options.filter((o) => o !== '*').map((o) => ({ opt: o, text: o, dim: allOn })),
    ];
    return (
      <div>
        <span className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
          {label}
        </span>
        <div className="flex flex-wrap gap-2">
          {items.map(({ opt, text, dim }) => {
            const on = value.includes(opt);
            return (
              <button
                key={opt}
                type="button"
                onClick={() => onChipToggle(opt)}
                className={`rounded-full border px-3 py-1 text-sm transition-colors ${
                  on
                    ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 font-medium'
                    : `border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600${dim ? ' opacity-50' : ''}`
                }`}
              >
                {text}
              </button>
            );
          })}
        </div>
        {allOn && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            Full access — no restriction on this dimension.
          </p>
        )}
        {none && (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
            No access — this user will see 0 rows.
          </p>
        )}
      </div>
    );
  }

  if (isBoolean(attr)) {
    const checked = value[0] === 'true';
    return (
      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onValue([e.target.checked ? 'true' : 'false'])}
          className="mt-0.5 h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-indigo-600 focus:ring-indigo-500"
        />
        <span className="text-sm text-slate-700 dark:text-slate-200">{label}</span>
      </label>
    );
  }

  if (attr.options && attr.options.length > 0) {
    return (
      <div>
        <span className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
          {label}
        </span>
        <select
          value={value[0] ?? ''}
          onChange={(e) => onValue([e.target.value])}
          className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-700 dark:text-slate-200"
        >
          {attr.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div>
      <span className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
        {label}
      </span>
      <input
        type="text"
        value={value.join(', ')}
        placeholder={attr.multivalued ? 'comma-separated' : ''}
        onChange={(e) =>
          onValue(
            e.target.value
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
        className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-700 dark:text-slate-200"
      />
    </div>
  );
}

// --- Page -----------------------------------------------------------------

const TABS: { key: Tab; label: string }[] = [
  { key: 'pending', label: 'Pending' },
  { key: 'active', label: 'Active' },
  { key: 'admin', label: 'Admins' },
];

export default function UsersClient({ scoutEnv, docsUrl }: { scoutEnv?: string; docsUrl: string }) {
  const { data: session, status } = useSession();
  const environment = scoutEnv ?? 'local';
  const [schema, setSchema] = useState<AttrSchema[]>([]);
  const [tab, setTab] = useState<Tab>('pending');
  const [rows, setRows] = useState<Row[] | null>(null);
  const [selected, setSelected] = useState<Row | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [authExpired, setAuthExpired] = useState(false);
  const [query, setQuery] = useState('');
  const [toast, setToast] = useState<string | null>(null);
  const [kcConsole, setKcConsole] = useState('');
  const [deepLink, setDeepLink] = useState<string | null>(null);
  const [sort, setSort] = useState<{ col: SortCol; dir: SortDir }>({
    col: 'requested',
    dir: 'desc',
  });

  const toggleSort = (col: SortCol) =>
    setSort((s) =>
      s.col === col
        ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { col, dir: col === 'requested' ? 'desc' : 'asc' },
    );

  const isAdmin = !!session?.user?.isAdmin;
  // Admins hold manage-users via the scout-admin group's role mapping, so one
  // capability check gates the console for both roles.
  const canManage = canManageUsers(session?.user?.roles);

  useEffect(() => {
    if (status !== 'loading' && !session) signIn('keycloak');
  }, [status, session]);

  useEffect(() => {
    setKcConsole(subdomainUrl('keycloak', '/admin/scout/console'));
    setDeepLink(new URLSearchParams(window.location.search).get('user'));
  }, []);

  // Schema once (drives the editor form regardless of tab).
  useEffect(() => {
    if (!canManage) return;
    apiFetch('/api/users/schema')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error())))
      .then((s) => setSchema(s as AttrSchema[]))
      .catch((e) =>
        (e as Error).message === SESSION_EXPIRED
          ? setAuthExpired(true)
          : setError(
              'Could not load the attribute schema. You may not have access to user administration.',
            ),
      );
  }, [canManage]);

  // Pending uses /pending (carries requestedAt for the queue sort); active and
  // admin use /users?status= (carries current attributes for the editor).
  const loadRows = useCallback(async (t: Tab) => {
    setRows(null);
    setError(null);
    try {
      if (t === 'pending') {
        const r = await apiFetch('/api/users/pending');
        if (!r.ok) throw new Error();
        setRows(((await r.json()) as PendingUser[]).map(pendingToRow));
      } else {
        const r = await apiFetch(`/api/users/users?status=${t}`);
        if (!r.ok) throw new Error();
        setRows(((await r.json()) as ScoutUser[]).map(scoutToRow));
      }
    } catch (e) {
      if ((e as Error).message === SESSION_EXPIRED) setAuthExpired(true);
      else setError('Could not load users. You may not have access to user administration.');
    }
  }, []);

  useEffect(() => {
    if (canManage) loadRows(tab);
  }, [canManage, tab, loadRows]);

  // Email deep-link (?user=<id>) opens that pending user's drawer — once the
  // schema has loaded (so the editor isn't seeded empty), and only once (clear
  // it after, or it would re-open the drawer after the admin closes it).
  useEffect(() => {
    if (deepLink && tab === 'pending' && rows && schema.length > 0) {
      const u = rows.find((x) => x.id === deepLink);
      if (u) {
        setSelected(u);
        setDeepLink(null);
      }
    }
  }, [deepLink, tab, rows, schema]);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(id);
  }, [toast]);

  const sorted = rows
    ? sortRows(rows, sort.col, sort.dir).filter((r) => matchesQuery(r, query))
    : [];
  const selectedIndex = selected ? sorted.findIndex((r) => r.id === selected.id) : -1;

  const onDone = (message: string) => {
    setToast(message);
    // Pending is a processing queue: after approving, advance to the next pending
    // user (close when the queue is empty) so an admin can work straight through.
    // Every other action (edit / promote / demote / offboard) just closes.
    setSelected(tab === 'pending' ? (sorted[selectedIndex + 1] ?? null) : null);
    loadRows(tab);
  };
  const showRequested = tab === 'pending';
  const actionLabel = tab === 'pending' ? 'Review' : 'Manage';

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-indigo-50/40 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/30">
      {/* Header */}
      <div className="border-b border-slate-200 dark:border-slate-800 bg-white/70 dark:bg-slate-900/60 backdrop-blur">
        <div className="max-w-content mx-auto px-6 py-6 flex items-center justify-between">
          <Brand crumbs={[environment, 'Users']} />
          <div className="flex items-center gap-4">
            {/* Keycloak's own console requires realm-admin, which user managers don't have. */}
            {kcConsole && isAdmin && (
              <a
                href={kcConsole}
                target="_blank"
                rel="noopener noreferrer"
                className="hidden sm:inline-flex items-center gap-1.5 text-sm text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors"
              >
                <HiExternalLink className="text-base" />
                Open in Keycloak
              </a>
            )}
            <TopBar docsUrl={docsUrl} />
          </div>
        </div>
      </div>

      <main className="max-w-content mx-auto px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-white tracking-tight">
            User administration
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            {isAdmin
              ? 'Approve access requests, edit data-access attributes, manage admins, and offboard users.'
              : 'Approve access requests, edit data-access attributes, and manage user managers.'}
          </p>
        </div>

        {status === 'loading' || !session ? (
          <Notice>Loading…</Notice>
        ) : authExpired ? (
          <Notice>
            <div className="flex flex-col items-center gap-3">
              <p>Your session expired. Sign in again to continue — your access is unchanged.</p>
              <button
                onClick={() => signIn('keycloak')}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold transition-colors"
              >
                Sign in again
              </button>
            </div>
          </Notice>
        ) : !canManage ? (
          <Notice>You need to be a Scout admin or user manager to administer users.</Notice>
        ) : (
          <>
            {/* Segmented tab filter + search */}
            <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
              <div className="inline-flex rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-1">
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setTab(t.key)}
                    className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                      tab === t.key
                        ? 'bg-indigo-600 text-white'
                        : 'text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
              <div className="relative">
                <HiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search name, username, email…"
                  className="w-64 max-w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 py-1.5 pl-9 pr-3 text-sm text-slate-700 dark:text-slate-200 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>

            {error ? (
              <Notice>{error}</Notice>
            ) : rows === null ? (
              <Notice>Loading…</Notice>
            ) : sorted.length === 0 ? (
              query.trim() ? (
                <Notice>No users match “{query.trim()}”.</Notice>
              ) : (
                <EmptyState tab={tab} />
              )
            ) : (
              <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                      <th className="px-5 py-3 font-semibold">
                        <button
                          onClick={() => toggleSort('name')}
                          className="inline-flex items-center gap-1 uppercase tracking-wider hover:text-slate-700 dark:hover:text-slate-200"
                        >
                          User
                          {sort.col === 'name' && (
                            <span className="text-[10px]">{sort.dir === 'asc' ? '▲' : '▼'}</span>
                          )}
                        </button>
                      </th>
                      <th className="px-5 py-3 font-semibold hidden sm:table-cell">Email</th>
                      <th className="px-5 py-3 font-semibold">Status</th>
                      {showRequested ? (
                        <th className="px-5 py-3 font-semibold hidden md:table-cell">
                          <button
                            onClick={() => toggleSort('requested')}
                            className="inline-flex items-center gap-1 uppercase tracking-wider hover:text-slate-700 dark:hover:text-slate-200"
                          >
                            Requested
                            {sort.col === 'requested' && (
                              <span className="text-[10px]">{sort.dir === 'asc' ? '▲' : '▼'}</span>
                            )}
                          </button>
                        </th>
                      ) : (
                        <th className="px-5 py-3 font-semibold hidden md:table-cell">Access</th>
                      )}
                      <th className="px-5 py-3" />
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((u) => {
                      const display = u.name || u.username;
                      const summary = attrSummary(u.attributes, schema);
                      return (
                        <tr
                          key={u.id}
                          onClick={() => setSelected(u)}
                          className="border-t border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40 cursor-pointer transition-colors"
                        >
                          <td className="px-5 py-3">
                            <div className="flex items-center gap-3">
                              <div className="w-8 h-8 rounded-full bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400 text-xs font-semibold flex items-center justify-center flex-shrink-0">
                                {initials(display)}
                              </div>
                              <div className="min-w-0">
                                <div className="font-medium text-slate-900 dark:text-white truncate">
                                  {display}
                                </div>
                                <div className="text-xs text-slate-400 dark:text-slate-500 truncate">
                                  {u.username}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-5 py-3 text-slate-500 dark:text-slate-400 hidden sm:table-cell truncate">
                            {u.email || '—'}
                          </td>
                          <td className="px-5 py-3">
                            <div className="flex items-center gap-1.5">
                              <StatusBadge status={u.status} isAdmin={u.isAdmin} />
                              {u.isUserManager && <ManagerBadge />}
                            </div>
                          </td>
                          {showRequested ? (
                            <td className="px-5 py-3 text-slate-500 dark:text-slate-400 hidden md:table-cell whitespace-nowrap">
                              {requestedLabel(u.requestedAt)}
                            </td>
                          ) : (
                            <td className="px-5 py-3 text-slate-500 dark:text-slate-400 hidden md:table-cell truncate max-w-xs">
                              {summary || '—'}
                            </td>
                          )}
                          <td className="px-5 py-3 text-right">
                            <span className="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 dark:text-indigo-400">
                              {actionLabel}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>

      {selected && (
        <UserDrawer
          user={selected}
          schema={schema}
          callerIsAdmin={isAdmin}
          position={selectedIndex >= 0 ? { index: selectedIndex, total: sorted.length } : undefined}
          onNavigate={(delta) => {
            const next = sorted[selectedIndex + delta];
            if (next) setSelected(next);
          }}
          onClose={() => setSelected(null)}
          onDone={onDone}
          onAuthExpired={() => {
            setSelected(null);
            setAuthExpired(true);
          }}
        />
      )}

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg dark:bg-slate-700">
          <HiCheckCircle className="text-base text-emerald-400" />
          {toast}
        </div>
      )}
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm p-8 text-center text-slate-500 dark:text-slate-400">
      {children}
    </div>
  );
}

function EmptyState({ tab }: { tab: Tab }) {
  const text =
    tab === 'pending'
      ? 'No users awaiting approval.'
      : tab === 'admin'
        ? 'No admins.'
        : 'No active users.';
  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm p-12 text-center">
      <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
        <HiUserGroup className="text-xl text-slate-400 dark:text-slate-500" />
      </div>
      <p className="text-slate-500 dark:text-slate-400">{text}</p>
    </div>
  );
}
