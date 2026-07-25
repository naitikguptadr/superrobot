import {
  faMagnifyingGlass,
  faArrowsRotate,
  faCircleCheck,
  faCloudArrowUp,
  faReceipt,
} from "@fortawesome/free-solid-svg-icons";
import { Badge } from "@datarobot/design-system/badge";
import { Stepper } from "@datarobot/design-system/stepper";
import type { Step } from "@datarobot/design-system/stepper";
import { badgePropsForStatus, labelForStatus } from "./status-mapping";
import type { PipelineState, StageId } from "./pipeline-types";

export interface PipelineViewProps {
  state: PipelineState;
}

const STAGE_LABELS: Record<StageId, string> = {
  scan: "Scan",
  transform: "Transform",
  validate: "Validate",
  deploy: "Deploy",
  receipt: "Receipt",
};

const STAGE_ICONS: Record<StageId, Step["icon"]> = {
  scan: faMagnifyingGlass,
  transform: faArrowsRotate,
  validate: faCircleCheck,
  deploy: faCloudArrowUp,
  receipt: faReceipt,
};

function noop(): void {
  // Stepper requires an onClick handler; the pipeline stepper here is
  // read-only status display, not an interactive navigation control.
}

export function PipelineView({ state }: PipelineViewProps) {
  if (state.length === 0) {
    return <p>No pipeline activity yet.</p>;
  }

  // Note: the Stepper renders each Step's `label` as its own visible text
  // node (see stepper-item-label in the design system's markup), so the
  // stage row list below intentionally does NOT repeat the stage label --
  // that would create ambiguous duplicate text in the DOM. The label is
  // still available on the row via `aria-label` for accessibility/testing
  // tools that inspect attributes rather than text content. `helperText`
  // is likewise left unset on the Step so the detail message renders in
  // exactly one place (the row's detail <span>) instead of two.
  const steps: Step[] = state.map((stage) => ({
    label: STAGE_LABELS[stage.id] ?? stage.id,
    key: stage.id,
    icon: STAGE_ICONS[stage.id] ?? faCircleCheck,
    hasErrored: stage.status === "failed",
  }));

  // When no stage is "active" yet (e.g. the pipeline hasn't started and
  // every stage is still "pending"), fall back to the FIRST stage rather
  // than the last -- the Stepper marks everything before `activeKey` as
  // completed, so falling back to the last stage would falsely show the
  // whole pipeline as done.
  const activeKey = state.find((s) => s.status === "active")?.id ?? state[0].id;

  return (
    <div>
      <Stepper steps={steps} onClick={noop} activeKey={activeKey} isDisabled />
      <ul>
        {state.map((stage) => (
          <li key={stage.id} aria-label={STAGE_LABELS[stage.id] ?? stage.id}>
            <Badge {...badgePropsForStatus(stage.status)} label={labelForStatus(stage.status)} />
            {stage.detail && <span>{stage.detail}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
