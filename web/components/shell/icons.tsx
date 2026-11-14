/* Ícones inline (20 px, traço em currentColor). Decorativos: o rótulo vem do texto ao lado. */

type IconProps = { className?: string };

function Svg({ className, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      className={className}
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function LogoIcon({ className }: IconProps) {
  return (
    <svg className={className} width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <circle cx="9" cy="9" r="7.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M9 3.5 L11 9 L9 14.5 L7 9 Z" fill="currentColor" />
    </svg>
  );
}

export function HomeIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 9 10 3.5 16.5 9" />
      <path d="M5 8v8h4v-4.5h2V16h4V8" />
    </Svg>
  );
}

export function ProfileIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="10" cy="7" r="3" />
      <path d="M4 16.5c.8-3 3.2-4.5 6-4.5s5.2 1.5 6 4.5" />
    </Svg>
  );
}

export function InterviewIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 5.5A1.5 1.5 0 0 1 5 4h10a1.5 1.5 0 0 1 1.5 1.5v6.5A1.5 1.5 0 0 1 15 13.5H9l-3.5 3v-3H5A1.5 1.5 0 0 1 3.5 12Z" />
      <path d="M7 8h6M7 10.5h4" />
    </Svg>
  );
}

export function SourcesIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 16.5V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v11.5" />
      <path d="M12 8h3a1 1 0 0 1 1 1v7.5" />
      <path d="M3 16.5h14M7 7h2M7 10h2M7 13h2" />
    </Svg>
  );
}

export function JobsIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="6.5" width="14" height="10" rx="1.5" />
      <path d="M7.5 6.5V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5M3 11h14" />
    </Svg>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4" />
    </Svg>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13" />
    </Svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 5l10 10M15 5 5 15" />
    </Svg>
  );
}
