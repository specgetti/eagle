import { Label, Spinner } from "@patternfly/react-core";
import { ReviewStatus, ReviewTrigger } from "../api";

const STATUS_COLOR: Record<ReviewStatus, "grey" | "blue" | "green" | "red" | "orange"> = {
  queued: "grey",
  running: "blue",
  succeeded: "green",
  failed: "red",
  cancelled: "orange",
};

export function StatusLabel({ status }: { status: ReviewStatus }) {
  return (
    <Label
      color={STATUS_COLOR[status] ?? "grey"}
      icon={status === "running" ? <Spinner size="sm" aria-label="Running" /> : undefined}
    >
      {status}
    </Label>
  );
}

const VERDICT_COLOR: Record<string, "green" | "blue" | "red"> = {
  approve: "green",
  comment: "blue",
  request_changes: "red",
};

export function VerdictLabel({ verdict }: { verdict: string | null | undefined }) {
  if (!verdict) return <>-</>;
  return <Label color={VERDICT_COLOR[verdict] ?? "grey"}>{verdict.replace(/_/g, " ")}</Label>;
}

export function TriggerLabel({ trigger }: { trigger: ReviewTrigger }) {
  return (
    <Label variant="outline" color={trigger === "poll" ? "purple" : "grey"}>
      {trigger}
    </Label>
  );
}
