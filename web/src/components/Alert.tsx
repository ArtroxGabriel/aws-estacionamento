import type { ReactNode } from "react";

export type AlertVariant = "error" | "success" | "warning" | "info";

interface AlertProps {
  variant: AlertVariant;
  children: ReactNode;
  onRetry?: () => void;
}

const variantClasses: Record<AlertVariant, string> = {
  error: "border-red-300 bg-red-50 text-red-800",
  success: "border-green-300 bg-green-50 text-green-800",
  warning: "border-amber-300 bg-amber-50 text-amber-800",
  info: "border-blue-300 bg-blue-50 text-blue-800",
};

export default function Alert({ variant, children, onRetry }: AlertProps) {
  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={`flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3 text-sm ${variantClasses[variant]}`}
    >
      <div>{children}</div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-current px-3 py-1 font-medium hover:bg-white/60"
        >
          Tentar novamente
        </button>
      )}
    </div>
  );
}
