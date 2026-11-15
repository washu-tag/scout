'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useSession, signIn, signOut } from 'next-auth/react';
import { FaBook, FaMoon, FaSignOutAlt, FaSun } from 'react-icons/fa';
import { HiOutlineExternalLink } from 'react-icons/hi';
import { ChipIcon } from '@/components/catalog/CatalogSections';
import { ChipAnchor } from '@/components/shell/ChipAnchor';
import { DEFAULT_ICON, ICONS } from '@/lib/catalog/icons';
import {
  chipTarget,
  deepPath,
  embeddedChip,
  frameUrl,
  isChipOrigin,
  openRequest,
  recentApps,
  shellLocation,
  type ShellSection,
} from '@/lib/catalog/shell';
import { TONES, type ToneName } from '@/lib/catalog/tones';
import type { RenderChip } from '@/lib/catalog/types';

interface ShellClientProps {
  sections: ShellSection[];
  environment: string;
  docsUrl: string;
  children: React.ReactNode;
}

const ACCOUNT_MENU = '@account';
const SESSION_CHECK_MS = 60_000;
const RECOVERY_KEY = 'scout-shell-session-recovery';
const RECOVERY_BACKOFF_MS = 60_000;
const LEAVING_TTL_MS = 30_000;
const MAX_OPEN_APPS = 3;
// A sandboxed app keeps scripts, its own origin, forms, dialogs and downloads, but
// can't open popups or navigate the launchpad window.
const FRAME_SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads';

const MENU =
  'fixed left-[76px] z-30 w-80 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg dark:border-slate-700 dark:bg-slate-900';
const MENU_ITEM =
  'flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm text-slate-700 no-underline hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800 cursor-pointer';
const MENU_ICON = 'text-base text-slate-500 dark:text-slate-400';

function railItemClass(highlighted: boolean) {
  return `flex w-[70px] flex-col items-center gap-1 rounded-xl py-1.5 no-underline transition-colors cursor-pointer ${
    highlighted ? 'bg-slate-100 dark:bg-slate-800' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
  }`;
}

function RailLabel({
  icon,
  label,
  active,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  tone?: ToneName;
}) {
  const colors = !tone
    ? 'border-transparent text-slate-500 dark:text-slate-400'
    : active
      ? `${TONES[tone].iconBg} ${TONES[tone].icon}`
      : `border-transparent ${TONES[tone].icon}`;
  return (
    <>
      <span
        className={`flex h-9 w-9 items-center justify-center rounded-lg border text-xl ${colors}`}
      >
        {icon}
      </span>
      <span
        className={`line-clamp-2 w-full break-words text-center text-[11px] font-medium leading-tight ${
          active ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'
        }`}
      >
        {label}
      </span>
    </>
  );
}

function Spinner() {
  return (
    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-slate-400 dark:border-slate-500" />
  );
}

export default function ShellClient({
  sections,
  environment,
  docsUrl,
  children,
}: ShellClientProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: session, status } = useSession();
  const railRef = useRef<HTMLDivElement>(null);
  const signingOut = useRef(false);
  const [menu, setMenu] = useState<{ id: string; top: number } | null>(null);
  const [dark, setDark] = useState(false);

  const appId = pathname.startsWith('/app/')
    ? decodeURIComponent(pathname.split('/')[2] ?? '')
    : null;
  const active = appId ? embeddedChip(sections, appId) : undefined;

  // The page each framed app last reported, so the address bar can follow it.
  const [locations, setLocations] = useState<Record<string, string>>({});
  const frames = useRef(new Map<string, HTMLIFrameElement>());
  // Where a frame said it is navigating; used if the frame then loads a page that
  // can't report itself.
  const leaving = useRef(new Map<string, { address: string; at: number }>());

  // A deep link (/app/<id>/<path>) opens the app at that page. Read from the
  // browser's URL, which during a rail click may still be the previous app's. An
  // app that was unloaded reopens at the page it last reported.
  const remembered = active ? locations[active.id] : undefined;
  const deep =
    active && typeof window !== 'undefined'
      ? (deepPath(active.id, window.location) ??
        (remembered ? deepPath(active.id, new URL(remembered, window.location.origin)) : null))
      : null;
  const activeSrc = active && deep ? frameUrl(active, deep) : active?.href;

  // The most recently used apps keep their frames, so switching back resumes them;
  // older ones are unloaded to bound memory. Frames are only appended or removed,
  // never moved, since moving an iframe reloads it.
  const [opened, setOpened] = useState<{ chip: RenderChip; src: string }[]>(
    active && activeSrc ? [{ chip: active, src: activeSrc }] : [],
  );
  const [recent, setRecent] = useState<string[]>(active ? [active.id] : []);
  const [loaded, setLoaded] = useState<string[]>([]);
  if (active && activeSrc && recent[recent.length - 1] !== active.id) {
    const keep = recentApps(recent, active.id, MAX_OPEN_APPS);
    const kept = opened.filter((entry) => keep.includes(entry.chip.id));
    setRecent(keep);
    setOpened(
      kept.some((entry) => entry.chip.id === active.id)
        ? kept
        : [...kept, { chip: active, src: activeSrc }],
    );
    setLoaded((ids) => ids.filter((id) => keep.includes(id)));
  }

  const skipAuth =
    process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_SKIP_AUTH === 'true';

  // Sign-in has to happen at the top level: identity-provider login pages
  // refuse to render inside a frame.
  useEffect(() => {
    if (!skipAuth && !signingOut.current && status !== 'loading' && !session) {
      signIn('keycloak', { callbackUrl: window.location.href });
    }
  }, [status, session, skipAuth]);

  // Scout's sessions can end while an app is open in a frame, where the sign-in
  // pages cannot render. Check from the top window and send the whole window to
  // sign in: a 401 is oauth2-proxy (the ingress session is gone), a 440 is the
  // launchpad reporting the SSO session gone.
  const checkSession = useCallback(async () => {
    if (skipAuth || signingOut.current) return;
    let response: Response;
    try {
      response = await fetch('/api/session', { cache: 'no-store' });
    } catch {
      return;
    }
    if (response.status !== 401 && response.status !== 440) return;
    try {
      const last = Number(sessionStorage.getItem(RECOVERY_KEY) ?? 0);
      if (Date.now() - last < RECOVERY_BACKOFF_MS) return;
      sessionStorage.setItem(RECOVERY_KEY, String(Date.now()));
    } catch {
      // Without storage there is no loop guard; recover anyway.
    }
    if (response.status === 401) window.location.reload();
    else signIn('keycloak', { callbackUrl: window.location.href });
  }, [skipAuth]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') checkSession();
    };
    const interval = window.setInterval(onVisible, SESSION_CHECK_MS);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [checkSession]);

  useEffect(() => {
    const saved = localStorage.getItem('theme');
    const prefersDark =
      saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches);
    setDark(prefersDark);
    document.documentElement.classList.toggle('dark', prefersDark);
  }, []);

  // Apps report their own address with a `scout:location` message, and where a
  // navigation is headed with `scout:leaving`. Only the app's own frame, from the
  // chip's origin, is believed; content an app nests inside its frame has a
  // different source and is ignored. Any Scout frame, nested ones included, can
  // ask with `scout:open` to open a Scout page here instead of in a new tab.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const type = event.data?.type;
      if (type === 'scout:open') {
        const request = openRequest(
          sections,
          new URL(window.location.href),
          event.origin,
          event.data.href,
        );
        if (!request) return;
        const { chip, address, href } = request;
        setLocations((current) => ({ ...current, [chip.id]: address }));
        setOpened((current) =>
          current.map((entry) => (entry.chip.id === chip.id ? { ...entry, src: href } : entry)),
        );
        router.push(address);
        (event.source as Window | null)?.postMessage(
          { type: 'scout:opened', href: event.data.href },
          event.origin,
        );
        return;
      }
      if (type !== 'scout:location' && type !== 'scout:leaving') return;
      for (const [id, frame] of frames.current) {
        if (frame.contentWindow !== event.source) continue;
        const chip = embeddedChip(sections, id);
        if (!chip || !isChipOrigin(chip, event.origin)) return;
        const address = shellLocation(chip, event.data.href);
        if (!address) return;
        if (type === 'scout:leaving') {
          leaving.current.set(id, { address, at: Date.now() });
        } else {
          leaving.current.delete(id);
          setLocations((current) => ({ ...current, [id]: address }));
        }
        return;
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [sections, router]);

  const activeLocation = active ? locations[active.id] : undefined;
  useEffect(() => {
    if (!activeLocation) return;
    const current = window.location.pathname + window.location.search + window.location.hash;
    if (current !== activeLocation) window.history.replaceState(null, '', activeLocation);
  }, [activeLocation]);

  const menuOpen = menu !== null;
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenu(null);
    const onPointerDown = (event: PointerEvent) => {
      if (!railRef.current?.contains(event.target as Node)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    // A click inside an app's frame never reaches this document; it blurs the window.
    window.addEventListener('blur', close);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('blur', close);
    };
  }, [menuOpen]);

  const toggleMenu = (id: string, event: React.MouseEvent<HTMLElement>) => {
    const top = event.currentTarget.getBoundingClientRect().top;
    setMenu((current) => (current?.id === id ? null : { id, top }));
  };

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    localStorage.setItem('theme', next ? 'dark' : 'light');
    document.documentElement.classList.toggle('dark', next);
  };

  const signOutOfScout = async () => {
    setMenu(null);
    signingOut.current = true;
    await signOut({ redirect: false });
    try {
      const response = await fetch('/api/signout', { method: 'POST' });
      const { redirectUrl } = await response.json();
      if (redirectUrl) {
        window.location.href = redirectUrl;
        return;
      }
    } catch {
      // Fall back to home below.
    }
    window.location.assign('/');
  };

  if (!skipAuth && (status === 'loading' || !session)) {
    return (
      <div className="min-h-screen w-full bg-slate-50 dark:bg-slate-950 flex items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const user = session?.user;
  const [primary, ...rest] = sections;
  const openSection = rest.find((section) => section.id === menu?.id);
  const isCurrent = (chip: RenderChip) => {
    const target = chipTarget(chip);
    return target.internal && (pathname === target.href || pathname.startsWith(`${target.href}/`));
  };

  const railChip = (chip: RenderChip) => {
    const current = isCurrent(chip);
    return (
      <ChipAnchor
        key={chip.id}
        chip={chip}
        onClick={() => setMenu(null)}
        title={chip.description || chip.title}
        aria-current={current ? 'page' : undefined}
        className={railItemClass(current)}
      >
        <RailLabel
          icon={<ChipIcon chip={chip} className="text-xl" />}
          label={chip.title}
          active={current}
          tone={chip.tone}
        />
      </ChipAnchor>
    );
  };

  const groupButton = (section: ShellSection) => {
    const currentChip = section.chips.find(isCurrent);
    const isOpen = menu?.id === section.id;
    const GroupIcon = ICONS[section.icon] ?? ICONS[DEFAULT_ICON];
    return (
      <button
        key={section.id}
        type="button"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        title={section.title}
        onClick={(event) => toggleMenu(section.id, event)}
        className={railItemClass(isOpen || currentChip !== undefined)}
      >
        <RailLabel
          icon={<GroupIcon />}
          label={section.title}
          active={currentChip !== undefined}
          tone={currentChip?.tone}
        />
      </button>
    );
  };

  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-50 dark:bg-slate-950">
      <div ref={railRef} className="flex-shrink-0">
        <nav
          aria-label="Scout"
          className="flex h-full w-[72px] flex-col items-center gap-1 overflow-y-auto border-r border-slate-200 bg-white py-3 dark:border-slate-800 dark:bg-slate-900"
        >
          <Link
            href="/"
            title="Scout home"
            aria-label="Scout home"
            aria-current={pathname === '/' ? 'page' : undefined}
            onClick={() => setMenu(null)}
            className="mb-2 flex flex-col items-center gap-1 rounded-md no-underline hover:opacity-80 transition-opacity"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/scout.svg" alt="" className="h-9 w-9 block dark:hidden" />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/scout-dark.svg" alt="" className="h-9 w-9 hidden dark:block" />
            <span className="max-w-16 truncate text-[10px] leading-tight text-slate-400 dark:text-slate-500">
              {environment}
            </span>
          </Link>

          {primary && (
            <div className="flex w-full flex-col items-center gap-1 border-t border-slate-200 pt-2 dark:border-slate-800">
              {primary.chips.map(railChip)}
            </div>
          )}

          {rest.length > 0 && (
            <div className="flex w-full flex-col items-center gap-1 border-t border-slate-200 pt-2 dark:border-slate-800">
              {rest.map((section) =>
                section.chips.length === 1 ? railChip(section.chips[0]) : groupButton(section),
              )}
            </div>
          )}

          <div className="mt-auto flex w-full flex-col items-center border-t border-slate-200 pt-2 dark:border-slate-800">
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={menu?.id === ACCOUNT_MENU}
              title={user?.name || user?.username || 'Account'}
              onClick={(event) => toggleMenu(ACCOUNT_MENU, event)}
              className={railItemClass(menu?.id === ACCOUNT_MENU)}
            >
              <RailLabel
                icon={
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-200 text-sm font-semibold text-slate-700 dark:bg-slate-700 dark:text-slate-200">
                    {(user?.name || user?.username || 'U').charAt(0).toUpperCase()}
                  </span>
                }
                label={user?.name?.split(' ')[0] || user?.username || 'Account'}
                active={false}
              />
            </button>
          </div>
        </nav>

        {openSection && menu && (
          <div
            role="menu"
            aria-label={openSection.title}
            className={MENU}
            style={{ top: menu.top }}
          >
            <p className="px-2.5 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
              {openSection.title}
            </p>
            {openSection.chips.map((chip) => {
              const tone = TONES[chip.tone];
              const current = isCurrent(chip);
              return (
                <ChipAnchor
                  key={chip.id}
                  chip={chip}
                  role="menuitem"
                  onClick={() => setMenu(null)}
                  aria-current={current ? 'page' : undefined}
                  className={`${MENU_ITEM} ${current ? 'bg-slate-100 dark:bg-slate-800' : ''}`}
                >
                  <span
                    className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border text-base ${tone.iconBg} ${tone.icon}`}
                  >
                    <ChipIcon chip={chip} className="text-base" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{chip.title}</span>
                    {chip.description && (
                      <span className="line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                        {chip.description}
                      </span>
                    )}
                  </span>
                  {!chipTarget(chip).internal && (
                    <HiOutlineExternalLink className={MENU_ICON} aria-label="Opens in a new tab" />
                  )}
                </ChipAnchor>
              );
            })}
          </div>
        )}

        {menu?.id === ACCOUNT_MENU && (
          <div role="menu" aria-label="Account" className={`${MENU} bottom-3`}>
            <div className="mb-1 border-b border-slate-200 px-2.5 pb-2 pt-1 dark:border-slate-700">
              <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
                {user?.name || user?.username || 'User'}
              </p>
              {user?.username && (
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                  {user.username}
                </p>
              )}
              {user?.email && (
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{user.email}</p>
              )}
            </div>
            <button role="menuitem" type="button" onClick={toggleTheme} className={MENU_ITEM}>
              {dark ? <FaSun className={MENU_ICON} /> : <FaMoon className={MENU_ICON} />}
              {dark ? 'Light mode' : 'Dark mode'}
            </button>
            <a
              role="menuitem"
              href={docsUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setMenu(null)}
              className={MENU_ITEM}
            >
              <FaBook className={MENU_ICON} />
              Documentation
            </a>
            <button role="menuitem" type="button" onClick={signOutOfScout} className={MENU_ITEM}>
              <FaSignOutAlt className={MENU_ICON} />
              Sign out
            </button>
          </div>
        )}
      </div>

      <main className="relative flex-1 overflow-hidden">
        {opened.map(({ chip, src }) => (
          <iframe
            key={chip.id}
            ref={(element) => {
              if (element) frames.current.set(chip.id, element);
              else frames.current.delete(chip.id);
            }}
            src={src}
            title={chip.title}
            sandbox={chip.sandbox ? FRAME_SANDBOX : undefined}
            allow="clipboard-read; clipboard-write; fullscreen"
            aria-hidden={chip.id !== active?.id}
            onLoad={() => {
              const next = leaving.current.get(chip.id);
              leaving.current.delete(chip.id);
              if (next && Date.now() - next.at < LEAVING_TTL_MS) {
                setLocations((current) => ({ ...current, [chip.id]: next.address }));
              }
              setLoaded((ids) => (ids.includes(chip.id) ? ids : [...ids, chip.id]));
              checkSession();
            }}
            className={`absolute inset-0 h-full w-full border-0 bg-white ${
              chip.id === active?.id ? 'visible' : 'invisible'
            }`}
          />
        ))}
        {active && !loaded.includes(active.id) && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-50 dark:bg-slate-950">
            <Spinner />
          </div>
        )}
        <div className={`h-full overflow-y-auto ${appId ? 'hidden' : ''}`}>{children}</div>
      </main>
    </div>
  );
}
