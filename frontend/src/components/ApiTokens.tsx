import { FormEvent, useState } from "react";
import {
  Alert,
  Button,
  ClipboardCopy,
  CodeBlock,
  CodeBlockCode,
  Form,
  FormGroup,
  FormHelperText,
  FormSelect,
  FormSelectOption,
  HelperText,
  HelperTextItem,
  Modal,
  ModalVariant,
  TextInput,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { api, ApiToken, ApiTokenCreated, errorMessage } from "../api";
import { useAsync } from "../hooks";
import { useToast } from "../toast";
import { formatTime, parseApiTime } from "../utils";
import { ConfirmModal } from "./ConfirmModal";
import { Empty, LoadError, Loading } from "./States";

export const EXPIRY_OPTIONS: { label: string; days: number | null }[] = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
  { label: "Never", days: null },
];

function isExpired(token: ApiToken): boolean {
  return token.expires_at !== null && parseApiTime(token.expires_at) <= new Date();
}

/** Lists tokens with a Revoke action; `all` shows every user's tokens (admins). */
export function ApiTokenTable({ all = false, reloadKey = 0 }: { all?: boolean; reloadKey?: number }) {
  const toast = useToast();
  const tokens = useAsync(() => api.listTokens(all), [all, reloadKey]);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);

  if (tokens.error) return <LoadError message={tokens.error} onRetry={() => void tokens.reload()} />;
  if (tokens.loading && !tokens.data) return <Loading />;
  const rows = tokens.data ?? [];
  if (rows.length === 0) {
    return <Empty title="No API tokens" body={all ? undefined : "Create one for a script or CI job."} />;
  }

  return (
    <>
      <Table aria-label="API tokens" variant="compact">
        <Thead>
          <Tr>
            <Th>Name</Th>
            {all && <Th>User</Th>}
            <Th>Token</Th>
            <Th>Created</Th>
            <Th>Last used</Th>
            <Th>Expires</Th>
            <Th screenReaderText="Actions" />
          </Tr>
        </Thead>
        <Tbody>
          {rows.map((t) => (
            <Tr key={t.id}>
              <Td dataLabel="Name">{t.name}</Td>
              {all && <Td dataLabel="User">{t.username ?? "-"}</Td>}
              <Td dataLabel="Token">
                <code>{t.prefix}...</code>
              </Td>
              <Td dataLabel="Created">{formatTime(t.created_at)}</Td>
              <Td dataLabel="Last used">{t.last_used_at ? formatTime(t.last_used_at) : "Never"}</Td>
              <Td dataLabel="Expires">
                {t.expires_at ? (isExpired(t) ? "Expired" : formatTime(t.expires_at)) : "Never"}
              </Td>
              <Td isActionCell>
                <Button variant="link" isDanger onClick={() => setRevoking(t)}>
                  Revoke
                </Button>
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
      <ConfirmModal
        title="Revoke API token?"
        isOpen={revoking !== null}
        confirmLabel="Revoke"
        onClose={() => setRevoking(null)}
        onConfirm={async () => {
          if (!revoking) return;
          await api.revokeToken(revoking.id);
          toast.success("Token revoked");
          await tokens.reload(true);
        }}
      >
        Anything using <strong>{revoking?.name}</strong>
        {all && revoking?.username ? ` (${revoking.username})` : ""} stops working immediately.
      </ConfirmModal>
    </>
  );
}

/** Creates a token and shows it once, with a copy button and a usage example. */
export function CreateApiTokenModal({
  isOpen,
  onClose,
  onCreated,
}: {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [expiry, setExpiry] = useState("90");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);

  const close = () => {
    setName("");
    setExpiry("90");
    setError(null);
    setCreated(null);
    onClose();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const token = await api.createToken({
        name: name.trim(),
        expires_in_days: expiry === "never" ? null : Number(expiry),
      });
      setCreated(token);
      onCreated();
    } catch (err) {
      setError(errorMessage(err, "Could not create the token"));
    } finally {
      setBusy(false);
    }
  };

  const origin = window.location.origin;
  return (
    <Modal
      variant={ModalVariant.medium}
      title={created ? "Copy your new token" : "Create API token"}
      isOpen={isOpen}
      onClose={close}
      actions={
        created
          ? [
              <Button key="done" onClick={close}>
                Done
              </Button>,
            ]
          : [
              <Button
                key="create"
                type="submit"
                form="create-token"
                isLoading={busy}
                isDisabled={busy || !name.trim()}
              >
                Create
              </Button>,
              <Button key="cancel" variant="link" onClick={close}>
                Cancel
              </Button>,
            ]
      }
    >
      {created ? (
        <>
          <Alert
            variant="warning"
            isInline
            title="This is the only time the token is shown"
            style={{ marginBottom: 16 }}
          >
            Store it in your CI secrets or a password manager. It acts as you, with your role.
          </Alert>
          <ClipboardCopy isReadOnly hoverTip="Copy" clickTip="Copied" aria-label="API token">
            {created.token}
          </ClipboardCopy>
          <p style={{ margin: "16px 0 8px" }}>Submit a pull request for review:</p>
          <CodeBlock>
            <CodeBlockCode>
              {`curl -H "Authorization: Bearer $EAGLE_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"pr_url": "https://github.com/org/repo/pull/1"}' \\
  ${origin}/api/v1/reviews`}
            </CodeBlockCode>
          </CodeBlock>
          <p style={{ marginTop: 8 }}>
            Every endpoint is described at <a href="/docs">{origin}/docs</a>.
          </p>
        </>
      ) : (
        <Form id="create-token" onSubmit={submit}>
          {error && <Alert variant="danger" isInline title={error} />}
          <FormGroup label="Name" isRequired fieldId="token-name">
            <TextInput
              id="token-name"
              isRequired
              maxLength={64}
              value={name}
              placeholder="github-actions"
              onChange={(_e, v) => setName(v)}
            />
            <FormHelperText>
              <HelperText>
                <HelperTextItem>What uses it, so you know what breaks if you revoke it.</HelperTextItem>
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <FormGroup label="Expires after" fieldId="token-expiry">
            <FormSelect id="token-expiry" value={expiry} onChange={(_e, v) => setExpiry(v)}>
              {EXPIRY_OPTIONS.map((o) => (
                <FormSelectOption
                  key={o.label}
                  value={o.days === null ? "never" : String(o.days)}
                  label={o.label}
                />
              ))}
            </FormSelect>
          </FormGroup>
        </Form>
      )}
    </Modal>
  );
}
