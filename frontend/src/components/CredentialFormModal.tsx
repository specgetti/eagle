import { FormEvent, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Form,
  FormGroup,
  FormHelperText,
  FormSelect,
  FormSelectOption,
  HelperText,
  HelperTextItem,
  Modal,
  ModalVariant,
  TextArea,
  TextInput,
} from "@patternfly/react-core";
import {
  api,
  Credential,
  CredentialField,
  CredentialKind,
  CredentialType,
  errorMessage,
} from "../api";
import { AGENT_LABELS, CREDENTIAL_KIND_LABELS, stringValue } from "../utils";

export const SUBSCRIPTION_WARNING =
  "Personal subscriptions shared by a team service may conflict with the provider's terms. Prefer API keys, Vertex AI or MaaS.";

interface Props {
  isOpen: boolean;
  types: CredentialType[];
  /** When set, the modal edits this credential; otherwise it creates one. */
  credential?: Credential | null;
  onClose: () => void;
  onSaved: (credential: Credential) => void;
}

function isMultiline(field: CredentialField): boolean {
  return field.name.endsWith("_json");
}

/**
 * Build the `values` object sent to the API. Public fields are always sent so they
 * can be cleared. Secret fields are sent only when non-blank; on edit, blank means
 * "keep the stored value".
 */
export function buildCredentialValues(
  type: CredentialType,
  values: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of type.fields) {
    const raw = values[field.name] ?? "";
    const v = isMultiline(field) ? raw : raw.trim();
    if (field.secret) {
      if (v.trim()) out[field.name] = v;
    } else {
      out[field.name] = v;
    }
  }
  return out;
}

export function CredentialFormModal({ isOpen, types, credential, onClose, onSaved }: Props) {
  const editing = !!credential;
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CredentialKind | "">("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    if (credential) {
      setName(credential.name);
      setKind(credential.kind);
      const initial: Record<string, string> = {};
      for (const [k, v] of Object.entries(credential.public_config)) initial[k] = stringValue(v);
      setValues(initial);
    } else {
      setName("");
      setKind(types[0]?.kind ?? "");
      setValues({});
    }
  }, [isOpen, credential, types]);

  const type = types.find((t) => t.kind === kind);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!type) return;
    setError(null);
    setBusy(true);
    try {
      const payloadValues = buildCredentialValues(type, values);
      const saved = credential
        ? await api.updateCredential(credential.id, {
            name: name.trim() !== credential.name ? name.trim() : undefined,
            values: payloadValues,
          })
        : await api.createCredential({ name: name.trim(), kind: type.kind, values: payloadValues });
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err, "Save failed"));
    } finally {
      setBusy(false);
    }
  };

  const renderField = (field: CredentialField) => {
    const id = `cred-field-${field.name}`;
    const value = values[field.name] ?? "";
    const set = (v: string) => setValues((prev) => ({ ...prev, [field.name]: v }));
    const masked = credential?.secret_fields[field.name];
    const required = field.required && !(editing && field.secret);
    const placeholder =
      editing && field.secret
        ? masked
          ? `Stored (${masked}). Leave blank to keep.`
          : "Leave blank to keep"
        : undefined;
    return (
      <FormGroup key={field.name} label={field.label} isRequired={required} fieldId={id}>
        {field.choices?.length ? (
          <FormSelect id={id} value={value} onChange={(_e, v) => set(v)}>
            {!field.required && (
              <FormSelectOption value="" label={`Default (${field.choices[0]})`} />
            )}
            {field.choices.map((c) => (
              <FormSelectOption key={c} value={c} label={c} />
            ))}
          </FormSelect>
        ) : isMultiline(field) ? (
          <TextArea
            id={id}
            value={value}
            onChange={(_e, v) => set(v)}
            rows={6}
            resizeOrientation="vertical"
            placeholder={placeholder}
            isRequired={required}
            spellCheck={false}
            style={{ fontFamily: "var(--pf-v5-global--FontFamily--monospace)" }}
          />
        ) : (
          <TextInput
            id={id}
            type={field.secret ? "password" : "text"}
            autoComplete={field.secret ? "new-password" : "off"}
            value={value}
            onChange={(_e, v) => set(v)}
            placeholder={placeholder}
            isRequired={required}
          />
        )}
        {(field.help || (editing && field.secret)) && (
          <FormHelperText>
            <HelperText>
              {field.help && <HelperTextItem>{field.help}</HelperTextItem>}
              {editing && field.secret && (
                <HelperTextItem>Secret. Blank keeps the current value.</HelperTextItem>
              )}
            </HelperText>
          </FormHelperText>
        )}
      </FormGroup>
    );
  };

  return (
    <Modal
      variant={ModalVariant.medium}
      title={editing ? `Edit credential ${credential?.name}` : "Add credential"}
      isOpen={isOpen}
      onClose={onClose}
      actions={[
        <Button
          key="save"
          type="submit"
          form="credential-form"
          isLoading={busy}
          isDisabled={busy || !type || !name.trim()}
        >
          {editing ? "Save" : "Create"}
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="credential-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="Name" isRequired fieldId="cred-name">
          <TextInput id="cred-name" isRequired value={name} onChange={(_e, v) => setName(v)} />
        </FormGroup>
        <FormGroup label="Kind" isRequired fieldId="cred-kind">
          <FormSelect
            id="cred-kind"
            value={kind}
            isDisabled={editing}
            onChange={(_e, v) => {
              setKind(v as CredentialKind);
              setValues({});
            }}
          >
            {types.map((t) => (
              <FormSelectOption
                key={t.kind}
                value={t.kind}
                label={CREDENTIAL_KIND_LABELS[t.kind] ?? t.kind}
              />
            ))}
          </FormSelect>
          {type && (
            <FormHelperText>
              <HelperText>
                <HelperTextItem>
                  Usable by: {type.agents.map((a) => AGENT_LABELS[a] ?? a).join(", ") || "none"}
                </HelperTextItem>
              </HelperText>
            </FormHelperText>
          )}
        </FormGroup>
        {type?.subscription && (
          <Alert variant="warning" isInline title="Subscription credential">
            {SUBSCRIPTION_WARNING}
          </Alert>
        )}
        {type?.fields.map(renderField)}
      </Form>
    </Modal>
  );
}
