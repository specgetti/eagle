import { Card, CardBody, PageSection, Title } from "@patternfly/react-core";
import { ApiTokenTable } from "../../components/ApiTokens";

/** Every user's API tokens. Users create their own on the Account page. */
export function ApiTokensPage() {
  return (
    <PageSection>
      <Title headingLevel="h1" size="xl" style={{ marginBottom: 8 }}>
        API tokens
      </Title>
      <p style={{ marginBottom: 16 }}>
        Tokens act as their user with that user's current role. Users create them on their
        Account page; revoke one here if it leaked or its owner left. Deactivating a user also
        stops their tokens.
      </p>
      <Card>
        <CardBody>
          <ApiTokenTable all />
        </CardBody>
      </Card>
    </PageSection>
  );
}
