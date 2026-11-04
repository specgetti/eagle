import { useState } from "react";
import {
  Card,
  CardBody,
  PageSection,
  Pagination,
  SearchInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { api } from "../../api";
import { Empty, LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { formatTime } from "../../utils";

export function AuditPage() {
  const [draft, setDraft] = useState("");
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);

  const audit = useAsync(
    () => api.listAudit({ action: action || null, limit: perPage, offset: (page - 1) * perPage }),
    [action, page, perPage],
  );
  const rows = audit.data?.items ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Audit log
      </Title>
      <Card>
        <CardBody>
          <Toolbar>
            <ToolbarContent>
              <ToolbarItem>
                <SearchInput
                  aria-label="Filter by action prefix"
                  placeholder="Action prefix, e.g. user. or review."
                  value={draft}
                  onChange={(_e, v) => setDraft(v)}
                  onSearch={(_e, v) => {
                    setAction(v.trim());
                    setPage(1);
                  }}
                  onClear={() => {
                    setDraft("");
                    setAction("");
                    setPage(1);
                  }}
                />
              </ToolbarItem>
              <ToolbarItem variant="pagination" align={{ default: "alignRight" }}>
                <Pagination
                  isCompact
                  itemCount={audit.data?.total ?? 0}
                  page={page}
                  perPage={perPage}
                  perPageOptions={[
                    { title: "25", value: 25 },
                    { title: "50", value: 50 },
                    { title: "100", value: 100 },
                  ]}
                  onSetPage={(_e, p) => setPage(p)}
                  onPerPageSelect={(_e, pp) => {
                    setPerPage(pp);
                    setPage(1);
                  }}
                />
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {audit.error && <LoadError message={audit.error} onRetry={() => void audit.reload()} />}
          {audit.loading && !audit.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title="No audit entries" />
          ) : (
            <Table aria-label="Audit log" variant="compact">
              <Thead>
                <Tr>
                  <Th>Time</Th>
                  <Th>Actor</Th>
                  <Th>Action</Th>
                  <Th>Target</Th>
                  <Th>Details</Th>
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((e) => (
                  <Tr key={e.id}>
                    <Td dataLabel="Time">{formatTime(e.created_at)}</Td>
                    <Td dataLabel="Actor">{e.actor_username}</Td>
                    <Td dataLabel="Action">
                      <code>{e.action}</code>
                    </Td>
                    <Td dataLabel="Target" modifier="breakWord">
                      {e.target ?? "-"}
                    </Td>
                    <Td dataLabel="Details" modifier="breakWord">
                      {e.details && Object.keys(e.details).length ? (
                        <code style={{ fontSize: "var(--pf-v5-global--FontSize--xs)" }}>
                          {JSON.stringify(e.details)}
                        </code>
                      ) : (
                        "-"
                      )}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          )}
        </CardBody>
      </Card>
    </PageSection>
  );
}
