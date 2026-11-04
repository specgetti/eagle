import { ReactNode } from "react";
import {
  Alert,
  Bullseye,
  Button,
  EmptyState,
  EmptyStateActions,
  EmptyStateBody,
  EmptyStateFooter,
  EmptyStateHeader,
  EmptyStateIcon,
  Spinner,
} from "@patternfly/react-core";
import CubesIcon from "@patternfly/react-icons/dist/esm/icons/cubes-icon";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <Bullseye style={{ padding: "2rem" }}>
      <Spinner aria-label={label} />
    </Bullseye>
  );
}

export function Empty({
  title,
  body,
  action,
}: {
  title: string;
  body?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <EmptyState variant="sm">
      <EmptyStateHeader
        titleText={title}
        headingLevel="h3"
        icon={<EmptyStateIcon icon={CubesIcon} />}
      />
      {body && <EmptyStateBody>{body}</EmptyStateBody>}
      {action && (
        <EmptyStateFooter>
          <EmptyStateActions>{action}</EmptyStateActions>
        </EmptyStateFooter>
      )}
    </EmptyState>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert
      variant="danger"
      isInline
      title="Could not load data"
      actionLinks={
        onRetry ? (
          <Button variant="link" isInline onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    >
      {message}
    </Alert>
  );
}
