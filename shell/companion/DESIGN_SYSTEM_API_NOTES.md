# @datarobot/design-system API notes (v30.13.0)

Scratch reference for the next task's implementer. Read directly from:
`node_modules/@datarobot/design-system/esm/{stepper,badge,granular-progress-bar}/*.d.ts`

Import paths that work — **subpath imports only**, NOT the root package:
```ts
import { Stepper } from '@datarobot/design-system/stepper'
import { Badge } from '@datarobot/design-system/badge'
import { GranularProgressBar } from '@datarobot/design-system/granular-progress-bar'
import type { Step, StepKey, StepperProps } from '@datarobot/design-system/stepper'
import type { BadgeProps } from '@datarobot/design-system/badge'
import type { GranularProgressBarProps } from '@datarobot/design-system/granular-progress-bar'
```
The root `@datarobot/design-system/package.json` has **no** `main`, `module`,
`types`, or `exports` field at all, so `import { Badge } from '@datarobot/design-system'`
fails both `tsc` (`TS2307: Cannot find module '@datarobot/design-system'`) and
`vite build` (`Rolldown failed to resolve import "@datarobot/design-system"`).
Verified directly against `node_modules/@datarobot/design-system/package.json`.

Note: the per-component subpath `package.json` files (e.g. `stepper/package.json`)
have real `main`/`module` fields (e.g. `"module": "../esm/stepper"`) but all set
`"types": "../esm/index.d.ts"` — i.e. types resolve via the top-level barrel
`esm/index.d.ts`, not a per-component `.d.ts`. That's fine; only the root
package's own `package.json` (used for the bare, no-subpath import) is missing
the fields entirely, which is what breaks the bare import.

## Stepper (`stepper/types.ts`, `stepper/stepper.ts`)

`StepKey = string | number`

`Step` interface:
- `label: string` (required)
- `key: StepKey` (required)
- `icon: IconLookup` (required — from `@fortawesome/fontawesome-svg-core`, NOT optional)
- `helperText?: string`
- `isDisabled?: boolean`
- `hasErrored?: boolean`

No `status` field, no `disabled` field (it's `isDisabled`), no `active`/`completed`
flags on `Step` itself — active/completed state is derived by the `Stepper`
component from `activeKey` + array order, not stored per-step.

`StepperProps`:
- `steps: Step[]` (required)
- `onClick: (key: StepKey) => void` (required)
- `isDisabled?: boolean` (disables the whole stepper)
- `className?: string`
- `testId?: string`
- `activeKey?: StepKey` — type is `StepKey` (`string | number`), NOT a numeric index

Helper functions also exported: `isLastInList(index, listLength)`,
`getActiveStepIndex(steps, key?)`, `isCompletedStep(stepIndex, activeStepIndex)`.

`StepItemProps` (internal `StepperItem` component, exported but usually not
used directly): `label`, `icon`, `helperText?`, `isDisabled?`, `hasErrored?`,
`isCompleted: boolean`, `onClick: (key: StepKey) => void`, `isRectHidden: boolean`,
`isActive: boolean`, `isVisited: boolean`, `stepKey: StepKey`, `testId: string`.

## Badge (`badge/badge.ts`)

`BadgeProps`:
- `children?: React.ReactNode`
- `label?: string` (string alternative to children)
- `error?: boolean`
- `success?: boolean`
- `info?: boolean`
- `warning?: boolean`
- `plain?: boolean`
- `outlined?: boolean`
- `dark?: boolean`
- Contextual color flags (not mentioned in task prompt, confirmed present):
  `pink?: boolean`, `turquoise?: boolean`, `purple?: boolean`, `blue?: boolean`,
  `orange?: boolean`, `apple?: boolean`
- `icon?: IconLookup | null`
- `badgeClassNames?: string`
- `testId?: string`
- `tooltipText?: string`
- `onClick?: (event?: MouseEvent<HTMLButtonElement | HTMLSpanElement>) => void`
- `onClose?: () => void`
- `isLoading?: boolean` (shows spinner, badge not clickable in this state)
- `isDisabled?: boolean` — note: this is `isDisabled`, NOT `disabled`
- `removeButtonAriaLabel?: string` (only relevant with `onClose`)
- `badgeContainerTestId?: string` (only relevant with `onClose`)
- `ariaLabel?: string`

Confirmed vs. task prompt's assumed field list: `success`, `error`, `info`,
`warning`, `plain`, `isLoading`, `outlined`, `icon`, `onClose` all match exactly.
Differences / additions found: no plain `disabled` (it's `isDisabled`); there
are extra boolean flags (`dark`, `pink`, `turquoise`, `purple`, `blue`, `orange`,
`apple`) and extra props (`label`, `children`, `badgeClassNames`, `testId`,
`tooltipText`, `onClick`, `removeButtonAriaLabel`, `badgeContainerTestId`,
`ariaLabel`) not mentioned in the task prompt.

## GranularProgressBar (`granular-progress-bar/granular-progress-bar.ts`)

`GranularProgressBarProps`:
- `levelsCount?: number` (total vertical bars)
- `activeLevelsCount?: number` (active/colored bars)
- `className?: string`
- `testId?: string`
- `isVertical?: boolean` (stack vertically instead of horizontally)

## Peer dependencies pulled in

`Badge`'s `icon` type (`IconLookup`) comes from `@fortawesome/fontawesome-svg-core`,
which is a peer dependency of `@datarobot/design-system` (peer range `^7.2.0`).
`npm install` auto-resolved and installed the FontAwesome peer packages
(`@fortawesome/fontawesome-svg-core@7.3.1`, `free-solid-svg-icons`,
`free-regular-svg-icons`, `free-brands-svg-icons`, `react-fontawesome`) alongside
several other peer deps (`moment`, `slate*`, `codemirror`, `@codemirror/*`,
`rehype-*`, `remark-*`, `unified`, `@tanstack/react-virtual`) — `npm install`
printed `ERESOLVE overriding peer dependency` warnings (non-fatal) during this,
likely due to version overlaps with existing deps; install still completed
with 0 vulnerabilities.
