import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Flex, FlexItem, SearchInput } from "@patternfly/react-core";
import { api, errorMessage, ModelList } from "../api";

interface Props {
  id: string;
  /** Credential to ask. Unsaved selections work too: the list comes from the credential. */
  credentialId: number | null;
  allowed: string[];
  onAllowedChange: (models: string[]) => void;
  /** Reports what was fetched, so other inputs can offer the same names. */
  onFetched?: (models: string[]) => void;
}

/**
 * "Fetch models" for an agent: lists what the credential's endpoint serves (or
 * common choices when it cannot list) and lets the admin tick the allowed ones.
 */
export function ModelPicker({ id, credentialId, allowed, onAllowedChange, onFetched }: Props) {
  const [list, setList] = useState<ModelList | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  // A different credential serves different models: drop the old list.
  useEffect(() => {
    setList(null);
    setError(null);
    setFilter("");
  }, [credentialId]);

  const fetchModels = async () => {
    if (credentialId === null) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.credentialModels(credentialId);
      setList(result);
      onFetched?.(result.models);
    } catch (err) {
      setError(errorMessage(err, "Could not fetch models"));
    } finally {
      setBusy(false);
    }
  };

  const needle = filter.trim().toLowerCase();
  const shown = (list?.models ?? []).filter((m) => m.toLowerCase().includes(needle));
  const toggle = (model: string, on: boolean) =>
    onAllowedChange(on ? [...allowed, model] : allowed.filter((m) => m !== model));

  return (
    <div style={{ marginTop: 8 }}>
      <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
        <FlexItem>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void fetchModels()}
            isLoading={busy}
            isDisabled={busy || credentialId === null}
          >
            {list ? "Refresh models" : "Fetch models"}
          </Button>
        </FlexItem>
        {credentialId === null && (
          <FlexItem>
            <small>Choose a credential first.</small>
          </FlexItem>
        )}
      </Flex>
      {error && <Alert variant="danger" isInline isPlain title={error} style={{ marginTop: 8 }} />}
      {list && (
        <div style={{ marginTop: 8 }}>
          {list.message && (
            <Alert variant="info" isInline isPlain title={list.message} style={{ marginBottom: 8 }} />
          )}
          {list.models.length > 8 && (
            <SearchInput
              aria-label="Filter models"
              placeholder="Filter models"
              value={filter}
              onChange={(_e, v) => setFilter(v)}
              onClear={() => setFilter("")}
              style={{ marginBottom: 8 }}
            />
          )}
          {list.models.length === 0 ? (
            <small>No models to show. Type model names above instead.</small>
          ) : (
            <div
              role="group"
              aria-label="Available models"
              style={{ maxHeight: 220, overflowY: "auto", paddingLeft: 2 }}
            >
              {shown.map((m) => (
                <Checkbox
                  key={m}
                  id={`${id}-model-${m}`}
                  label={m}
                  isChecked={allowed.includes(m)}
                  onChange={(_e, on) => toggle(m, on)}
                />
              ))}
              {shown.length === 0 && <small>No model matches "{filter}".</small>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
