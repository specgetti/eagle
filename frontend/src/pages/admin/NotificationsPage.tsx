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
  FormSelect,
  FormSelectOption,
  HelperText,
  HelperTextItem,
  Label,
  LabelGroup,
  Modal,
  ModalVariant,
  PageSection,
  Radio,
  Switch,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import {
  api,
  Channel,
  CHANNEL_EVENTS,
  ChannelEvent,
  ChannelKind,
  errorMessage,
} from "../../api";
import { ConfirmModal } from "../../components/ConfirmModal";
import { Empty, LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { splitList, stringValue } from "../../utils";

type SlackMode = "webhook" | "bot";
type Security = "starttls" | "ssl" | "none";

interface FormState {
  name: string;
  kind: ChannelKind;
  enabled: boolean;
  events: ChannelEvent[];
  slackMode: SlackMode;
  webhookUrl: string;
  botToken: string;
  channel: string;
  host: string;
  port: string;
  security: Security;
  username: string;
  password: string;
  fromAddr: string;
  toAddrs: string;
  notifySubmitter: boolean;
}

function initialState(ch: Channel | null): FormState {
  const pc = ch?.public_config ?? {};
  const secrets = ch?.secret_fields ?? {};
  return {
    name: ch?.name ?? "",
    kind: ch?.kind ?? "slack",
    enabled: ch?.enabled ?? true,
    events: (ch?.events as ChannelEvent[] | undefined) ?? [...CHANNEL_EVENTS],
    slackMode: secrets.bot_token && !secrets.webhook_url ? "bot" : "webhook",
    webhookUrl: "",
    botToken: "",
    channel: stringValue(pc.channel),
    host: stringValue(pc.host),
    port: pc.port === undefined || pc.port === null ? "" : String(pc.port),
    security: (pc.security as Security) || "starttls",
    username: stringValue(pc.username),
    password: "",
    fromAddr: stringValue(pc.from_addr),
    toAddrs: stringValue(pc.to_addrs),
    notifySubmitter: pc.notify_submitter === true,
  };
}

/** Build the `config` object. Blank secrets are left out so the stored value is kept. */
export function buildChannelConfig(s: FormState): Record<string, unknown> {
  if (s.kind === "slack") {
    if (s.slackMode === "webhook") {
      return s.webhookUrl.trim() ? { webhook_url: s.webhookUrl.trim() } : {};
    }
    const out: Record<string, unknown> = { channel: s.channel.trim() };
    if (s.botToken.trim()) out.bot_token = s.botToken.trim();
    return out;
  }
  const out: Record<string, unknown> = {
    host: s.host.trim(),
    security: s.security,
    username: s.username.trim(),
    from_addr: s.fromAddr.trim(),
    to_addrs: splitList(s.toAddrs),
    notify_submitter: s.notifySubmitter,
  };
  if (s.port.trim()) out.port = Number(s.port);
  if (s.password.trim()) out.password = s.password;
  return out;
}

function ChannelModal({
  isOpen,
  channel,
  onClose,
  onSaved,
}: {
  isOpen: boolean;
  channel: Channel | null;
  onClose: () => void;
  onSaved: (c: Channel) => void;
}) {
  const [s, setS] = useState<FormState>(initialState(null));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = !!channel;
  const secrets = channel?.secret_fields ?? {};

  useEffect(() => {
    if (isOpen) {
      setS(initialState(channel));
      setError(null);
    }
  }, [isOpen, channel]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setS((prev) => ({ ...prev, [key]: value }));

  const toggleEvent = (ev: ChannelEvent, on: boolean) =>
    set("events", on ? [...s.events.filter((e) => e !== ev), ev] : s.events.filter((e) => e !== ev));

  const keepHint = (field: string) =>
    editing && secrets[field] ? `Stored (${secrets[field]}). Leave blank to keep.` : undefined;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const config = buildChannelConfig(s);
      const saved = channel
        ? await api.updateChannel(channel.id, {
            name: s.name.trim(),
            enabled: s.enabled,
            events: s.events,
            config,
          })
        : await api.createChannel({
            name: s.name.trim(),
            kind: s.kind,
            enabled: s.enabled,
            events: s.events,
            config,
          });
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err, "Save failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      variant={ModalVariant.medium}
      title={editing ? `Edit ${channel?.name}` : "Add notification channel"}
      isOpen={isOpen}
      onClose={onClose}
      actions={[
        <Button
          key="save"
          type="submit"
          form="channel-form"
          isLoading={busy}
          isDisabled={busy || !s.name.trim()}
        >
          Save
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="channel-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="Name" isRequired fieldId="ch-name">
          <TextInput id="ch-name" isRequired value={s.name} onChange={(_e, v) => set("name", v)} />
        </FormGroup>
        <FormGroup label="Kind" fieldId="ch-kind">
          <FormSelect
            id="ch-kind"
            value={s.kind}
            isDisabled={editing}
            onChange={(_e, v) => set("kind", v as ChannelKind)}
          >
            <FormSelectOption value="slack" label="Slack" />
            <FormSelectOption value="smtp" label="Email (SMTP)" />
          </FormSelect>
        </FormGroup>
        <FormGroup fieldId="ch-enabled">
          <Switch
            id="ch-enabled"
            label="Enabled"
            labelOff="Disabled"
            isChecked={s.enabled}
            onChange={(_e, v) => set("enabled", v)}
          />
        </FormGroup>
        <FormGroup label="Events" role="group" fieldId="ch-events" isInline>
          {CHANNEL_EVENTS.map((ev) => (
            <Checkbox
              key={ev}
              id={`ch-event-${ev}`}
              label={ev}
              isChecked={s.events.includes(ev)}
              onChange={(_e, v) => toggleEvent(ev, v)}
            />
          ))}
        </FormGroup>

        {s.kind === "slack" ? (
          <>
            <FormGroup label="Delivery" role="radiogroup" fieldId="ch-slack-mode" isInline>
              <Radio
                id="ch-slack-webhook"
                name="slack-mode"
                label="Incoming webhook"
                isChecked={s.slackMode === "webhook"}
                onChange={() => set("slackMode", "webhook")}
              />
              <Radio
                id="ch-slack-bot"
                name="slack-mode"
                label="Bot token + channel"
                isChecked={s.slackMode === "bot"}
                onChange={() => set("slackMode", "bot")}
              />
            </FormGroup>
            {s.slackMode === "webhook" ? (
              <FormGroup
                label="Webhook URL"
                isRequired={!secrets.webhook_url}
                fieldId="ch-webhook"
              >
                <TextInput
                  id="ch-webhook"
                  type="password"
                  autoComplete="off"
                  value={s.webhookUrl}
                  placeholder={keepHint("webhook_url") ?? "https://hooks.slack.com/services/..."}
                  onChange={(_e, v) => set("webhookUrl", v)}
                />
              </FormGroup>
            ) : (
              <>
                <FormGroup label="Bot token" isRequired={!secrets.bot_token} fieldId="ch-bot-token">
                  <TextInput
                    id="ch-bot-token"
                    type="password"
                    autoComplete="off"
                    value={s.botToken}
                    placeholder={keepHint("bot_token") ?? "xoxb-..."}
                    onChange={(_e, v) => set("botToken", v)}
                  />
                </FormGroup>
                <FormGroup label="Channel" isRequired fieldId="ch-channel">
                  <TextInput
                    id="ch-channel"
                    value={s.channel}
                    placeholder="#pr-reviews or channel ID"
                    onChange={(_e, v) => set("channel", v)}
                  />
                </FormGroup>
              </>
            )}
          </>
        ) : (
          <>
            <FormGroup label="SMTP host" isRequired fieldId="ch-host">
              <TextInput id="ch-host" value={s.host} onChange={(_e, v) => set("host", v)} />
            </FormGroup>
            <FormGroup label="Security" fieldId="ch-security">
              <FormSelect
                id="ch-security"
                value={s.security}
                onChange={(_e, v) => set("security", v as Security)}
              >
                <FormSelectOption value="starttls" label="STARTTLS" />
                <FormSelectOption value="ssl" label="SSL/TLS" />
                <FormSelectOption value="none" label="None" />
              </FormSelect>
            </FormGroup>
            <FormGroup label="Port" fieldId="ch-port">
              <TextInput
                id="ch-port"
                type="number"
                value={s.port}
                placeholder={s.security === "ssl" ? "465" : "587"}
                onChange={(_e, v) => set("port", v)}
              />
            </FormGroup>
            <FormGroup label="Username" fieldId="ch-username">
              <TextInput
                id="ch-username"
                autoComplete="off"
                value={s.username}
                onChange={(_e, v) => set("username", v)}
              />
            </FormGroup>
            <FormGroup label="Password" fieldId="ch-password">
              <TextInput
                id="ch-password"
                type="password"
                autoComplete="new-password"
                value={s.password}
                placeholder={keepHint("password")}
                onChange={(_e, v) => set("password", v)}
              />
            </FormGroup>
            <FormGroup label="From address" isRequired fieldId="ch-from">
              <TextInput
                id="ch-from"
                type="email"
                value={s.fromAddr}
                onChange={(_e, v) => set("fromAddr", v)}
              />
            </FormGroup>
            <FormGroup label="To addresses" fieldId="ch-to">
              <TextInput
                id="ch-to"
                value={s.toAddrs}
                placeholder="team@example.com, lead@example.com"
                onChange={(_e, v) => set("toAddrs", v)}
              />
              <FormHelperText>
                <HelperText>
                  <HelperTextItem>Comma separated</HelperTextItem>
                </HelperText>
              </FormHelperText>
            </FormGroup>
            <Checkbox
              id="ch-notify-submitter"
              label="Also email the person who submitted the review"
              isChecked={s.notifySubmitter}
              onChange={(_e, v) => set("notifySubmitter", v)}
            />
          </>
        )}
      </Form>
    </Modal>
  );
}

function describe(ch: Channel): string {
  const pc = ch.public_config;
  if (ch.kind === "slack") {
    if (ch.secret_fields.webhook_url) return "incoming webhook";
    return `bot to ${stringValue(pc.channel) || "?"}`;
  }
  const to = stringValue(pc.to_addrs);
  return `${stringValue(pc.host)}:${stringValue(pc.port)}${to ? ` to ${to}` : ""}${
    pc.notify_submitter ? " (+ submitter)" : ""
  }`;
}

export function NotificationsPage() {
  const toast = useToast();
  const channels = useAsync(() => api.listChannels(), []);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Channel | null>(null);
  const [deleting, setDeleting] = useState<Channel | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const rows = channels.data ?? [];

  const sendTest = async (ch: Channel) => {
    setTesting(ch.id);
    try {
      await api.testChannel(ch.id);
      toast.success(`Test sent to ${ch.name}`);
    } catch (err) {
      toast.error(`Test to ${ch.name} failed`, errorMessage(err));
    } finally {
      setTesting(null);
    }
  };

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Notification channels
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
                  Add channel
                </Button>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {channels.error && (
            <LoadError message={channels.error} onRetry={() => void channels.reload()} />
          )}
          {channels.loading && !channels.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title="No channels" body="Announce finished reviews in Slack or by email." />
          ) : (
            <Table aria-label="Notification channels" variant="compact">
              <Thead>
                <Tr>
                  <Th>Name</Th>
                  <Th>Kind</Th>
                  <Th>Destination</Th>
                  <Th>Events</Th>
                  <Th>Status</Th>
                  <Th screenReaderText="Test" />
                  <Th screenReaderText="Actions" />
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((ch) => (
                  <Tr key={ch.id}>
                    <Td dataLabel="Name">{ch.name}</Td>
                    <Td dataLabel="Kind">{ch.kind === "smtp" ? "Email" : "Slack"}</Td>
                    <Td dataLabel="Destination">{describe(ch)}</Td>
                    <Td dataLabel="Events">
                      <LabelGroup>
                        {ch.events.map((e) => (
                          <Label key={e} isCompact color={e === "review.failed" ? "red" : "green"}>
                            {e}
                          </Label>
                        ))}
                      </LabelGroup>
                    </Td>
                    <Td dataLabel="Status">
                      {ch.enabled ? (
                        <Label isCompact color="green">
                          enabled
                        </Label>
                      ) : (
                        <Label isCompact color="grey">
                          disabled
                        </Label>
                      )}
                    </Td>
                    <Td>
                      <Button
                        variant="secondary"
                        size="sm"
                        isLoading={testing === ch.id}
                        isDisabled={testing === ch.id}
                        onClick={() => void sendTest(ch)}
                      >
                        Send test
                      </Button>
                    </Td>
                    <Td isActionCell>
                      <ActionsColumn
                        items={[
                          {
                            title: "Edit",
                            onClick: () => {
                              setEditing(ch);
                              setModalOpen(true);
                            },
                          },
                          { isSeparator: true },
                          { title: "Delete", onClick: () => setDeleting(ch) },
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
      <ChannelModal
        isOpen={modalOpen}
        channel={editing}
        onClose={() => setModalOpen(false)}
        onSaved={(saved) => {
          setModalOpen(false);
          const list = channels.data ?? [];
          channels.setData(
            list.some((x) => x.id === saved.id)
              ? list.map((x) => (x.id === saved.id ? saved : x))
              : [...list, saved],
          );
          toast.success(`Channel ${saved.name} saved`);
        }}
      />
      <ConfirmModal
        title="Delete channel?"
        isOpen={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return;
          await api.deleteChannel(deleting.id);
          channels.setData((channels.data ?? []).filter((x) => x.id !== deleting.id));
          toast.success(`Channel ${deleting.name} deleted`);
        }}
      >
        Channel <strong>{deleting?.name}</strong> will no longer receive notifications.
      </ConfirmModal>
    </PageSection>
  );
}
