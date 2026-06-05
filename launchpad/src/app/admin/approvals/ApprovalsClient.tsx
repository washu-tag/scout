'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useSession, signIn } from 'next-auth/react';
import TopBar from '@/components/TopBar';
import { HiArrowLeft, HiCheckCircle, HiExternalLink, HiUserGroup, HiX } from 'react-icons/hi';

interface AttrSchema {
  name: string;
  displayName: string;
  multivalued: boolean;
  inputType: string;
  options: string[] | null;
  defaultValue: string | null;
}

interface PendingUser {
  id: string;
  username: string;
  email: string | null;
  name: string | null;
  requestedAt: string | null;
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

type SortCol = 'name' | 'requested';
type SortDir = 'asc' | 'desc';

// Sort the pending list by column/direction. Undated users (no request
// timestamp) always sort last, regardless of direction.
function sortPending(list: PendingUser[], col: SortCol, dir: SortDir): PendingUser[] {
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

// --- Approval drawer ------------------------------------------------------

interface DrawerProps {
  user: PendingUser;
  schema: AttrSchema[];
  onClose: () => void;
  onApproved: (id: string) => void;
}

function ApprovalDrawer({ user, schema, onClose, onApproved }: DrawerProps) {
  const [shown, setShown] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initialize each control from the schema's server default (e.g. mask=true,
  // bypass=false). Re-mounts per user via the `key` prop, so state is fresh.
  const [values, setValues] = useState<Record<string, string[]>>(() => {
    const init: Record<string, string[]> = {};
    for (const a of schema) {
      if (isBoolean(a)) {
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
  });

  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const close = useCallback(() => {
    setShown(false);
    setTimeout(onClose, 250);
  }, [onClose]);

  const toggleChip = (name: string, opt: string) => {
    setValues((v) => {
      const cur = v[name] ?? [];
      return { ...v, [name]: cur.includes(opt) ? cur.filter((x) => x !== opt) : [...cur, opt] };
    });
  };

  const approve = async () => {
    setSubmitting(true);
    setError(null);
    // Only send attributes that carry a value; booleans/selects always do.
    const attributes: Record<string, string[]> = {};
    for (const [name, val] of Object.entries(values)) {
      if (val.length > 0) attributes[name] = val;
    }
    try {
      const r = await fetch('/api/approvals/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userId: user.id, attributes }),
      });
      if (!r.ok) {
        throw new Error((await r.text()) || `${r.status} ${r.statusText}`);
      }
      onApproved(user.id);
    } catch (e) {
      setSubmitting(false);
      setError((e as Error).message);
    }
  };

  const display = user.name || user.username;

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
            <h2 className="text-base font-semibold text-slate-900 dark:text-white truncate">
              {display}
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 truncate">
              {[user.username, user.email].filter(Boolean).join(' · ')}
            </p>
            <p className="text-xs text-slate-400 dark:text-slate-500 mt-0.5">
              Requested {requestedLabel(user.requestedAt)}
            </p>
          </div>
          <button
            onClick={close}
            aria-label="Close"
            className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
          >
            <HiX className="text-xl" />
          </button>
        </div>

        {/* Dynamic form */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
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

        {/* Footer */}
        <div className="flex items-center gap-3 p-6 border-t border-slate-200 dark:border-slate-800">
          {error && (
            <span className="text-sm text-rose-600 dark:text-rose-400 flex-1">{error}</span>
          )}
          {!error && <span className="flex-1" />}
          <button
            onClick={close}
            className="px-4 py-2 text-sm font-medium text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={approve}
            disabled={submitting}
            className="inline-flex items-center gap-2 px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-sm font-semibold transition-colors"
          >
            <HiCheckCircle className="text-base" />
            {submitting ? 'Approving…' : 'Approve'}
          </button>
        </div>
      </aside>
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
    return (
      <div>
        <span className="block text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">
          {label}
        </span>
        <div className="flex flex-wrap gap-2">
          {attr.options.map((opt) => {
            const on = value.includes(opt);
            return (
              <button
                key={opt}
                type="button"
                onClick={() => onChipToggle(opt)}
                className={`rounded-full border px-3 py-1 text-sm transition-colors ${
                  on
                    ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 font-medium'
                    : 'border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:border-slate-300 dark:hover:border-slate-600'
                }`}
              >
                {opt}
              </button>
            );
          })}
        </div>
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

export default function ApprovalsClient() {
  const { data: session, status } = useSession();
  const [schema, setSchema] = useState<AttrSchema[]>([]);
  const [pending, setPending] = useState<PendingUser[] | null>(null);
  const [selected, setSelected] = useState<PendingUser | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  // Auto sign-in (matches the launchpad home), and derive the Keycloak scout
  // realm console URL + the ?user deep-link from the browser location.
  useEffect(() => {
    if (status !== 'loading' && !session) signIn('keycloak');
  }, [status, session]);

  useEffect(() => {
    setKcConsole(
      `${window.location.protocol}//keycloak.${window.location.host}/admin/scout/console`,
    );
    setDeepLink(new URLSearchParams(window.location.search).get('user'));
  }, []);

  const load = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([
        fetch('/api/approvals/schema').then((r) => {
          if (!r.ok) throw new Error('schema');
          return r.json();
        }),
        fetch('/api/approvals/pending').then((r) => {
          if (!r.ok) throw new Error('pending');
          return r.json();
        }),
      ]);
      setSchema(s as AttrSchema[]);
      setPending(p as PendingUser[]);
    } catch {
      setError('Could not load approvals. You may not have the scout-admin role.');
    }
  }, []);

  useEffect(() => {
    if (isAdmin) load();
  }, [isAdmin, load]);

  // When arriving from the email link (?user=<id>), open that row's drawer.
  useEffect(() => {
    if (deepLink && pending) {
      const u = pending.find((x) => x.id === deepLink);
      if (u) setSelected(u);
    }
  }, [deepLink, pending]);

  const onApproved = (id: string) => {
    setPending((cur) => (cur ? cur.filter((u) => u.id !== id) : cur));
    setSelected(null);
  };

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-indigo-50/40 dark:from-slate-950 dark:via-slate-950 dark:to-indigo-950/30">
      {/* Header */}
      <div className="border-b border-slate-200 dark:border-slate-800 bg-white/70 dark:bg-slate-900/60 backdrop-blur">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <a
              href="/"
              className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
              aria-label="Back to Launchpad"
            >
              <HiArrowLeft className="text-lg" />
            </a>
            <div className="p-0.5 rounded-md bg-gradient-to-br from-indigo-500 to-indigo-700">
              <img src="/scout.png" alt="Scout" className="h-7 w-7 rounded bg-white p-0.5 block" />
            </div>
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">Scout</span>
            <span className="text-slate-300 dark:text-slate-700 text-sm">/</span>
            <span className="text-sm text-slate-500 dark:text-slate-400">User Approval</span>
          </div>
          <div className="flex items-center gap-4">
            {kcConsole && (
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
            <TopBar />
          </div>
        </div>
      </div>

      <main className="max-w-5xl mx-auto px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-white tracking-tight">
            User Approval
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            Review access requests and grant data-access attributes.
          </p>
        </div>

        {status === 'loading' || !session ? (
          <Notice>Loading…</Notice>
        ) : !isAdmin ? (
          <Notice>
            You need the{' '}
            <code className="font-mono text-rose-600 dark:text-rose-400">scout-admin</code> role to
            review approvals.
          </Notice>
        ) : error ? (
          <Notice>{error}</Notice>
        ) : pending === null ? (
          <Notice>Loading…</Notice>
        ) : pending.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
              {pending.length} {pending.length === 1 ? 'user' : 'users'} awaiting approval
            </p>
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
                    <th className="px-5 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {sortPending(pending, sort.col, sort.dir).map((u) => {
                    const display = u.name || u.username;
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
                        <td className="px-5 py-3 text-slate-500 dark:text-slate-400 hidden md:table-cell whitespace-nowrap">
                          {requestedLabel(u.requestedAt)}
                        </td>
                        <td className="px-5 py-3 text-right">
                          <span className="inline-flex items-center gap-1 text-sm font-medium text-indigo-600 dark:text-indigo-400">
                            Review
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>

      {selected && (
        <ApprovalDrawer
          key={selected.id}
          user={selected}
          schema={schema}
          onClose={() => setSelected(null)}
          onApproved={onApproved}
        />
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

function EmptyState() {
  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm p-12 text-center">
      <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-slate-100 dark:bg-slate-800 flex items-center justify-center">
        <HiUserGroup className="text-xl text-slate-400 dark:text-slate-500" />
      </div>
      <p className="text-slate-500 dark:text-slate-400">No users awaiting approval.</p>
    </div>
  );
}
