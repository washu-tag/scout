'use client';

import Link from 'next/link';
import React, { createContext, useContext } from 'react';
import { chipTarget } from '@/lib/catalog/shell';
import type { RenderChip } from '@/lib/catalog/types';

export const EmbedAppsContext = createContext(true);

type ChipAnchorProps = Omit<
  React.AnchorHTMLAttributes<HTMLAnchorElement>,
  'href' | 'target' | 'rel'
> & { chip: RenderChip };

export function ChipAnchor({ chip, children, ...props }: ChipAnchorProps) {
  const target = chipTarget(chip, useContext(EmbedAppsContext));
  if (target.internal) {
    return (
      <Link href={target.href} {...props}>
        {children}
      </Link>
    );
  }
  return (
    <a
      href={target.href}
      {...(target.newTab ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      {...props}
    >
      {children}
    </a>
  );
}
