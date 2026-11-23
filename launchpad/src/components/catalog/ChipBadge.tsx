import { TONES } from '@/lib/catalog/tones';
import type { RenderChip } from '@/lib/catalog/types';

export function ChipBadge({ chip }: { chip: RenderChip }) {
  if (!chip.badge) return null;
  const tone = TONES[chip.badge.tone];
  return (
    <span
      title={chip.badge.text}
      className={`inline-flex max-w-full flex-shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${tone.iconBg} ${tone.badgeText}`}
    >
      <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-current" aria-hidden="true" />
      <span className="min-w-0 truncate">{chip.badge.text}</span>
    </span>
  );
}
