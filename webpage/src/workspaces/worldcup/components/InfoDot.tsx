export function InfoDot({ label }: { label: string }) {
  return <span className="wm-worldcup-info-dot" aria-label={label} title={label}>?</span>;
}
