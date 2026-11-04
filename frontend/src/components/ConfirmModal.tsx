import { ReactNode, useState } from "react";
import { Alert, Button, Modal, ModalVariant } from "@patternfly/react-core";
import { errorMessage } from "../api";

interface Props {
  title: string;
  isOpen: boolean;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  children: ReactNode;
}

/** Confirmation dialog. Errors from onConfirm (for example 409s) stay visible in the dialog. */
export function ConfirmModal({
  title,
  isOpen,
  confirmLabel = "Delete",
  danger = true,
  onConfirm,
  onClose,
  children,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setError(null);
    onClose();
  };

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      variant={ModalVariant.small}
      title={title}
      titleIconVariant={danger ? "warning" : undefined}
      isOpen={isOpen}
      onClose={close}
      actions={[
        <Button
          key="confirm"
          variant={danger ? "danger" : "primary"}
          onClick={confirm}
          isLoading={busy}
          isDisabled={busy}
        >
          {confirmLabel}
        </Button>,
        <Button key="cancel" variant="link" onClick={close}>
          Cancel
        </Button>,
      ]}
    >
      {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: 16 }} />}
      {children}
    </Modal>
  );
}
