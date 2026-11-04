import { FormEvent, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  CardBody,
  Checkbox,
  Form,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  Label,
  Modal,
  ModalVariant,
  PageSection,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { api, errorMessage, GitHost, GitHostIn } from "../../api";
import { ConfirmModal } from "../../components/ConfirmModal";
import { Empty, LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { formatTime } from "../../utils";

function HostModal({
  isOpen,
  host,
  onClose,
  onSaved,
}: {
  isOpen: boolean;
  host: GitHost | null;
  onClose: () => void;
  onSaved: (h: GitHost) => void;
}) {
  const [name, setName] = useState("");
  const [apiUrl, setApiUrl] = useState("");
  const [token, setToken] = useState("");
  const [removeToken, setRemoveToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setName(host?.host ?? "");
    setApiUrl(host?.api_url ?? "");
    setToken("");
    setRemoveToken(false);
    setError(null);
  }, [isOpen, host]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    // token: null keeps the stored token, "" removes it, a value replaces it.
    const body: GitHostIn = {
      host: name.trim(),
      api_url: apiUrl.trim() || null,
      token: removeToken ? "" : token.trim() || null,
    };
    try {
      onSaved(host ? await api.updateGitHost(host.id, body) : await api.createGitHost(body));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      variant={ModalVariant.small}
      title={host ? `Edit ${host.host}` : "Add git host"}
      isOpen={isOpen}
      onClose={onClose}
      actions={[
        <Button
          key="save"
          type="submit"
          form="git-host-form"
          isLoading={busy}
          isDisabled={busy || !name.trim()}
        >
          Save
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="git-host-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="Host" isRequired fieldId="gh-host">
          <TextInput
            id="gh-host"
            isRequired
            value={name}
            placeholder="github.com or github.example.com"
            onChange={(_e, v) => setName(v)}
          />
        </FormGroup>
        <FormGroup label="API URL" fieldId="gh-api-url">
          <TextInput
            id="gh-api-url"
            value={apiUrl}
            placeholder="https://github.example.com/api/v3"
            onChange={(_e, v) => setApiUrl(v)}
          />
          <FormHelperText>
            <HelperText>
              <HelperTextItem>Optional. Leave blank to derive it from the host.</HelperTextItem>
            </HelperText>
          </FormHelperText>
        </FormGroup>
        <FormGroup label="Token" fieldId="gh-token">
          <TextInput
            id="gh-token"
            type="password"
            autoComplete="new-password"
            value={token}
            isDisabled={removeToken}
            placeholder={host?.has_token ? "Stored. Leave blank to keep." : "Read access token"}
            onChange={(_e, v) => setToken(v)}
          />
          <FormHelperText>
            <HelperText>
              <HelperTextItem>
                Read access for cloning private repositories; write access to pull requests if
                reviews are posted as comments.
              </HelperTextItem>
            </HelperText>
          </FormHelperText>
        </FormGroup>
        {host?.has_token && (
          <Checkbox
            id="gh-remove-token"
            label="Remove the stored token"
            isChecked={removeToken}
            onChange={(_e, v) => setRemoveToken(v)}
          />
        )}
      </Form>
    </Modal>
  );
}

export function GitHostsPage() {
  const toast = useToast();
  const hosts = useAsync(() => api.listGitHosts(), []);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<GitHost | null>(null);
  const [deleting, setDeleting] = useState<GitHost | null>(null);
  const rows = hosts.data ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Git hosts
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
                  Add git host
                </Button>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {hosts.error && <LoadError message={hosts.error} onRetry={() => void hosts.reload()} />}
          {hosts.loading && !hosts.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title="No git hosts" body="Add a host and token to review private repositories." />
          ) : (
            <Table aria-label="Git hosts" variant="compact">
              <Thead>
                <Tr>
                  <Th>Host</Th>
                  <Th>API URL</Th>
                  <Th>Token</Th>
                  <Th>Created</Th>
                  <Th screenReaderText="Actions" />
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((h) => (
                  <Tr key={h.id}>
                    <Td dataLabel="Host">{h.host}</Td>
                    <Td dataLabel="API URL">
                      <code>{h.api_url}</code>
                    </Td>
                    <Td dataLabel="Token">
                      {h.has_token ? (
                        <Label color="green" isCompact>
                          set
                        </Label>
                      ) : (
                        <Label color="grey" isCompact>
                          none
                        </Label>
                      )}
                    </Td>
                    <Td dataLabel="Created">{formatTime(h.created_at)}</Td>
                    <Td isActionCell>
                      <ActionsColumn
                        items={[
                          {
                            title: "Edit",
                            onClick: () => {
                              setEditing(h);
                              setModalOpen(true);
                            },
                          },
                          { isSeparator: true },
                          { title: "Delete", onClick: () => setDeleting(h) },
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
      <HostModal
        isOpen={modalOpen}
        host={editing}
        onClose={() => setModalOpen(false)}
        onSaved={(saved) => {
          setModalOpen(false);
          const list = hosts.data ?? [];
          hosts.setData(
            list.some((x) => x.id === saved.id)
              ? list.map((x) => (x.id === saved.id ? saved : x))
              : [...list, saved],
          );
          toast.success(`Saved ${saved.host}`);
        }}
      />
      <ConfirmModal
        title="Delete git host?"
        isOpen={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return;
          await api.deleteGitHost(deleting.id);
          hosts.setData((hosts.data ?? []).filter((x) => x.id !== deleting.id));
          toast.success(`Deleted ${deleting.host}`);
        }}
      >
        <strong>{deleting?.host}</strong> and its token will be removed.
      </ConfirmModal>
    </PageSection>
  );
}
