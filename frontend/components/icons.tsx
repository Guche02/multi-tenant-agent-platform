// Small inline icons (24px grid, stroke = currentColor).

type Props = { className?: string };

function Icon({ className = "h-5 w-5", children }: Props & { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
      strokeLinejoin="round" className={className} aria-hidden="true">
      {children}
    </svg>
  );
}

export const MenuIcon = (p: Props) => <Icon {...p}><path d="M4 6h16M4 12h16M4 18h16" /></Icon>;
export const PlusIcon = (p: Props) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
export const PencilIcon = (p: Props) => <Icon {...p}><path d="M4 20h4L19 9l-4-4L4 16v4z" /><path d="M14 6l4 4" /></Icon>;
export const TrashIcon = (p: Props) => <Icon {...p}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></Icon>;
export const SendIcon = (p: Props) => <Icon {...p}><path d="M5 12h13M13 6l6 6-6 6" /></Icon>;
export const StopIcon = (p: Props) => <Icon {...p}><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" /></Icon>;
export const XIcon = (p: Props) => <Icon {...p}><path d="M6 6l12 12M18 6L6 18" /></Icon>;
export const CheckIcon = (p: Props) => <Icon {...p}><path d="M5 12.5l4.5 4.5L19 7.5" /></Icon>;
export const BoxIcon = (p: Props) => <Icon {...p}><path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5v-9z" /><path d="M3 7.5L12 12l9-4.5M12 12v9" /></Icon>;
export const CalendarIcon = (p: Props) => <Icon {...p}><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M4 10h16M9 3v4M15 3v4" /></Icon>;
export const DocIcon = (p: Props) => <Icon {...p}><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></Icon>;
export const CopyIcon = (p: Props) => <Icon {...p}><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3" /></Icon>;
export const RefreshIcon = (p: Props) => <Icon {...p}><path d="M20 11a8 8 0 10-2.3 5.7M20 5v6h-6" /></Icon>;
export const LogoutIcon = (p: Props) => <Icon {...p}><path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" /></Icon>;
export const AlertIcon = (p: Props) => <Icon {...p}><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17.5v.5" /></Icon>;

export function Spinner({ className = "h-4 w-4" }: Props) {
  return (
    <svg viewBox="0 0 24 24" className={`animate-spin ${className}`} aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" opacity="0.25" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}
