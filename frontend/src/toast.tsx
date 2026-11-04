import { createContext, ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { Alert, AlertActionCloseButton, AlertGroup } from "@patternfly/react-core";

type Variant = "success" | "danger" | "warning" | "info";

interface Toast {
  key: number;
  variant: Variant;
  title: string;
  description?: string;
}

interface ToastApi {
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const remove = useCallback((key: number) => {
    setToasts((prev) => prev.filter((t) => t.key !== key));
  }, []);

  const push = useCallback((variant: Variant, title: string, description?: string) => {
    const key = next.current++;
    setToasts((prev) => [...prev, { key, variant, title, description }]);
  }, []);

  const value = useMemo<ToastApi>(
    () => ({
      success: (t, d) => push("success", t, d),
      error: (t, d) => push("danger", t, d),
      warning: (t, d) => push("warning", t, d),
      info: (t, d) => push("info", t, d),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <AlertGroup isToast isLiveRegion>
        {toasts.map((t) => (
          <Alert
            key={t.key}
            variant={t.variant}
            title={t.title}
            timeout={t.variant === "danger" ? 10000 : 5000}
            onTimeout={() => remove(t.key)}
            actionClose={<AlertActionCloseButton onClose={() => remove(t.key)} />}
          >
            {t.description}
          </Alert>
        ))}
      </AlertGroup>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
