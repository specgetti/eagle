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
import { api, errorMessage, isSsoUser, Role, ROLES, User } from "../../api";
import { useUser } from "../../auth";
import { ConfirmModal } from "../../components/ConfirmModal";
import { Empty, LoadError, Loading } from "../../components/States";
import { useAsync } from "../../hooks";
import { useToast } from "../../toast";
import { formatTime } from "../../utils";
import { MIN_PASSWORD } from "../AccountPage";

export const SSO_ROLE_NOTE =
  "This user signs in through SSO. Their role is re-synced from identity provider groups on every login, so changes made here may be overwritten.";

function PasswordHelp({ value }: { value: string }) {
  const short = value.length > 0 && value.length < MIN_PASSWORD;
  return (
    <FormHelperText>
      <HelperText>
        <HelperTextItem variant={short ? "error" : "default"}>
          At least {MIN_PASSWORD} characters
        </HelperTextItem>
      </HelperText>
    </FormHelperText>
  );
}

function CreateUserModal({
  isOpen,
  onClose,
  onSaved,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSaved: (u: User) => void;
}) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setUsername("");
      setEmail("");
      setPassword("");
      setRole("member");
      setError(null);
    }
  }, [isOpen]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(
        await api.createUser({
          username: username.trim(),
          email: email.trim() || null,
          password,
          role,
        }),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const valid = username.trim().length >= 3 && password.length >= MIN_PASSWORD;

  return (
    <Modal
      variant={ModalVariant.small}
      title="Create user"
      isOpen={isOpen}
      onClose={onClose}
      actions={[
        <Button key="save" type="submit" form="create-user-form" isDisabled={!valid || busy} isLoading={busy}>
          Create
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="create-user-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="Username" isRequired fieldId="new-username">
          <TextInput id="new-username" isRequired value={username} onChange={(_e, v) => setUsername(v)} />
        </FormGroup>
        <FormGroup label="Email" fieldId="new-email">
          <TextInput id="new-email" type="email" value={email} onChange={(_e, v) => setEmail(v)} />
        </FormGroup>
        <FormGroup label="Password" isRequired fieldId="new-password">
          <TextInput
            id="new-password"
            type="password"
            autoComplete="new-password"
            isRequired
            value={password}
            onChange={(_e, v) => setPassword(v)}
          />
          <PasswordHelp value={password} />
        </FormGroup>
        <FormGroup label="Role" isRequired fieldId="new-role">
          <FormSelect id="new-role" value={role} onChange={(_e, v) => setRole(v as Role)}>
            {ROLES.map((r) => (
              <FormSelectOption key={r} value={r} label={r} />
            ))}
          </FormSelect>
        </FormGroup>
      </Form>
    </Modal>
  );
}

function EditUserModal({
  user,
  onClose,
  onSaved,
}: {
  user: User | null;
  onClose: () => void;
  onSaved: (u: User) => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (user) {
      setEmail(user.email ?? "");
      setRole(user.role);
      setActive(user.is_active);
      setError(null);
    }
  }, [user]);

  if (!user) return null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSaved(
        await api.updateUser(user.id, {
          email: email.trim() || null,
          role,
          is_active: active,
        }),
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      variant={ModalVariant.small}
      title={`Edit ${user.username}`}
      isOpen
      onClose={onClose}
      actions={[
        <Button key="save" type="submit" form="edit-user-form" isDisabled={busy} isLoading={busy}>
          Save
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="edit-user-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="Email" fieldId="edit-email">
          <TextInput id="edit-email" type="email" value={email} onChange={(_e, v) => setEmail(v)} />
        </FormGroup>
        <FormGroup label="Role" fieldId="edit-role">
          <FormSelect id="edit-role" value={role} onChange={(_e, v) => setRole(v as Role)}>
            {ROLES.map((r) => (
              <FormSelectOption key={r} value={r} label={r} />
            ))}
          </FormSelect>
          {isSsoUser(user) && (
            <FormHelperText>
              <HelperText>
                <HelperTextItem variant="warning">{SSO_ROLE_NOTE}</HelperTextItem>
              </HelperText>
            </FormHelperText>
          )}
        </FormGroup>
        <FormGroup fieldId="edit-active">
          <Checkbox
            id="edit-active"
            label="Active (can sign in)"
            isChecked={active}
            onChange={(_e, v) => setActive(v)}
          />
        </FormGroup>
      </Form>
    </Modal>
  );
}

function ResetPasswordModal({ user, onClose }: { user: User | null; onClose: () => void }) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPassword("");
    setError(null);
  }, [user]);

  if (!user) return null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.updateUser(user.id, { password });
      toast.success(`Password reset for ${user.username}`);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      variant={ModalVariant.small}
      title={`Reset password for ${user.username}`}
      isOpen
      onClose={onClose}
      actions={[
        <Button
          key="save"
          type="submit"
          form="reset-pw-form"
          isDisabled={busy || password.length < MIN_PASSWORD}
          isLoading={busy}
        >
          Reset password
        </Button>,
        <Button key="cancel" variant="link" onClick={onClose}>
          Cancel
        </Button>,
      ]}
    >
      <Form id="reset-pw-form" onSubmit={submit}>
        {error && <Alert variant="danger" isInline title={error} />}
        <FormGroup label="New password" isRequired fieldId="reset-password">
          <TextInput
            id="reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(_e, v) => setPassword(v)}
          />
          <PasswordHelp value={password} />
        </FormGroup>
      </Form>
    </Modal>
  );
}

export function UsersPage() {
  const me = useUser();
  const toast = useToast();
  const users = useAsync(() => api.listUsers(), []);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);

  const replace = (u: User) =>
    users.setData((users.data ?? []).map((x) => (x.id === u.id ? u : x)));

  const rows = users.data ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Users
      </Title>
      <Card>
        <CardBody>
          <Toolbar>
            <ToolbarContent>
              <ToolbarItem>
                <Button onClick={() => setCreating(true)}>Create user</Button>
              </ToolbarItem>
            </ToolbarContent>
          </Toolbar>
          {users.error && <LoadError message={users.error} onRetry={() => void users.reload()} />}
          {users.loading && !users.data ? (
            <Loading />
          ) : rows.length === 0 ? (
            <Empty title="No users" />
          ) : (
            <Table aria-label="Users" variant="compact">
              <Thead>
                <Tr>
                  <Th>Username</Th>
                  <Th>Email</Th>
                  <Th>Role</Th>
                  <Th>Status</Th>
                  <Th>Created</Th>
                  <Th screenReaderText="Actions" />
                </Tr>
              </Thead>
              <Tbody>
                {rows.map((u) => (
                  <Tr key={u.id}>
                    <Td dataLabel="Username">
                      {u.username}{" "}
                      {isSsoUser(u) && (
                        <Label isCompact color="cyan">
                          SSO
                        </Label>
                      )}{" "}
                      {u.id === me.id && (
                        <Label isCompact variant="outline">
                          you
                        </Label>
                      )}
                    </Td>
                    <Td dataLabel="Email">{u.email ?? "-"}</Td>
                    <Td dataLabel="Role">{u.role}</Td>
                    <Td dataLabel="Status">
                      {u.is_active ? (
                        <Label color="green" isCompact>
                          active
                        </Label>
                      ) : (
                        <Label color="grey" isCompact>
                          disabled
                        </Label>
                      )}
                    </Td>
                    <Td dataLabel="Created">{formatTime(u.created_at)}</Td>
                    <Td isActionCell>
                      <ActionsColumn
                        items={[
                          { title: "Edit", onClick: () => setEditing(u) },
                          ...(isSsoUser(u)
                            ? []
                            : [{ title: "Reset password", onClick: () => setResetting(u) }]),
                          { isSeparator: true },
                          {
                            title: "Delete",
                            onClick: () => setDeleting(u),
                            isDisabled: u.id === me.id,
                          },
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

      <CreateUserModal
        isOpen={creating}
        onClose={() => setCreating(false)}
        onSaved={(u) => {
          setCreating(false);
          users.setData([...(users.data ?? []), u]);
          toast.success(`User ${u.username} created`);
        }}
      />
      <EditUserModal
        user={editing}
        onClose={() => setEditing(null)}
        onSaved={(u) => {
          setEditing(null);
          replace(u);
          toast.success(`User ${u.username} updated`);
        }}
      />
      <ResetPasswordModal user={resetting} onClose={() => setResetting(null)} />
      <ConfirmModal
        title="Delete user?"
        isOpen={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting) return;
          await api.deleteUser(deleting.id);
          users.setData((users.data ?? []).filter((x) => x.id !== deleting.id));
          toast.success(`User ${deleting.username} deleted`);
        }}
      >
        User <strong>{deleting?.username}</strong> will be removed. This cannot be undone.
      </ConfirmModal>
    </PageSection>
  );
}
