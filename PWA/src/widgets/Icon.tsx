const PATHS: Record<string, string> = {
  backup: "M12 16V4m0 0L7 9m5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
};

/** Built-in icons by name. Unknown names render nothing. */
export default function Icon({ name }: { name?: string }) {
  const d = name ? PATHS[name] : undefined;
  if (!d) return null;
  return (
    <svg className="jw__icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
