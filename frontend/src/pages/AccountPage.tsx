import { FormEvent, useState } from "react";
import {
  ActionGroup,
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Form,
  FormGroup,
  FormHelperText,
  HelperText,
  HelperTextItem,
  PageSection,
  TextInput,
  Title,
} from "@patternfly/react-core";
import { api, errorMessage, isSsoUser } from "../api";
import { useUser } from "../auth";
import { useToast } from "../toast";

export const MIN_PASSWORD = 12;

export function AccountPage() {
  const user = useUser();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD;
  const mismatch = confirm.length > 0 && confirm !== next;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success("Password changed");
    } catch (err) {
      setError(errorMessage(err, "Password change failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 16 }}>
        Account
      </Title>
      <Card style={{ marginBottom: 16, maxWidth: 640 }}>
        <CardBody>
          <DescriptionList isHorizontal isCompact>
            <DescriptionListGroup>
              <DescriptionListTerm>Username</DescriptionListTerm>
              <DescriptionListDescription>{user.username}</DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Email</DescriptionListTerm>
              <DescriptionListDescription>{user.email ?? "-"}</DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Role</DescriptionListTerm>
              <DescriptionListDescription>{user.role}</DescriptionListDescription>
            </DescriptionListGroup>
            <DescriptionListGroup>
              <DescriptionListTerm>Sign-in</DescriptionListTerm>
              <DescriptionListDescription>
                {isSsoUser(user) ? "Single sign-on (SSO)" : "Local password"}
              </DescriptionListDescription>
            </DescriptionListGroup>
          </DescriptionList>
        </CardBody>
      </Card>
      {isSsoUser(user) ? (
        <Alert
          variant="info"
          isInline
          title="Your account signs in through SSO"
          style={{ maxWidth: 640 }}
        >
          Your password and role are managed by your identity provider.
        </Alert>
      ) : (
      <Card style={{ maxWidth: 640 }}>
        <CardTitle>Change password</CardTitle>
        <CardBody>
          <Form onSubmit={submit}>
            {error && <Alert variant="danger" isInline title={error} />}
            <FormGroup label="Current password" isRequired fieldId="pw-current">
              <TextInput
                id="pw-current"
                type="password"
                autoComplete="current-password"
                isRequired
                value={current}
                onChange={(_e, v) => setCurrent(v)}
              />
            </FormGroup>
            <FormGroup label="New password" isRequired fieldId="pw-new">
              <TextInput
                id="pw-new"
                type="password"
                autoComplete="new-password"
                isRequired
                value={next}
                validated={tooShort ? "error" : "default"}
                onChange={(_e, v) => setNext(v)}
              />
              <FormHelperText>
                <HelperText>
                  <HelperTextItem variant={tooShort ? "error" : "default"}>
                    At least {MIN_PASSWORD} characters
                  </HelperTextItem>
                </HelperText>
              </FormHelperText>
            </FormGroup>
            <FormGroup label="Confirm new password" isRequired fieldId="pw-confirm">
              <TextInput
                id="pw-confirm"
                type="password"
                autoComplete="new-password"
                isRequired
                value={confirm}
                validated={mismatch ? "error" : "default"}
                onChange={(_e, v) => setConfirm(v)}
              />
              {mismatch && (
                <FormHelperText>
                  <HelperText>
                    <HelperTextItem variant="error">Passwords do not match</HelperTextItem>
                  </HelperText>
                </FormHelperText>
              )}
            </FormGroup>
            <ActionGroup>
              <Button
                type="submit"
                isLoading={busy}
                isDisabled={
                  busy || !current || next.length < MIN_PASSWORD || next !== confirm
                }
              >
                Change password
              </Button>
            </ActionGroup>
          </Form>
        </CardBody>
      </Card>
      )}
    </PageSection>
  );
}
