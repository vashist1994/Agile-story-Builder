import { useState, type FormEvent } from "react";

type RequirementSummary = {
  business_objective: string;
  actors: string[];
  key_capabilities: string[];
  constraints_and_business_rules: string[];
};

type Epic = {
  epic_id: string;
  title: string;
  description: string;
  business_value: string;
  related_story_ids: string[];
};

type AcceptanceCriterion = {
  given: string;
  when: string;
  then: string;
};

type UserStory = {
  story_id: string;
  epic_id: string | null;
  title: string;
  persona: string;
  user_story: string;
  acceptance_criteria: AcceptanceCriterion[];
  suggested_priority: "High" | "Medium" | "Low" | "Not suggested";
  priority_rationale: string;
  dependencies: string[];
  refinement_status:
    | "Draft"
    | "Needs Clarification"
    | "Ready for Refinement Review"
    | "Sprint-Ready Candidate";
  requirement_references: string[];
  assumptions: string[];
  open_questions: string[];
};

type NonFunctionalRequirement = {
  item_id: string;
  requirement: string;
  related_item_ids: string[];
  testability_or_clarification: string;
};

type OpenQuestion = {
  question: string;
  affected_items: string[];
};

type RequirementCoverage = {
  requirement_id: string;
  requirement_type:
    | "Functional"
    | "Non-functional"
    | "Business rule"
    | "Constraint"
    | "Actor goal"
    | "Business objective"
    | "Other";
  explicit_requirement: string;
  mapped_items: string[];
  status: "Covered" | "Partially covered" | "Not covered";
  reason: string;
};

type RefinementRecommendation = {
  recommendation: string;
  related_items: string[];
};

type AgileBacklog = {
  requirement_summary: RequirementSummary;
  epics: Epic[];
  user_stories: UserStory[];
  non_functional_requirements: NonFunctionalRequirement[];
  assumptions: string[];
  open_questions: OpenQuestion[];
  out_of_scope: string[];
  requirement_coverage: RequirementCoverage[];
  refinement_recommendations: RefinementRecommendation[];
};

type GeneratedBacklog = {
  source_requirement: string;
  backlog: AgileBacklog;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const MAX_REQUIREMENT_CHARS = 10_000;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const ACCEPTED_FILE_TYPES = ".txt,.pdf,.docx";

async function responseError(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      return body.detail;
    }
  } catch {
    return `Request failed (${response.status}).`;
  }
  return `Request failed (${response.status}).`;
}

function ListSection({
  title,
  items,
  emptyMessage = "None identified.",
}: {
  title: string;
  items: string[];
  emptyMessage?: string;
}) {
  return (
    <section className="story-section">
      <h3>{title}</h3>
      {items.length > 0 ? (
        <ul>
          {items.map((item, index) => (
            <li key={`${title}-${index}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">{emptyMessage}</p>
      )}
    </section>
  );
}

function ItemTags({ items }: { items: string[] }) {
  return items.length > 0 ? (
    <div className="item-tags">
      {items.map((item) => <span className="item-tag" key={item}>{item}</span>)}
    </div>
  ) : (
    <span className="muted">None identified</span>
  );
}

function BacklogView({ backlog }: { backlog: AgileBacklog }) {
  const summary = backlog.requirement_summary;
  return (
    <div className="backlog-content">
      <section className="backlog-section">
        <p className="section-kicker">REQUIREMENT SUMMARY</p>
        <p className="summary-objective">{summary.business_objective}</p>
        <div className="story-details">
          <ListSection title="Actors" items={summary.actors} />
          <ListSection title="Key capabilities" items={summary.key_capabilities} />
        </div>
        <ListSection title="Constraints and business rules" items={summary.constraints_and_business_rules} />
      </section>

      <section className="backlog-section">
        <p className="section-kicker">EPICS <span>{backlog.epics.length}</span></p>
        {backlog.epics.length ? (
          <div className="backlog-list">
            {backlog.epics.map((epic) => (
              <article className="backlog-item" key={epic.epic_id}>
                <div className="item-title-row">
                  <span className="item-id">{epic.epic_id}</span>
                  <h3>{epic.title}</h3>
                </div>
                <p>{epic.description}</p>
                <p><strong>Business value:</strong> {epic.business_value}</p>
                <div className="item-meta">
                  <span>Stories</span><ItemTags items={epic.related_story_ids} />
                </div>
              </article>
            ))}
          </div>
        ) : <p className="muted">No epic hierarchy needed for this requirement.</p>}
      </section>

      <section className="backlog-section">
        <p className="section-kicker">USER STORIES <span>{backlog.user_stories.length}</span></p>
        {backlog.user_stories.length ? (
          <div className="backlog-list">
            {backlog.user_stories.map((story) => (
              <article className="backlog-item story-item" key={story.story_id}>
                <div className="item-title-row">
                  <span className="item-id">{story.story_id}</span>
                  <h3>{story.title}</h3>
                </div>
                <p className="persona-tag">{story.persona}</p>
                {story.epic_id && <p className="item-meta-line">Epic: {story.epic_id}</p>}
                <blockquote>{story.user_story}</blockquote>

                <div className="criteria-list">
                  <h4>Acceptance criteria</h4>
                  {story.acceptance_criteria.map((criterion, index) => (
                    <div className="criterion" key={`${story.story_id}-criterion-${index}`}>
                      <p><strong>Given</strong> {criterion.given}</p>
                      <p><strong>When</strong> {criterion.when}</p>
                      <p><strong>Then</strong> {criterion.then}</p>
                    </div>
                  ))}
                </div>

                <div className="story-details story-meta">
                  <div><h4>Priority suggestion</h4><p>{story.suggested_priority}</p><p className="muted">{story.priority_rationale}</p></div>
                  <div><h4>Refinement status</h4><p>{story.refinement_status}</p></div>
                  <div><h4>Dependencies</h4><ItemTags items={story.dependencies} /></div>
                  <div><h4>Requirement references</h4><ListSection title="" items={story.requirement_references} /></div>
                  <div><h4>Assumptions for this story</h4><ListSection title="" items={story.assumptions} /></div>
                  <div><h4>Open questions for this story</h4><ListSection title="" items={story.open_questions} /></div>
                </div>
              </article>
            ))}
          </div>
        ) : <p className="muted">No user stories were generated.</p>}
      </section>

      <section className="backlog-section">
        <p className="section-kicker">NON-FUNCTIONAL REQUIREMENTS <span>{backlog.non_functional_requirements.length}</span></p>
        {backlog.non_functional_requirements.length ? (
          <div className="backlog-list">
            {backlog.non_functional_requirements.map((item, index) => (
              <article className="backlog-item" key={`nfr-${index}`}>
                <div className="item-title-row">
                  <span className="item-id">{item.item_id}</span>
                </div>
                <p>{item.requirement}</p>
                <p><strong>Related items:</strong> {item.related_item_ids.join(", ") || "None"}</p>
                <p><strong>Testability / clarification:</strong> {item.testability_or_clarification}</p>
              </article>
            ))}
          </div>
        ) : <p className="muted">No explicit non-functional requirements identified.</p>}
      </section>

      <div className="backlog-columns">
        <ListSection title="Assumptions" items={backlog.assumptions} />
        <ListSection title="Out of scope" items={backlog.out_of_scope} emptyMessage="No explicit exclusions stated." />
      </div>

      <section className="backlog-section">
        <p className="section-kicker">OPEN QUESTIONS <span>{backlog.open_questions.length}</span></p>
        {backlog.open_questions.length ? (
          <ul className="plain-list">
            {backlog.open_questions.map((item, index) => (
              <li key={`question-${index}`}>
                <p>{item.question}</p>
                <span className="muted">Affected: {item.affected_items.join(", ") || "Requirement"}</span>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No material open questions identified.</p>}
      </section>

      <section className="backlog-section">
        <p className="section-kicker">REQUIREMENT COVERAGE <span>{backlog.requirement_coverage.length}</span></p>
        {backlog.requirement_coverage.length ? (
          <div className="backlog-list">
            {backlog.requirement_coverage.map((item, index) => (
              <article className="coverage-item" key={`coverage-${index}`}>
                <div className="coverage-header">
                  <span className="item-id">{item.requirement_id}</span>
                  <span className="item-meta-line">{item.requirement_type}</span>
                  <span className={`coverage-status ${item.status.toLowerCase().replace(/ /g, "-")}`}>{item.status}</span>
                  <ItemTags items={item.mapped_items} />
                </div>
                <p>{item.explicit_requirement}</p>
                <p className="muted">{item.reason}</p>
              </article>
            ))}
          </div>
        ) : <p className="muted">No explicit requirements mapped.</p>}
      </section>

      <section className="backlog-section">
        <p className="section-kicker">REFINEMENT RECOMMENDATIONS <span>{backlog.refinement_recommendations.length}</span></p>
        {backlog.refinement_recommendations.length ? (
          <ul className="plain-list">
            {backlog.refinement_recommendations.map((item, index) => (
              <li key={`recommendation-${index}`}>
                <p>{item.recommendation}</p>
                <span className="muted">Related: {item.related_items.join(", ") || "Backlog"}</span>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No further refinement recommendations.</p>}
      </section>
    </div>
  );
}

export default function App() {
  const [requirement, setRequirement] = useState("");
  const [requirementFile, setRequirementFile] = useState<File | null>(null);
  const [result, setResult] = useState<GeneratedBacklog | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState("");

  async function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResult(null);
    setIsGenerating(true);
    try {
      let response: Response;
      if (requirementFile) {
        const formData = new FormData();
        formData.append("file", requirementFile);
        response = await fetch(`${API_BASE_URL}/api/v1/stories/generate-file`, {
          method: "POST",
          body: formData,
        });
      } else {
        response = await fetch(`${API_BASE_URL}/api/v1/stories/generate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ requirement: requirement.trim() }),
        });
      }
      if (!response.ok) throw new Error(await responseError(response));
      setResult((await response.json()) as GeneratedBacklog);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  }

  async function handleExport() {
    if (!result) return;
    setError("");
    setIsExporting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/stories/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(result),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const download = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = download;
      link.download = "agile-backlog.csv";
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(download);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "CSV export failed. Please try again.");
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Storyforge home">
          <span className="brand-mark" aria-hidden="true">s</span>
          <span>storyforge</span>
        </a>
        <span className="topbar-note"><span className="status-dot" /> Grounded by design</span>
      </header>

      <section className="hero">
        <p className="eyebrow"><span /> PRODUCT WORK, MADE CLEARER</p>
        <h1>From requirement<br />to <em>ready.</em></h1>
        <p className="hero-copy">
          Turn the way your team talks into a grounded, traceable Agile backlog.
          Clear stories, testable criteria, and open questions.
        </p>
      </section>

      <section className="workspace" aria-label="Agile backlog generator">
        <form className="input-card" onSubmit={handleGenerate}>
          <div className="card-heading">
            <div>
              <span className="step-label">01 / YOUR INPUT</span>
              <h2>What do you need?</h2>
            </div>
            <span className="input-icon" aria-hidden="true">↳</span>
          </div>
          <label className="sr-only" htmlFor="requirement">Describe your requirement</label>
          <textarea
            id="requirement"
            value={requirement}
            onChange={(event) => {
              setRequirementFile(null);
              setRequirement(event.target.value.slice(0, MAX_REQUIREMENT_CHARS));
            }}
            placeholder="Describe the product requirement, goals, constraints, and expected behavior…"
            maxLength={MAX_REQUIREMENT_CHARS}
            required={!requirementFile}
            minLength={requirementFile ? undefined : 10}
          />
          <div className="upload-row">
            <label className="upload-button" htmlFor="requirement-file">
              <span aria-hidden="true">↑</span> Choose a file
            </label>
            <input
              id="requirement-file"
              className="file-input"
              type="file"
              accept={ACCEPTED_FILE_TYPES}
              onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                if (file && file.size > MAX_FILE_BYTES) {
                  setError("File exceeds the 10 MB upload limit.");
                  event.target.value = "";
                  setRequirementFile(null);
                  return;
                }
                setError("");
                setRequirement("");
                setRequirementFile(file);
              }}
            />
            <span className="upload-hint">.TXT, .PDF, .DOCX · MAX 10 MB</span>
            {requirementFile && (
              <button
                className="remove-file"
                type="button"
                onClick={() => {
                  setRequirementFile(null);
                  const input = document.getElementById("requirement-file");
                  if (input instanceof HTMLInputElement) input.value = "";
                }}
              >
                {requirementFile.name} ×
              </button>
            )}
          </div>
          <div className="input-footer">
            <span>{requirementFile ? "Document selected" : `${requirement.length.toLocaleString()} / ${MAX_REQUIREMENT_CHARS.toLocaleString()}`}</span>
            <button className="generate-button" type="submit" disabled={isGenerating || (!requirementFile && requirement.trim().length < 10)}>
              {isGenerating ? (
                <><span className="spinner" /> Refining backlog</>
              ) : (
                <>Generate backlog <span aria-hidden="true">↗</span></>
              )}
            </button>
          </div>
          <p className="privacy-note"><span aria-hidden="true">✳</span> Your requirement stays yours. Nothing is stored.</p>
        </form>

        <section className={`result-card${result ? " has-result" : ""}`} aria-live="polite">
          <div className="card-heading result-heading">
            <div>
              <span className="step-label">02 / THE BACKLOG</span>
              <h2>{result ? "Ready to refine." : "Your backlog, right here."}</h2>
            </div>
            {result && (
              <button className="export-button" type="button" onClick={handleExport} disabled={isExporting}>
                {isExporting ? "Preparing…" : "↓  Export CSV"}
              </button>
            )}
          </div>

          {error && <div className="error-message" role="alert">{error}</div>}

          {result ? (
            <BacklogView backlog={result.backlog} />
          ) : (
            <div className="empty-state">
              <div className="empty-illustration" aria-hidden="true">
                <span className="paper paper-back" /><span className="paper paper-front"><i /><i /><i /></span>
                <span className="sparkle sparkle-one">✳</span><span className="sparkle sparkle-two">✦</span>
              </div>
              <p>A good backlog starts with<br />a little context.</p>
              <span>YOUR GENERATED BACKLOG WILL APPEAR HERE</span>
            </div>
          )}
        </section>
      </section>

      <footer>
        <span>MADE FOR THE PEOPLE BUILDING WHAT’S NEXT</span>
        <span>Clear intent. Better outcomes.</span>
      </footer>
    </main>
  );
}
