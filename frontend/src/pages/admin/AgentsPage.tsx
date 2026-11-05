import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ActionGroup,
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Form,
  FormGroup,
  FormHelperText,
  FormSelect,
  FormSelectOption,
  Gallery,
  HelperText,
  HelperTextItem,
  Label,
  PageSection,
  Switch,
  TextArea,
  TextInput,
  Title,
} from "@patternfly/react-core";
import {
  AgentConfig,
  api,
  Credential,
  CredentialKind,
  errorMessage,
} from "../../api";
import { ChipListInput } from "../../components/ChipListInput";
import { ModelPicker } from "../../components/ModelPicker";
import { Empty, LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { AGENT_LABELS, CREDENTIAL_KIND_LABELS } from "../../utils";

/** Gateways serve their own model names, so the CLI's built in default rarely fits. */
const GATEWAY_CREDENTIAL_KINDS: CredentialKind[] = [
  "anthropic_compatible",
  "openai_compatible",
];

function AgentCard({
  config,
  credentials,
  onSaved,
}: {
  config: AgentConfig;
  credentials: Credential[];
  onSaved: (c: AgentConfig) => void;
}) {
  const toast = useToast();
  const [enabled, setEnabled] = useState(config.enabled);
  const [defaultModel, setDefaultModel] = useState(config.default_model ?? "");
  const [allowed, setAllowed] = useState<string[]>(config.allowed_models);
  const [credentialId, setCredentialId] = useState(
    config.credential_id === null ? "" : String(config.credential_id),
  );
  const [instructions, setInstructions] = useState(config.instructions ?? "");
  const [timeout, setTimeoutSeconds] = useState(String(config.timeout_seconds));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);

  useEffect(() => {
    setEnabled(config.enabled);
    setDefaultModel(config.default_model ?? "");
    setAllowed(config.allowed_models);
    setCredentialId(
      config.credential_id === null ? "" : String(config.credential_id),
    );
    setInstructions(config.instructions ?? "");
    setTimeoutSeconds(String(config.timeout_seconds));
  }, [config]);

  const compatible = credentials.filter((c) =>
    config.compatible_credential_kinds.includes(c.kind),
  );
  const timeoutNum = Number(timeout);
  const timeoutValid =
    Number.isInteger(timeoutNum) && timeoutNum >= 60 && timeoutNum <= 7200;
  const defaultNotAllowed =
    !!defaultModel && allowed.length > 0 && !allowed.includes(defaultModel);

  const changeAllowed = (v: string[]) => {
    setAllowed(v);
    if (defaultModel && v.length > 0 && !v.includes(defaultModel))
      setDefaultModel("");
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await api.updateAgent(config.agent, {
        enabled,
        default_model: defaultModel.trim() || null,
        allowed_models: allowed,
        credential_id: credentialId ? Number(credentialId) : null,
        instructions: instructions.trim() || null,
        timeout_seconds: timeoutNum,
      });
      onSaved(saved);
      toast.success(`${AGENT_LABELS[config.agent]} settings saved`);
    } catch (err) {
      setError(errorMessage(err, "Save failed"));
    } finally {
      setBusy(false);
    }
  };

  const id = (name: string) => `agent-${config.agent}-${name}`;
  const selectedCredential = credentials.find(
    (c) => String(c.id) === credentialId,
  );
  const needsDefaultModel =
    selectedCredential !== undefined &&
    GATEWAY_CREDENTIAL_KINDS.includes(selectedCredential.kind) &&
    !defaultModel.trim();

  return (
    <Card>
      <CardHeader
        actions={{
          actions: (
            <Switch
              id={id("enabled")}
              label="Enabled"
              labelOff="Disabled"
              isChecked={enabled}
              onChange={(_e, v) => setEnabled(v)}
            />
          ),
          hasNoOffset: true,
        }}
      >
        <CardTitle>
          {AGENT_LABELS[config.agent] ?? config.agent}{" "}
          {!config.credential_id && (
            <Label color="orange" isCompact>
              no credential
            </Label>
          )}
        </CardTitle>
      </CardHeader>
      <CardBody>
        <Form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          {error && <Alert variant="danger" isInline title={error} />}
          <FormGroup label="Inference credential" fieldId={id("credential")}>
            <FormSelect
              id={id("credential")}
              value={credentialId}
              onChange={(_e, v) => setCredentialId(v)}
            >
              <FormSelectOption value="" label="None" />
              {compatible.map((c) => (
                <FormSelectOption
                  key={c.id}
                  value={String(c.id)}
                  label={`${c.name} (${CREDENTIAL_KIND_LABELS[c.kind] ?? c.kind})`}
                />
              ))}
            </FormSelect>
            <FormHelperText>
              <HelperText>
                <HelperTextItem>
                  {compatible.length === 0 ? (
                    <>
                      No compatible credentials yet.{" "}
                      <Link to="/admin/credentials">
                        Add one under Credentials
                      </Link>
                      .
                    </>
                  ) : (
                    `Compatible kinds: ${config.compatible_credential_kinds
                      .map((k) => CREDENTIAL_KIND_LABELS[k] ?? k)
                      .join(", ")}`
                  )}
                </HelperTextItem>
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <FormGroup label="Allowed models" fieldId={id("allowed")}>
            <ChipListInput
              id={id("allowed")}
              ariaLabel={`Allowed models for ${config.agent}`}
              values={allowed}
              onChange={changeAllowed}
              placeholder="Type a model and press Enter"
            />
            <ModelPicker
              id={id("picker")}
              credentialId={credentialId ? Number(credentialId) : null}
              allowed={allowed}
              onAllowedChange={changeAllowed}
              onFetched={setFetchedModels}
            />
            <FormHelperText>
              <HelperText>
                <HelperTextItem>
                  Models members may pick when submitting. Empty means any model
                  name is accepted.
                </HelperTextItem>
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <FormGroup label="Default model" fieldId={id("default-model")}>
            {allowed.length > 0 ? (
              <FormSelect
                id={id("default-model")}
                value={defaultNotAllowed ? "" : defaultModel}
                onChange={(_e, v) => setDefaultModel(v)}
              >
                <FormSelectOption value="" label="None (agent CLI default)" />
                {allowed.map((m) => (
                  <FormSelectOption key={m} value={m} label={m} />
                ))}
              </FormSelect>
            ) : (
              <>
                <TextInput
                  id={id("default-model")}
                  value={defaultModel}
                  onChange={(_e, v) => setDefaultModel(v)}
                  placeholder="Agent CLI default"
                  list={
                    fetchedModels.length ? id("default-options") : undefined
                  }
                  autoComplete="off"
                />
                {fetchedModels.length > 0 && (
                  <datalist id={id("default-options")}>
                    {fetchedModels.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                )}
              </>
            )}
            <FormHelperText>
              <HelperText>
                <HelperTextItem>
                  Used for every review that does not choose a model, including
                  poller re-reviews.
                </HelperTextItem>
                {needsDefaultModel && (
                  <HelperTextItem variant="warning" hasIcon>
                    {selectedCredential?.name} is a gateway. Without a default
                    model the agent CLI asks it for the CLI&apos;s own default
                    model name, which the gateway may not serve or may map to
                    another model. Set a model the gateway serves.
                  </HelperTextItem>
                )}
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <FormGroup
            label="Team review instructions"
            fieldId={id("instructions")}
          >
            <TextArea
              id={id("instructions")}
              value={instructions}
              onChange={(_e, v) => setInstructions(v)}
              rows={5}
              maxLength={20000}
              resizeOrientation="vertical"
              placeholder="Conventions, priorities or checklists every review by this agent should follow."
            />
          </FormGroup>
          <FormGroup
            label="Timeout (seconds)"
            isRequired
            fieldId={id("timeout")}
          >
            <TextInput
              id={id("timeout")}
              type="number"
              min={60}
              max={7200}
              value={timeout}
              validated={timeoutValid ? "default" : "error"}
              onChange={(_e, v) => setTimeoutSeconds(v)}
            />
            <FormHelperText>
              <HelperText>
                <HelperTextItem variant={timeoutValid ? "default" : "error"}>
                  Between 60 and 7200
                </HelperTextItem>
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <ActionGroup>
            <Button
              type="submit"
              isLoading={busy}
              isDisabled={busy || !timeoutValid}
            >
              Save
            </Button>
          </ActionGroup>
        </Form>
      </CardBody>
    </Card>
  );
}

export function AgentsPage() {
  const agents = useAsync(() => api.listAgents(), []);
  const credentials = useAsync(() => api.listCredentials(), []);

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 8 }}>
        Agents
      </Title>
      <Alert
        variant="info"
        isInline
        isPlain
        title="Assign each agent its default model, the models members may choose, and the credential it runs with."
        style={{ marginBottom: 16 }}
      />
      {agents.error && (
        <LoadError
          message={agents.error}
          onRetry={() => void agents.reload()}
        />
      )}
      {credentials.error && (
        <LoadError
          message={credentials.error}
          onRetry={() => void credentials.reload()}
        />
      )}
      {agents.loading && !agents.data ? (
        <Loading />
      ) : (agents.data ?? []).length === 0 ? (
        <Empty title="No agents" />
      ) : (
        <Gallery hasGutter minWidths={{ default: "100%", lg: "420px" }}>
          {(agents.data ?? []).map((a) => (
            <AgentCard
              key={a.agent}
              config={a}
              credentials={credentials.data ?? []}
              onSaved={(saved) =>
                agents.setData(
                  (agents.data ?? []).map((x) =>
                    x.agent === saved.agent ? saved : x,
                  ),
                )
              }
            />
          ))}
        </Gallery>
      )}
    </PageSection>
  );
}
