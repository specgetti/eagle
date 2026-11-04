import { useState } from "react";
import {
  Button,
  Card,
  CardBody,
  Label,
  LabelGroup,
  PageSection,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { api, Credential } from "../../api";
import { ConfirmModal } from "../../components/ConfirmModal";
import { CredentialFormModal } from "../../components/CredentialFormModal";
import { TestCredentialModal } from "../../components/TestCredentialModal";
import { Empty, LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { AGENT_LABELS, CREDENTIAL_KIND_LABELS, formatTime, stringValue } from "../../utils";

function KeyValues({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (!entries.length) return <>-</>;
  return (
    <div>
      {entries.map(([k, v]) => (
        <div key={k}>
          <span style={{ color: "var(--pf-v5-global--Color--200)" }}>{k}:</span>{" "}
          <code>{stringValue(v)}</code>
        </div>
      ))}
    </div>
  );
}

export function CredentialsPage() {
  const toast = useToast();
  const credentials = useAsync(() => api.listCredentials(), []);
  const types = useAsync(() => api.credentialTypes(), []);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Credential | null>(null);
  const [deleting, setDeleting] = useState<Credential | null>(null);
  const [testing, setTesting] = useState<Credential | null>(null);
  // Only to prefill the test model with an assigned agent's default model.
  const agents = useAsync(() => api.listAgents(), []);

  const typeByKind = new Map((types.data ?? []).map((t) => [t.kind, t]));
  const rows = credentials.data ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Inference credentials
      </Title>
      <Card>
        <CardBody>
          <Toolbar>
            <ToolbarContent>
              <ToolbarItem>
                <Button
                  isDisabled={!types.data}
                  onClick={() => {
                    setEditing(null);
                    setModalOpen(true);
                  }}
                >
                  Add credential
                </Button>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {credentials.error && (
            <LoadError message={credentials.error} onRetry={() => void credentials.reload()} />
          )}
          {types.error && <LoadError message={types.error} onRetry={() => void types.reload()} />}
          {credentials.loading && !credentials.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty
              title="No credentials"
              body="Add an API key, Vertex AI service account or MaaS endpoint, then assign it to an agent."
            />
          ) : (
            <Table aria-label="Credentials" variant="compact">
              <Thead>
                <Tr>
                  <Th>Name</Th>
                  <Th>Kind</Th>
                  <Th>Configuration</Th>
                  <Th>Secrets</Th>
                  <Th>Used by</Th>
                  <Th>Updated</Th>
                  <Th screenReaderText="Actions" />
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((c) => (
                  <Tr key={c.id}>
                    <Td dataLabel="Name">{c.name}</Td>
                    <Td dataLabel="Kind">
                      {CREDENTIAL_KIND_LABELS[c.kind] ?? c.kind}{" "}
                      {typeByKind.get(c.kind)?.subscription && (
                        <Label color="orange" isCompact>
                          subscription
                        </Label>
                      )}
                    </Td>
                    <Td dataLabel="Configuration">
                      <KeyValues values={c.public_config} />
                    </Td>
                    <Td dataLabel="Secrets">
                      <KeyValues values={c.secret_fields} />
                    </Td>
                    <Td dataLabel="Used by">
                      {c.used_by.length ? (
                        <LabelGroup>
                          {c.used_by.map((a) => (
                            <Label key={a} isCompact color="blue">
                              {AGENT_LABELS[a] ?? a}
                            </Label>
                          ))}
                        </LabelGroup>
                      ) : (
                        "-"
                      )}
                    </Td>
                    <Td dataLabel="Updated">{formatTime(c.updated_at)}</Td>
                    <Td isActionCell>
                      <ActionsColumn
                        items={[
                          { title: "Test", onClick: () => setTesting(c) },
                          {
                            title: "Edit",
                            isDisabled: !types.data,
                            onClick: () => {
                              setEditing(c);
                              setModalOpen(true);
                            },
                          },
                          { isSeparator: true },
                          { title: "Delete", onClick: () => setDeleting(c) },
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

      <CredentialFormModal
        isOpen={modalOpen}
        types={types.data ?? []}
        credential={editing}
        onClose={() => setModalOpen(false)}
        onSaved={(saved) => {
          setModalOpen(false);
          const list = credentials.data ?? [];
          credentials.setData(
            list.some((x) => x.id === saved.id)
              ? list.map((x) => (x.id === saved.id ? saved : x))
              : [...list, saved],
          );
          toast.success(`Credential ${saved.name} saved`);
        }}
      />
      <TestCredentialModal
        credential={testing}
        suggestedModel={
          (agents.data ?? []).find((a) => a.credential_id === testing?.id && a.default_model)
            ?.default_model
        }
        onClose={() => setTesting(null)}
      />
      <ConfirmModal
        title="Delete credential?"
        isOpen={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return;
          await api.deleteCredential(deleting.id);
          credentials.setData((credentials.data ?? []).filter((x) => x.id !== deleting.id));
          toast.success(`Credential ${deleting.name} deleted`);
        }}
      >
        Credential <strong>{deleting?.name}</strong> will be permanently deleted.
        {deleting && deleting.used_by.length > 0 && (
          <> It is currently used by {deleting.used_by.join(", ")}; reassign those agents first.</>
        )}
      </ConfirmModal>
    </PageSection>
  );
}
