import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, CardBody, PageSection, Title } from "@patternfly/react-core";
import { ApiTokenTable, CreateApiTokenModal } from "../../components/ApiTokens";

/** Every user's API tokens. Anyone creates their own here or on the Account page. */
export function ApiTokensPage() {
  const [creating, setCreating] = useState(false);
  const [version, setVersion] = useState(0);

  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 8 }}>
        API tokens
      </Title>
      <p style={{ marginBottom: 16 }}>
        Every user&apos;s tokens. A token acts as its user with that user&apos;s current role.
        Revoke one here if it leaked or its owner left; deactivating a user also stops their
        tokens. Users create their own on their <Link to="/account">Account</Link> page.
      </p>
      <Button onClick={() => setCreating(true)} style={{ marginBottom: 16 }}>
        Create token for yourself
      </Button>
      <Card>
        <CardBody>
          <ApiTokenTable all reloadKey={version} />
        </CardBody>
      </Card>
      <CreateApiTokenModal
        isOpen={creating}
        onClose={() => setCreating(false)}
        onCreated={() => setVersion((v) => v + 1)}
      />
    </PageSection>
  );
}
