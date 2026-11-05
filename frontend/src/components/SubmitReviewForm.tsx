import { FormEvent, useState } from "react";
import {
  ActionGroup,
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  Form,
  FormGroup,
  FormHelperText,
  FormSelect,
  FormSelectOption,
  Grid,
  GridItem,
  HelperText,
  HelperTextItem,
  Label,
  TextArea,
  TextInput,
} from "@patternfly/react-core";
import ExclamationTriangleIcon from "@patternfly/react-icons/dist/esm/icons/exclamation-triangle-icon";
import { AgentKind, api, errorMessage, ReviewCreate, ReviewDetail } from "../api";
import { useAsync } from "../hooks";
import { AGENT_LABELS, fromTriState, TriState } from "../utils";
import { TriStateSelect } from "./TriStateSelect";

export const CUSTOM_PROMPT_MAX = 8000;

interface Props {
  onSubmitted: (review: ReviewDetail) => void;
}

export function SubmitReviewForm({ onSubmitted }: Props) {
  const options = useAsync(() => api.reviewOptions(), []);
  const [prUrl, setPrUrl] = useState("");
  const [agent, setAgent] = useState<AgentKind | "">("");
  const [model, setModel] = useState("");
  const [customPrompt, setCustomPrompt] = useState("");
  const [postComment, setPostComment] = useState<TriState>("default");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const agents = options.data ?? [];
  const selected = agents.find((o) => o.agent === agent);
  const noAgents = options.data !== undefined && agents.length === 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const payload: ReviewCreate = {
      pr_url: prUrl.trim(),
      agent: agent || null,
      model: model.trim() || null,
      custom_prompt: customPrompt.trim() || null,
      post_comment: fromTriState(postComment),
    };
    setBusy(true);
    try {
      const review = await api.submitReview(payload);
      setPrUrl("");
      setCustomPrompt("");
      setModel("");
      setPostComment("default");
      onSubmitted(review);
    } catch (err) {
      setError(errorMessage(err, "Submit failed"));
    } finally {
      setBusy(false);
    }
  };

  const modelField =
    selected && selected.allowed_models.length > 0 ? (
      <FormSelect id="review-model" value={model} onChange={(_e, v) => setModel(v)}>
        <FormSelectOption
          value=""
          label={
            selected.default_model ? `Agent default (${selected.default_model})` : "CLI default"
          }
        />
        {selected.allowed_models.map((m) => (
          <FormSelectOption key={m} value={m} label={m} />
        ))}
      </FormSelect>
    ) : (
      <TextInput
        id="review-model"
        value={model}
        onChange={(_e, v) => setModel(v)}
        placeholder={
          !selected
            ? "Agent default"
            : selected.default_model
              ? `Agent default: ${selected.default_model}`
              : "CLI default"
        }
      />
    );
  const modelHelp = !selected
    ? "Leave blank to use the agent's default model"
    : selected.default_model
      ? `Leave blank to use the agent default, ${selected.default_model}`
      : "No default model is set for this agent, so the agent CLI picks one. The review shows the model it reports.";

  return (
    <Card>
      <CardTitle>Request a review</CardTitle>
      <CardBody>
        {error && (
          <Alert variant="danger" isInline title={error} style={{ marginBottom: 16 }} />
        )}
        {options.error && (
          <Alert
            variant="warning"
            isInline
            title={`Could not load agent options: ${options.error}`}
            style={{ marginBottom: 16 }}
          />
        )}
        {noAgents && (
          <Alert
            variant="warning"
            isInline
            title="No agents are enabled. Ask an admin to enable one under Admin > Agents."
            style={{ marginBottom: 16 }}
          />
        )}
        <Form onSubmit={submit}>
          <Grid hasGutter>
            <GridItem md={12} lg={6}>
              <FormGroup label="Pull request URL" isRequired fieldId="review-pr-url">
                <TextInput
                  id="review-pr-url"
                  isRequired
                  type="url"
                  value={prUrl}
                  placeholder="https://github.com/org/repo/pull/123"
                  onChange={(_e, v) => setPrUrl(v)}
                />
              </FormGroup>
            </GridItem>
            <GridItem md={4} lg={2}>
              <FormGroup label="Agent" fieldId="review-agent">
                <FormSelect
                  id="review-agent"
                  value={agent}
                  onChange={(_e, v) => {
                    setAgent(v as AgentKind | "");
                    setModel("");
                  }}
                >
                  <FormSelectOption value="" label="Default (repo or platform)" />
                  {agents.map((o) => (
                    <FormSelectOption
                      key={o.agent}
                      value={o.agent}
                      label={`${AGENT_LABELS[o.agent]}${o.configured ? "" : " (no credential)"}`}
                    />
                  ))}
                </FormSelect>
                {selected && !selected.configured && (
                  <FormHelperText>
                    <Label color="orange" icon={<ExclamationTriangleIcon />} isCompact>
                      No credential configured
                    </Label>
                  </FormHelperText>
                )}
              </FormGroup>
            </GridItem>
            <GridItem md={4} lg={2}>
              <FormGroup label="Model" fieldId="review-model">
                {modelField}
                <FormHelperText>
                  <HelperText>
                    <HelperTextItem>{modelHelp}</HelperTextItem>
                  </HelperText>
                </FormHelperText>
              </FormGroup>
            </GridItem>
            <GridItem md={4} lg={2}>
              <FormGroup label="Post review as PR comment" fieldId="review-post-comment">
                <TriStateSelect
                  id="review-post-comment"
                  value={postComment}
                  onChange={setPostComment}
                  defaultLabel="Default"
                />
              </FormGroup>
            </GridItem>
            <GridItem span={12}>
              <FormGroup
                label="Additional context for the reviewer (optional)"
                fieldId="review-custom-prompt"
              >
                <TextArea
                  id="review-custom-prompt"
                  value={customPrompt}
                  onChange={(_e, v) => setCustomPrompt(v.slice(0, CUSTOM_PROMPT_MAX))}
                  maxLength={CUSTOM_PROMPT_MAX}
                  rows={3}
                  resizeOrientation="vertical"
                  placeholder="e.g. Focus on the DB migration. This replaces the v1 API, so backward compatibility is not needed."
                />
                <FormHelperText>
                  <HelperText>
                    <HelperTextItem
                      variant={customPrompt.length >= CUSTOM_PROMPT_MAX ? "warning" : "default"}
                    >
                      {customPrompt.length} / {CUSTOM_PROMPT_MAX} characters
                    </HelperTextItem>
                  </HelperText>
                </FormHelperText>
              </FormGroup>
            </GridItem>
          </Grid>
          <ActionGroup>
            <Button
              type="submit"
              isDisabled={!prUrl.trim() || busy || noAgents}
              isLoading={busy}
            >
              Submit review
            </Button>
          </ActionGroup>
        </Form>
      </CardBody>
    </Card>
  );
}
