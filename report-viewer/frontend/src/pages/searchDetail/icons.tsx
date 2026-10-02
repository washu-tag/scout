// Shared toolbar icons. The cohort viewer and the chart viewer sit in the
// same iframe and use the same expand/contract affordance, so they draw the
// same glyph.

export function ExpandIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3" />
    </svg>
  );
}

export function ContractIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 3v3H3M13 6h-3V3M10 13v-3h3M3 10h3v3" />
    </svg>
  );
}

// The negation glyph, so a contradicted row is marked by shape as well as
// colour and still reads in greyscale.
function EvidenceGlyph(props: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="10"
      height="10"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: '0 0 auto' }}
    >
      {props.children}
    </svg>
  );
}

export function NegationIcon() {
  return (
    <EvidenceGlyph>
      <path d="M8 2.5 14.5 13.5h-13z" />
      <path d="M8 6.5v3M8 11.5v.01" />
    </EvidenceGlyph>
  );
}
