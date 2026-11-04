import { FormEvent, useEffect, useState } from "react";
import {
  ActionGroup,
  Alert,
  Button,
  Card,
  CardBody,
  Form,
  FormGroup,
  FormHelperText,
  FormSelect,
  FormSelectOption,
  HelperText,
  HelperTextItem,
  PageSection,
  Switch,
  TextInput,
  Title,
} from "@patternfly/react-core";
import { AgentKind, AGENTS, api, errorMessage, PlatformSettings } from "../../api";
import { LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { AGENT_LABELS } from "../../utils";

function intInRange(value: string, min: number, max: number): boolean {
  const n = Number(value);
  return value.trim() !== "" && Number.isInteger(n) && n >= min && n <= max;
}

export function SettingsPage() {
  const toast = useToast();
  const settings = useAsync(() => api.getSettings(), []);
  const [maxConcurrent, setMaxConcurrent] = useState("3");
  const [pollerEnabled, setPollerEnabled] = useState(false);
  const [pollInterval, setPollInterval] = useState("15");
  const [postComment, setPostComment] = useState(false);
  const [defaultAgent, setDefaultAgent] = useState<AgentKind>("claude");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const s = settings.data;
    if (!s) return;
    setMaxConcurrent(String(s.max_concurrent_reviews));
    setPollerEnabled(s.poller_enabled);
    setPollInterval(String(s.poll_interval_minutes));
    setPostComment(s.post_comment_default);
    setDefaultAgent(s.default_agent);
  }, [settings.data]);

  const maxValid = intInRange(maxConcurrent, 1, 50);
  const intervalValid = intInRange(pollInterval, 1, 1440);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: PlatformSettings = {
      max_concurrent_reviews: Number(maxConcurrent),
      poller_enabled: pollerEnabled,
      poll_interval_minutes: Number(pollInterval),
      post_comment_default: postComment,
      default_agent: defaultAgent,
    };
    try {
      settings.setData(await api.updateSettings(body));
      toast.success("Settings saved");
    } catch (err) {
      setError(errorMessage(err, "Save failed"));
    } finally {
      setBusy(false);
    }
  };

  if (settings.loading && !settings.data) return <Loading />;

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Platform settings
      </Title>
      {settings.error && <LoadError message={settings.error} onRetry={() => void settings.reload()} />}
      <Card style={{ maxWidth: 720 }}>
        <CardBody>
          <Form onSubmit={submit}>
            {error && <Alert variant="danger" isInline title={error} />}
            <FormGroup label="Max concurrent reviews" isRequired fieldId="set-max">
              <TextInput
                id="set-max"
                type="number"
                min={1}
                max={50}
                value={maxConcurrent}
                validated={maxValid ? "default" : "error"}
                onChange={(_e, v) => setMaxConcurrent(v)}
              />
              <FormHelperText>
                <HelperText>
                  <HelperTextItem variant={maxValid ? "default" : "error"}>
                    Agent runs allowed at the same time (1 to 50). Others wait in the queue.
                  </HelperTextItem>
                </HelperText>
              </FormHelperText>
            </FormGroup>
            <FormGroup label="Default agent" fieldId="set-agent">
              <FormSelect
                id="set-agent"
                value={defaultAgent}
                onChange={(_e, v) => setDefaultAgent(v as AgentKind)}
              >
                {AGENTS.map((a) => (
                  <FormSelectOption key={a} value={a} label={AGENT_LABELS[a]} />
                ))}
              </FormSelect>
              <FormHelperText>
                <HelperText>
                  <HelperTextItem>
                    Used when neither the submitter nor a repository setting picks an agent.
                  </HelperTextItem>
                </HelperText>
              </FormHelperText>
            </FormGroup>
            <FormGroup label="Post reviews as PR comments by default" fieldId="set-post">
              <Switch
                id="set-post"
                label="On"
                labelOff="Off"
                isChecked={postComment}
                onChange={(_e, v) => setPostComment(v)}
              />
            </FormGroup>
            <FormGroup label="PR poller" fieldId="set-poller">
              <Switch
                id="set-poller"
                label="Enabled"
                labelOff="Disabled"
                isChecked={pollerEnabled}
                onChange={(_e, v) => setPollerEnabled(v)}
              />
              <FormHelperText>
                <HelperText>
                  <HelperTextItem>
                    Off by default. When on, tracked pull requests are checked periodically and
                    re-reviewed when their head commit changes.
                  </HelperTextItem>
                </HelperText>
              </FormHelperText>
            </FormGroup>
            <FormGroup label="Poll interval (minutes)" isRequired fieldId="set-interval">
              <TextInput
                id="set-interval"
                type="number"
                min={1}
                max={1440}
                value={pollInterval}
                validated={intervalValid ? "default" : "error"}
                onChange={(_e, v) => setPollInterval(v)}
              />
              <FormHelperText>
                <HelperText>
                  <HelperTextItem variant={intervalValid ? "default" : "error"}>
                    Default for tracked PRs without their own interval (1 to 1440).
                  </HelperTextItem>
                </HelperText>
              </FormHelperText>
            </FormGroup>
            <ActionGroup>
              <Button
                type="submit"
                isLoading={busy}
                isDisabled={busy || !maxValid || !intervalValid}
              >
                Save
              </Button>
            </ActionGroup>
          </Form>
        </CardBody>
      </Card>
    </PageSection>
  );
}
