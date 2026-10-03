interface SpinnerProps {
  label?: string; // texto para leitores de tela
  className?: string;
}

export default function Spinner({ label = "Carregando...", className = "size-5" }: SpinnerProps) {
  return (
    <span className="inline-flex items-center">
      <span
        aria-hidden="true"
        className={`${className} animate-spin rounded-full border-2 border-current border-t-transparent`}
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}
