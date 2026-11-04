import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Form,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  Modal,
  ModalVariant,
  TextInput,
} from "@patternfly/react-core";
import { api, Credential, CredentialTestResult, errorMessage } from "../api";

interface Props {
  credential: Credential | null;
  /** Prefills the model, e.g. the default model of an agent using this credential. */
  suggestedModel?: string | null;
  onClose: () => void;
}

const VARIANT = { true: "success", false: "danger", null: "info" } as const;
const TITLE = { true: "Works", false: "Failed", null: "Not checked" } as const;

/**
 * Checks a credential from the server: a cheap request without a model, or a
 * one-token request when a model is given. Model names come from the endpoint
 * where it can list them, and can always be typed.
 */
export function TestCredentialModal({ credential, suggestedModel, onClose }: Props) {
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CredentialTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!credential) return;
    setModel(suggestedModel ?? "");
    setResult(null);
    setError(null);
    setModels([]);
    let cancelled = false;
    // Suggestions only: a failure here shows up when the admin runs the test.
    api
      .credentialModels(credential.id)
      .then((list) => !cancelled && setModels(list.models))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [credential, suggestedModel]);

  const run = async () => {
    if (!credential) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.testCredential(credential.id, model.trim() || null));
    } catch (err) {
      setError(errorMessage(err, "Test failed"));
    } finally {
      setBusy(false);
    }
  };

  const key = String(result?.ok ?? null) as "true" | "false" | "null";
  const listId = "test-credential-models";

  return (
    <Modal
      variant={ModalVariant.small}
      title={`Test ${credential?.name ?? "credential"}`}
      isOpen={!!credential}
      onClose={onClose}
      actions={[
        <Button key="run" type="submit" form="test-credential-form" isLoading={busy} isDisabled={busy}>
          Run test
        </Button>,
        <Button key="close" variant="link" onClick={onClose}>
          Close
        </Button>,
      ]}
    >
      <Form
        id="test-credential-form"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <FormGroup label="Model (optional)" fieldId="test-credential-model">
          <TextInput
            id="test-credential-model"
            value={model}
            onChange={(_e, v) => setModel(v)}
            list={models.length ? listId : undefined}
            placeholder="Leave empty to only check the key"
            autoComplete="off"
          />
          {models.length > 0 && (
            <datalist id={listId} data-testid="test-model-options">
              {models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          )}
          <FormHelperText>
            <HelperText>
              <HelperTextItem>
                With a model, Eagle sends a one-token request, which confirms the model name and
                access. This may cost a fraction of a cent.
              </HelperTextItem>
            </HelperText>
          </FormHelperText>
        </FormGroup>
        {error && <Alert variant="danger" isInline title={error} />}
        {result && (
          <Alert variant={VARIANT[key]} isInline title={TITLE[key]}>
            {result.message}
            {result.latency_ms != null && <div>Took {result.latency_ms} ms.</div>}
          </Alert>
        )}
      </Form>
    </Modal>
  );
}
