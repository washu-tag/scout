import { TONES } from '@/lib/catalog/tones';
import type { RenderChip } from '@/lib/catalog/types';

export function ChipBadge({ chip }: { chip: RenderChip }) {
  if (!chip.badge) return null;
  const tone = TONES[chip.badge.tone];
  return (
    <span
      className={`inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${tone.iconBg} ${tone.icon}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      {chip.badge.text}
    </span>
  );
}
