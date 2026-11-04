import { FormEvent, useEffect, useState } from "react";
import {
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
  Modal,
  ModalVariant,
  PageSection,
  TextArea,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { AgentKind, AGENTS, api, errorMessage, RepoSetting } from "../../api";
import { ConfirmModal } from "../../components/ConfirmModal";
import { Empty, LoadError, Loading } from "../../components/States";
import { TriStateSelect } from "../../components/TriStateSelect";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { AGENT_LABELS, fromTriState, toTriState, TriState } from "../../utils";

function RepoModal({
  isOpen,
  setting,
  onClose,
  onSaved,
}: {
  isOpen: boolean;
  setting: RepoSetting | null;
  onClose: () => void;
  onSaved: (s: RepoSetting) => void;
}) {
  const [pattern, setPattern] = useState("");
  const [instructions, setInstructions] = useState("");
  const [agent, setAgent] = useState<AgentKind | "">("");
  const [postComment, setPostComment] = useState<TriState>("default");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setPattern(setting?.pattern ?? "");
    setInstructions(setting?.instructions ?? "");
    setAgent(setting?.default_agent ?? "");
    setPostComment(toTriState(setting?.post_comment));
    setError(null);
  }, [isOpen, setting]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      pattern: pattern.trim(),
      instructions: instructions.trim() || null,
      default_agent: agent || null,
      post_comment: fromTriState(postComment),
    };
    try {
      onSaved(
        setting
          ? await api.updateRepoSetting(setting.id, body)
          : await api.createRepoSetting(body),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      variant={ModalVariant.medium}
      title={setting ? `Edit ${setting.pattern}` : "Add repository setting"}
      isOpen={isOpen}
      onClose={onClose}
      actions={[
        <Button
          key="save"
          type="submit"
          form="repo-form"
          isLoading={busy}
          isDisabled={busy || !pattern.trim()}
        >
          Save
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="repo-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="Repository pattern" isRequired fieldId="repo-pattern">
          <TextInput
            id="repo-pattern"
            isRequired
            value={pattern}
            placeholder="org/* or org/repo"
            onChange={(_e, v) => setPattern(v)}
          />
          <FormHelperText>
            <HelperText>
              <HelperTextItem>
                Matches owner/name. Use org/* for every repository in an organization. More
                specific patterns win.
              </HelperTextItem>
            </HelperText>
          </FormHelperText>
        </FormGroup>
        <FormGroup label="Default agent" fieldId="repo-agent">
          <FormSelect id="repo-agent" value={agent} onChange={(_e, v) => setAgent(v as AgentKind | "")}>
            <FormSelectOption value="" label="Platform default" />
            {AGENTS.map((a) => (
              <FormSelectOption key={a} value={a} label={AGENT_LABELS[a]} />
            ))}
          </FormSelect>
        </FormGroup>
        <FormGroup label="Post review as PR comment" fieldId="repo-post-comment">
          <TriStateSelect
            id="repo-post-comment"
            value={postComment}
            onChange={setPostComment}
            defaultLabel="Platform default"
          />
        </FormGroup>
        <FormGroup label="Repository review instructions" fieldId="repo-instructions">
          <TextArea
            id="repo-instructions"
            value={instructions}
            maxLength={20000}
            rows={6}
            resizeOrientation="vertical"
            onChange={(_e, v) => setInstructions(v)}
            placeholder="Repository specific guidance, for example architecture rules or areas that need extra scrutiny."
          />
        </FormGroup>
      </Form>
    </Modal>
  );
}

function triLabel(v: boolean | null): string {
  return v === null ? "default" : v ? "yes" : "no";
}

export function RepositoriesPage() {
  const toast = useToast();
  const settings = useAsync(() => api.listRepoSettings(), []);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<RepoSetting | null>(null);
  const [deleting, setDeleting] = useState<RepoSetting | null>(null);
  const rows = settings.data ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Repository settings
      </Title>
      <Card>
        <CardBody>
          <Toolbar>
            <ToolbarContent>
              <ToolbarItem>
                <Button
                  onClick={() => {
                    setEditing(null);
                    setModalOpen(true);
                  }}
                >
                  Add repository setting
                </Button>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {settings.error && (
            <LoadError message={settings.error} onRetry={() => void settings.reload()} />
          )}
          {settings.loading && !settings.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty
              title="No repository settings"
              body="Add per-repository instructions, a default agent or a PR comment policy."
            />
          ) : (
            <Table aria-label="Repository settings" variant="compact">
              <Thead>
                <Tr>
                  <Th>Pattern</Th>
                  <Th>Default agent</Th>
                  <Th>Post comment</Th>
                  <Th>Instructions</Th>
                  <Th screenReaderText="Actions" />
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((s) => (
                  <Tr key={s.id}>
                    <Td dataLabel="Pattern">
                      <code>{s.pattern}</code>
                    </Td>
                    <Td dataLabel="Default agent">
                      {s.default_agent ? AGENT_LABELS[s.default_agent] : "platform default"}
                    </Td>
                    <Td dataLabel="Post comment">{triLabel(s.post_comment)}</Td>
                    <Td dataLabel="Instructions" modifier="truncate">
                      {s.instructions ?? "-"}
                    </Td>
                    <Td isActionCell>
                      <ActionsColumn
                        items={[
                          {
                            title: "Edit",
                            onClick: () => {
                              setEditing(s);
                              setModalOpen(true);
                            },
                          },
                          { isSeparator: true },
                          { title: "Delete", onClick: () => setDeleting(s) },
                        ]}
                      />
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          )}
        </CardBody>
      </Card>
      <RepoModal
        isOpen={modalOpen}
        setting={editing}
        onClose={() => setModalOpen(false)}
        onSaved={(saved) => {
          setModalOpen(false);
          const list = settings.data ?? [];
          settings.setData(
            list.some((x) => x.id === saved.id)
              ? list.map((x) => (x.id === saved.id ? saved : x))
              : [...list, saved],
          );
          toast.success(`Saved ${saved.pattern}`);
        }}
      />
      <ConfirmModal
        title="Delete repository setting?"
        isOpen={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return;
          await api.deleteRepoSetting(deleting.id);
          settings.setData((settings.data ?? []).filter((x) => x.id !== deleting.id));
          toast.success(`Deleted ${deleting.pattern}`);
        }}
      >
        Settings for <code>{deleting?.pattern}</code> will be removed.
      </ConfirmModal>
    </PageSection>
  );
}
