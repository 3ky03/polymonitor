type ShellIconProps = {
  className?: string;
};

export function ResetWorkspaceIcon({ className }: ShellIconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4.5 8.5V4.5h4" />
      <path d="M5.1 7.1A8 8 0 1 1 4 14" />
      <path d="M9.25 9.25h5.5v5.5h-5.5z" />
    </svg>
  );
}
