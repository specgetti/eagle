import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Breadcrumb,
  BreadcrumbItem,
  Button,
  Card,
  CardBody,
  CardTitle,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Flex,
  FlexItem,
  List,
  ListItem,
  PageSection,
  Title,
} from "@patternfly/react-core";
import ExternalLinkAltIcon from "@patternfly/react-icons/dist/esm/icons/external-link-alt-icon";
import { api, ApiError, errorMessage } from "../api";
import { can, useUser } from "../auth";
import { ConfirmModal } from "../components/ConfirmModal";
import { StatusLabel, TriggerLabel, VerdictLabel } from "../components/Labels";
import { Markdown } from "../components/Markdown";
import { Empty, LoadError, Loading } from "../components/States";
import { useAsync, useInterval } from "../hooks";
import { useToast } from "../toast";
import { AGENT_LABELS, formatCost, formatTime, isActive, shortSha } from "../utils";

export function ReviewDetailPage() {
  const { id } = useParams();
  const reviewId = Number(id);
  const user = useUser();
  const toast = useToast();
  const navigate = useNavigate();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [rerunning, setRerunning] = useState(false);

  const review = useAsync(() => api.getReview(reviewId), [reviewId]);
  const r = review.data;
  const active = !!r && isActive(r.status);
  useInterval(() => void review.reload(true), 3000, active);

  if (!Number.isInteger(reviewId)) {
    return (
      <PageSection>
        <Empty title="Review not found" />
      </PageSection>
    );
  }
  if (review.loading && !r) return <Loading />;
  if (!r) {
    return (
      <PageSection>
        <LoadError message={review.error ?? "Not found"} onRetry={() => void review.reload()} />
      </PageSection>
    );
  }

  const isSubmitter = r.submitted_by_id !== null && r.submitted_by_id === user.id;
  const canCancel = active && (user.role === "admin" || (can.submit(user.role) && isSubmitter));
  const canRerun = !active && can.submit(user.role);

  const rerun = async () => {
    setRerunning(true);
    try {
      const next = await api.rerunReview(r.id);
      toast.success(`Re-run queued as review #${next.id}`);
      navigate(`/reviews/${next.id}`);
    } catch (err) {
      toast.error("Re-run failed", errorMessage(err));
    } finally {
      setRerunning(false);
    }
  };

  const cancel = async () => {
    try {
      review.setData(await api.cancelReview(r.id));
      toast.success(`Review #${r.id} cancelled`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) void review.reload(true);
      throw err;
    }
  };

  const field = (term: string, value: React.ReactNode) => (
    <DescriptionListGroup>
      <DescriptionListTerm>{term}</DescriptionListTerm>
      <DescriptionListDescription>{value}</DescriptionListDescription>
    </DescriptionListGroup>
  );

  return (
    <>
      <PageSection variant="light">
        <Breadcrumb style={{ marginBottom: 12 }}>
          <BreadcrumbItem>
            <Link to="/">Reviews</Link>
          </BreadcrumbItem>
          <BreadcrumbItem isActive>Review #{r.id}</BreadcrumbItem>
        </Breadcrumb>
        <Flex
          justifyContent={{ default: "justifyContentSpaceBetween" }}
          alignItems={{ default: "alignItemsFlexStart" }}
        >
          <FlexItem>
            <Title headingLevel="h1" size="2xl">
              {r.title ?? `${r.repo}#${r.pr_number}`}
            </Title>
            <a href={r.pr_url} target="_blank" rel="noreferrer noopener">
              {r.repo}#{r.pr_number} <ExternalLinkAltIcon />
            </a>
            <Flex spaceItems={{ default: "spaceItemsSm" }} style={{ marginTop: 8 }}>
              <StatusLabel status={r.status} />
              <VerdictLabel verdict={r.verdict} />
              <TriggerLabel trigger={r.trigger} />
            </Flex>
          </FlexItem>
          <FlexItem>
            <Flex spaceItems={{ default: "spaceItemsSm" }}>
              {canCancel && (
                <Button variant="danger" onClick={() => setConfirmCancel(true)}>
                  Cancel review
                </Button>
              )}
              {canRerun && (
                <Button variant="secondary" onClick={rerun} isLoading={rerunning} isDisabled={rerunning}>
                  Re-run
                </Button>
              )}
            </Flex>
          </FlexItem>
        </Flex>
      </PageSection>

      <PageSection>
        {review.error && (
          <Alert
            variant="warning"
            isInline
            title={`Refresh failed: ${review.error}`}
            style={{ marginBottom: 16 }}
          />
        )}
        <Card style={{ marginBottom: 16 }}>
          <CardBody>
            <DescriptionList
              columnModifier={{ default: "1Col", md: "2Col", xl: "3Col" }}
              isCompact
            >
              {field("Author", r.author ?? "-")}
              {field("Head SHA", <code>{shortSha(r.head_sha)}</code>)}
              {field("Base", r.base_ref ?? "-")}
              {field(
                "Agent / model",
                `${AGENT_LABELS[r.agent] ?? r.agent} / ${r.model ?? "default model"}`,
              )}
              {field("Cost", formatCost(r.cost_usd))}
              {field("Submitted by", r.submitted_by ?? (r.trigger === "poll" ? "poller" : "-"))}
              {field("Submitted", formatTime(r.created_at))}
              {field("Started", formatTime(r.started_at))}
              {field("Finished", formatTime(r.finished_at))}
              {field("Post as PR comment", r.post_comment ? "yes" : "no")}
              {r.since_sha && field("Incremental since", <code>{shortSha(r.since_sha)}</code>)}
              {r.previous_review_id !== null &&
                field(
                  "Previous review",
                  <Link to={`/reviews/${r.previous_review_id}`}>#{r.previous_review_id}</Link>,
                )}
              {r.posted_comment_url &&
                field(
                  "PR comment",
                  <a href={r.posted_comment_url} target="_blank" rel="noreferrer noopener">
                    View comment <ExternalLinkAltIcon />
                  </a>,
                )}
            </DescriptionList>
          </CardBody>
        </Card>

        {r.error && (
          <Alert variant="danger" isInline title="Review failed" style={{ marginBottom: 16 }}>
            <pre style={{ whiteSpace: "pre-wrap" }}>{r.error}</pre>
          </Alert>
        )}
        {r.delivery_errors && r.delivery_errors.length > 0 && (
          <Alert variant="warning" isInline title="Delivery problems" style={{ marginBottom: 16 }}>
            <List>
              {r.delivery_errors.map((e, i) => (
                <ListItem key={i}>{e}</ListItem>
              ))}
            </List>
          </Alert>
        )}

        {r.custom_prompt && (
          <Card style={{ marginBottom: 16 }} isCompact>
            <CardTitle>Additional context from the submitter</CardTitle>
            <CardBody>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{r.custom_prompt}</pre>
            </CardBody>
          </Card>
        )}

        <Card>
          <CardTitle>Review</CardTitle>
          <CardBody>
            {r.result_markdown ? (
              <Markdown>{r.result_markdown}</Markdown>
            ) : active ? (
              <Loading label="Review in progress" />
            ) : (
              <Empty title="No review output" />
            )}
          </CardBody>
        </Card>
      </PageSection>

      <ConfirmModal
        title="Cancel this review?"
        isOpen={confirmCancel}
        confirmLabel="Cancel review"
        onConfirm={cancel}
        onClose={() => setConfirmCancel(false)}
      >
        The agent run for {r.repo}#{r.pr_number} will be stopped.
      </ConfirmModal>
    </>
  );
}
