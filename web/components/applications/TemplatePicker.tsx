import type { Template } from "@/lib/applications";

export const TEMPLATE_OPTIONS: { value: Template; label: string }[] = [
  { value: "us", label: "EUA (1–2 páginas)" },
  { value: "de", label: "Alemanha (Lebenslauf)" },
  { value: "au", label: "Austrália (2–3 páginas)" },
  { value: "br", label: "Brasil (livre)" },
];

type Props = {
  value: Template;
  onChange: (template: Template) => void;
  disabled?: boolean;
};

/** Modelo do currículo (país). A pessoa pode trocar na revisão. */
export function TemplatePicker({ value, onChange, disabled = false }: Props) {
  return (
    <label className="template-picker">
      <span>Modelo do currículo</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as Template)}
      >
        {TEMPLATE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
