import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  CardBody,
  Label,
  PageSection,
  Switch,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { api, errorMessage, PlatformSettings, TrackedPR } from "../api";
import { can, useUser } from "../auth";
import { Empty, LoadError, Loading } from "../components/States";
import { useAsync } from "../hooks";
import { useToast } from "../toast";
import { formatTime, shortSha } from "../utils";

function IntervalEditor({
  row,
  disabled,
  onSave,
}: {
  row: TrackedPR;
  disabled: boolean;
  onSave: (minutes: number | null) => Promise<void>;
}) {
  const initial = row.poll_interval_minutes === null ? "" : String(row.poll_interval_minutes);
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const dirty = value.trim() !== initial;
  const parsed = value.trim() === "" ? null : Number(value);
  const valid = parsed === null || (Number.isInteger(parsed) && parsed >= 1 && parsed <= 1440);

  const save = async () => {
    setBusy(true);
    try {
      await onSave(parsed);
    } finally {
      setBusy(false);
    }
  };

  if (disabled) return <>{initial ? `${initial} min` : "platform default"}</>;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <TextInput
        aria-label={`Poll interval for ${row.repo}#${row.pr_number}`}
        type="number"
        min={1}
        max={1440}
        value={value}
        placeholder="default"
        validated={valid ? "default" : "error"}
        onChange={(_e, v) => setValue(v)}
        style={{ width: 110 }}
      />
      {dirty && (
        <Button variant="secondary" size="sm" onClick={save} isDisabled={!valid || busy} isLoading={busy}>
          Save
        </Button>
      )}
    </div>
  );
}

export function TrackedPage() {
  const user = useUser();
  const toast = useToast();
  const [includeClosed, setIncludeClosed] = useState(false);
  const tracked = useAsync(() => api.listTracked(includeClosed), [includeClosed]);
  const settings = useAsync<PlatformSettings | null>(
    () => (user.role === "admin" ? api.getSettings() : Promise.resolve(null)),
    [user.role],
  );
  const editable = can.submit(user.role);

  const patch = async (row: TrackedPR, body: Parameters<typeof api.updateTracked>[1]) => {
    try {
      const updated = await api.updateTracked(row.id, body);
      tracked.setData((tracked.data ?? []).map((t) => (t.id === row.id ? updated : t)));
      toast.success(`Updated ${row.repo}#${row.pr_number}`);
    } catch (err) {
      toast.error("Update failed", errorMessage(err));
    }
  };

  const rows = tracked.data ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Tracked pull requests
      </Title>
      {settings.data && !settings.data.poller_enabled && (
        <Alert variant="info" isInline title="The PR poller is disabled" style={{ marginBottom: 16 }}>
          Tracked PRs are not re-reviewed automatically until an admin turns on polling under{" "}
          <Link to="/admin/settings">Admin &gt; Settings</Link>.
        </Alert>
      )}
      {settings.data?.poller_enabled && (
        <Alert variant="info" isInline isPlain title={`Poller on, default interval ${settings.data.poll_interval_minutes} minutes`} style={{ marginBottom: 16 }} />
      )}
      <Card>
        <CardBody>
          <Toolbar>
            <ToolbarContent>
              <ToolbarItem alignSelf="center">
                <Switch
                  id="tracked-include-closed"
                  label="Include closed PRs"
                  isChecked={includeClosed}
                  onChange={(_e, v) => setIncludeClosed(v)}
                />
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {tracked.error && <LoadError message={tracked.error} onRetry={() => void tracked.reload()} />}
          {tracked.loading && !tracked.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty
              title="No tracked PRs"
              body="A pull request is tracked once it has been reviewed."
            />
          ) : (
            <Table aria-label="Tracked PRs" variant="compact">
              <Thead>
                <Tr>
                  <Th>Pull request</Th>
                  <Th>Polling</Th>
                  <Th>Interval (minutes)</Th>
                  <Th>Last reviewed SHA</Th>
                  <Th>Latest review</Th>
                  <Th>Last polled</Th>
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((row) => (
                  <Tr key={row.id}>
                    <Td dataLabel="Pull request">
                      <a href={row.pr_url} target="_blank" rel="noreferrer noopener">
                        {row.repo}#{row.pr_number}
                      </a>{" "}
                      {row.closed && (
                        <Label isCompact color="grey">
                          closed
                        </Label>
                      )}
                    </Td>
                    <Td dataLabel="Polling">
                      <Switch
                        id={`tracked-enabled-${row.id}`}
                        aria-label={`Polling for ${row.repo}#${row.pr_number}`}
                        isChecked={row.enabled}
                        isDisabled={!editable}
                        onChange={(_e, v) => void patch(row, { enabled: v })}
                      />
                    </Td>
                    <Td dataLabel="Interval">
                      <IntervalEditor
                        key={`${row.id}-${row.poll_interval_minutes}`}
                        row={row}
                        disabled={!editable}
                        onSave={(m) => patch(row, { poll_interval_minutes: m })}
                      />
                    </Td>
                    <Td dataLabel="Last reviewed SHA">
                      <code>{shortSha(row.last_reviewed_sha)}</code>
                    </Td>
                    <Td dataLabel="Latest review">
                      {row.latest_review_id ? (
                        <Link to={`/reviews/${row.latest_review_id}`}>#{row.latest_review_id}</Link>
                      ) : (
                        "-"
                      )}
                    </Td>
                    <Td dataLabel="Last polled">{formatTime(row.last_polled_at)}</Td>
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
