import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Button,
  Card,
  CardBody,
  FormSelect,
  FormSelectOption,
  Pagination,
  PageSection,
  SearchInput,
  Switch,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import SyncIcon from "@patternfly/react-icons/dist/esm/icons/sync-alt-icon";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { api, REVIEW_STATUSES, ReviewStatus } from "../api";
import { can, useUser } from "../auth";
import { StatusLabel, TriggerLabel, VerdictLabel } from "../components/Labels";
import { Empty, LoadError, Loading } from "../components/States";
import { SubmitReviewForm } from "../components/SubmitReviewForm";
import { useAsync, useInterval } from "../hooks";
import { useToast } from "../toast";
import { AGENT_LABELS, formatTime, isActive, modelText } from "../utils";

export function ReviewsPage() {
  const user = useUser();
  const toast = useToast();
  const navigate = useNavigate();
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [repoDraft, setRepoDraft] = useState("");
  const [repo, setRepo] = useState("");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(20);

  const reviews = useAsync(
    () =>
      api.listReviews({
        status: status || null,
        repo: repo || null,
        mine,
        limit: perPage,
        offset: (page - 1) * perPage,
      }),
    [status, repo, mine, page, perPage],
  );

  const items = reviews.data?.items ?? [];
  const anyActive = items.some((r) => isActive(r.status));
  useInterval(() => void reviews.reload(true), 5000, anyActive);

  const resetPage = () => setPage(1);

  return (
    <>
      {can.submit(user.role) && (
        <PageSection>
          <SubmitReviewForm
            onSubmitted={(r) => {
              toast.success(`Review #${r.id} queued`, `${r.repo}#${r.pr_number}`);
              void reviews.reload(true);
            }}
          />
        </PageSection>
      )}
      <PageSection>
        <Card>
          <CardBody>
            <Title headingLevel="h2" size="lg" style={{ marginBottom: 8 }}>
              Reviews
            </Title>
            <Toolbar>
              <ToolbarContent>
                <ToolbarItem>
                  <FormSelect
                    aria-label="Filter by status"
                    value={status}
                    onChange={(_e, v) => {
                      setStatus(v as ReviewStatus | "");
                      resetPage();
                    }}
                  >
                    <FormSelectOption value="" label="All statuses" />
                    {REVIEW_STATUSES.map((s) => (
                      <FormSelectOption key={s} value={s} label={s} />
                    ))}
                  </FormSelect>
                </ToolbarItem>
                <ToolbarItem>
                  <SearchInput
                    aria-label="Filter by repository"
                    placeholder="Repository (org/repo)"
                    value={repoDraft}
                    onChange={(_e, v) => setRepoDraft(v)}
                    onSearch={(_e, v) => {
                      setRepo(v.trim());
                      resetPage();
                    }}
                    onClear={() => {
                      setRepoDraft("");
                      setRepo("");
                      resetPage();
                    }}
                  />
                </ToolbarItem>
                <ToolbarItem alignSelf="center">
                  <Switch
                    id="reviews-mine"
                    label="Only mine"
                    isChecked={mine}
                    onChange={(_e, v) => {
                      setMine(v);
                      resetPage();
                    }}
                  />
                </ToolbarItem>
                <ToolbarItem>
                  <Button
                    variant="plain"
                    aria-label="Refresh"
                    onClick={() => void reviews.reload(true)}
                  >
                    <SyncIcon />
                  </Button>
                </ToolbarItem>
                <ToolbarItem variant="pagination" align={{ default: "alignRight" }}>
                  <Pagination
                    isCompact
                    itemCount={reviews.data?.total ?? 0}
                    page={page}
                    perPage={perPage}
                    onSetPage={(_e, p) => setPage(p)}
                    onPerPageSelect={(_e, pp) => {
                      setPerPage(pp);
                      setPage(1);
                    }}
                  />
                </ToolbarItem>
              </ToolbarContent>
            </Toolbar>
            {reviews.error && (
              <LoadError message={reviews.error} onRetry={() => void reviews.reload()} />
            )}
            {reviews.loading && !reviews.data ? (
              <Loading />
            ) : items.length === 0 && !reviews.error ? (
              <Empty
                title="No reviews"
                body={
                  status || repo || mine
                    ? "No reviews match the current filters."
                    : "Reviews appear here once someone submits a pull request."
                }
              />
            ) : (
              <Table aria-label="Reviews" variant="compact">
                <Thead>
                  <Tr>
                    <Th>Pull request</Th>
                    <Th>Agent / model</Th>
                    <Th>Status</Th>
                    <Th>Verdict</Th>
                    <Th>Trigger</Th>
                    <Th>Submitted</Th>
                  </Tr>
                </Thead>
                <Tbody>
                  {items.map((r) => (
                    <Tr
                      key={r.id}
                      isClickable
                      onRowClick={(e) => {
                        if ((e?.target as HTMLElement | undefined)?.closest("a")) return;
                        navigate(`/reviews/${r.id}`);
                      }}
                    >
                      <Td dataLabel="Pull request">
                        <Link to={`/reviews/${r.id}`}>
                          {r.repo}#{r.pr_number}
                        </Link>
                        {r.title && (
                          <div style={{ color: "var(--pf-v5-global--Color--200)" }}>{r.title}</div>
                        )}
                      </Td>
                      <Td dataLabel="Agent / model">
                        {AGENT_LABELS[r.agent] ?? r.agent}
                        <div style={{ color: "var(--pf-v5-global--Color--200)" }}>
                          {modelText(r)}
                        </div>
                      </Td>
                      <Td dataLabel="Status">
                        <StatusLabel status={r.status} />
                      </Td>
                      <Td dataLabel="Verdict">
                        <VerdictLabel verdict={r.verdict} />
                      </Td>
                      <Td dataLabel="Trigger">
                        <TriggerLabel trigger={r.trigger} />
                      </Td>
                      <Td dataLabel="Submitted">{formatTime(r.created_at)}</Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            )}
          </CardBody>
        </Card>
      </PageSection>
    </>
  );
}
