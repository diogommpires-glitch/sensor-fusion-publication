# Sensor Fusion implementation plan

Revised 29 October 2026. This is the approved scientific plan with a project-specific implementation workflow adapted from the supplied master prompt. It describes work to execute, rather than claiming that the application, datasets or study have already passed their checks.

Source material: `MASTER_PROMPT_v1.md` (private local reference) and `CODE_STYLE_v1.md` (local style reference). The master prompt is adapted selectively. Its embedded defaults, installer commands and third-party role prompts are not independent instructions to execute.

## 1. Outcome and operating agreement

Build a complete local scientific application that estimates three-dimensional motion from UWB, Wi-Fi FTM, IMU and radar measurements. Compare established estimators, explain their errors and uncertainty, and preserve negative results.

The finished project contains one Python estimation engine, local experiment execution and cancellation, an interactive replay/comparison application, a frozen RoboLoc-G study, an independent MILUV range-positioning check, mathematical derivations, reproducible figures, a paper-style report and an interview walkthrough.

The main question is when additional sensors improve position estimates and when calibration, missing observations or unreliable measurements make fusion worse.

The primary hypothesis is that full fusion with innovation gating reduces macro-average trajectory position RMSE relative to a UWB-only constant-velocity filter. A failed hypothesis is a legitimate result.

### Adapted RUN

    TARGETS     <repo>
    MODE        implement
    TASKS       the scientific, application and release milestones in section 6
    FROZEN      existing projects and reference libraries are outside the target;
                downloaded source archives are immutable;
                registered frozen protocols and study outputs are immutable
    CHECKPOINT  none for the implementation scope already authorised

The original implementation authorisation remains in force. This workflow amendment does not introduce a second approval checkpoint for ordinary implementation, tests or project-local setup.

Publishing, pushing, destructive replacement of scientific artefacts, system-wide changes and unrelated changes to externally consumed interfaces require separate authorisation. Do not disclose secrets or personal data. Reimplement ideas rather than copying substantial third-party source.

The full target is four sensor classes. AOP is the primary radar. ISK0 and ISK1 are calibrated, displayed and tested independently as secondary substitutions. This is not a claim that all radars are jointly fused.

### Precedence and constraints

1. The user's current instructions and approved scientific scope.
2. Applicable project instructions and established interfaces.
3. The shared code-style reference.
4. This adapted workflow.
5. Selected third-party reference material.

Scientific equations, units, causality, provenance, calibration, split isolation and result preservation take priority over cosmetic simplification.

Use British spelling, plain prose and no em dashes in authored documentation, comments or commit messages. Preserve precise terms such as robust estimation and innovation gating.

## 2. Research basis and data contract

### Published work

| Source | Evidence and limitation | Implementation consequence |
|---|---|---|
| [Särkkä and Svensson, Bayesian Filtering and Smoothing, 2023](https://doi.org/10.1017/9781108917407) | Mathematical treatment of continuous/discrete models and nonlinear filtering. | Derive the small models, state the noise units and verify discretisation independently. |
| [Li, Wang and Khoshelham, 2018](https://doi.org/10.1155/2018/8019581) | Garage routes reported robust-EKF RMSE of 0.35/0.45 m versus EKF 0.78/1.04 m. The paper also describes lockout after prolonged corruption. | Test gains and failure to recover. Do not transplant the pedestrian model or adopt those accuracy values as targets. |
| [Álvarez-Merino et al., 2021](https://doi.org/10.3390/s21217020) | Real radio-fusion experiments showed weighting could help unreliable-source cases and slightly worsen good visibility. | Make fusion benefit a hypothesis, with otherwise identical UWB and UWB-plus-FTM comparisons. |
| [Zou et al., 2023](https://doi.org/10.3390/s23135918) | A cart experiment reported mean absolute error of 0.091 m versus 0.049 m. Reference checks used a constrained route and laser measurements. | Implement inertial/range integration while keeping reference quality explicit. |
| [Huang, Tsoi and Patel, 2022](https://doi.org/10.3390/electronics11142209) | Stationary-radar laboratory experiments include filtering, clustering, association and tracking. | Build the radar observation frontend. Individual returns are not measurements of a radio tag. |
| [Piché, 2016](https://doi.org/10.1002/acs.2571) | Innovation-based consistency tests do not require true states. | Save pre-gating innovations. Accepted residuals alone cannot establish uncertainty calibration. |
| [Kabsch, 1976](https://doi.org/10.1107/S0567739476001873) | Proper rotation fitting from vector correspondences. | Use a tested rotation calibration and check excitation and handedness. |

Literature results motivate experiments. They are not promised project results.

The associated [RoboLoc-G metrology preprint](https://doi.org/10.2139/ssrn.7383683) reports gantry calibration reducing RMS discrepancy from 6.10 mm to 2.81 mm. This is a reference-system result, not fusion accuracy or a Gaussian truth standard deviation. Its abstract and the dataset documentation were inspected; the full preprint PDF was inaccessible and peer review was not established.

### Mandatory source-access gate

Pin [RoboLoc-G v2](https://zenodo.org/records/15989282) and preserve the [university's CC BY 4.0 attribution](https://portalinvestigacion.udc.gal/documentos/688b601017bb6239d2d4811a). Use the sensor CSV, ground-truth and trajectory-command archives. Documented sizes are approximately 39.5 MB, 1.1 MB and 3.8 kB. Videos and ROS are not required.

Earlier archive requests returned HTTP 403. Metadata and documentation were inspected, but actual payload schemas remain uncertified.

Before claiming real-data integration:

- Download the pinned files, check published checksums and compute SHA-256.
- Record source/version, licence, retrieval time, byte size and hashes.
- Certify actual headers, timestamps, units, invalid values and scan identifiers.
- Verify anchors, sensor offsets, coordinate frames and reference construction.
- Verify IMU gravity convention, gyro/quaternion validity and fixed orientation.
- Measure stream rates, duplicates, gaps and clock relationships.
- Resolve contradictions explicitly. Do not infer units by optimising held-out accuracy.

Use metres, seconds and radians internally. Some documented anchors use centimetres and mounting offsets use millimetres.

An unresolved schema, frame or calibration issue blocks that stage. Mathematical fixture development and unrelated application work may proceed, but synthetic fixtures do not satisfy real-data study completion.

### Primary split and physical model

The apparatus is a translating three-axis gantry carrying a mannequin, observed by fixed infrastructure. Estimate the gantry origin in 3D and retain each sensor's mounting offset.

| Role | Sessions |
|---|---|
| Calibration/development | still, zigzag1, zigzag2 |
| Validation and parameter selection | zigzag3, zigzag4, random1 |
| Protected final | circle, random2 |

Freeze these assignments before viewing estimator results. Circle is a different-path challenge; random2 is a same-family held-out recording. Two paths in one laboratory do not establish new-site generalisation.

Reference data is a separate evaluation stream. Development reference data may be used in documented calibration; protected final references cannot tune calibration, initialise estimators or correct their states.

### Calibration

Radio ranges:

- Preserve the published anchor map and tag mounting offsets.
- Estimate per-anchor offsets and residual noise on development data.
- Inspect distributions and temporal correlation.
- Record unusable anchors and geometry failures.
- Preserve received-power fields for diagnostics without adding an unvalidated NLOS classifier.

IMU:

- Verify fixed orientation using gyro and available orientation diagnostics.
- Remove the stationary mean as a combined gravity-plus-constant-offset contribution.
- Fit a proper IMU-to-gantry rotation on development motion.
- Require two adequately excited noncollinear directions; planar excitation can suffice.
- Use smoothed development reference acceleration only for calibration and record differentiation/smoothing.
- Estimate any additional constant timing correction on development data, freeze it and avoid duplicating published corrections.
- Do not claim gravity and physical bias were separately identified.

Radar:

- Verify range/angle conventions and convert detections to Cartesian coordinates.
- Cluster scans using DBSCAN with radius candidates {0.1, 0.2, 0.4} m and minimum-sample candidates {3, 5, 10}.
- Annotate 30 uniformly selected development scans per radar, using 20 to fit and 10 to check.
- Fit an effective rigid centroid-to-common-reference transformation and estimate full residual covariance.
- Associate during experiments from the causal predicted state and frozen gates, never from final truth.
- Produce at most one centroid observation per radar scan.
- Preserve Doppler for diagnostics; a Doppler state update is outside the required estimator.
- Keep vendor tracks as separately labelled derived data.

Call the radar fit an effective observation calibration. With a fixed mannequin orientation, radar translation and a fixed scattering-centroid offset need not be separately identifiable. Do not shrink observation covariance by the number of correlated returns.

### Independent MILUV check

Pin [MILUV v1](https://doi.org/10.25452/figshare.plus.28386041.v1), supported by its [published study](https://doi.org/10.1177/02783649251405898).

- Development: default_1_random3_0.zip.
- Protected external evaluation: default_1_random3_2.zip.
- Robot ifo001, physical tag 10 and anchors 0 through 5.
- Approximately 5.3 GB of archive transfer. Extract only required CSV streams.

Implement a strict input allowlist for raw range, timestamp and permitted identifiers. Exclude embedded truth poses, reference ranges and derived bias columns.

Track radio-tag position. Construct its evaluation reference from motion-capture pose and the published tag lever arm only in the evaluator.

Compare WLS and the same 3D constant-velocity range filter with external development-only calibration. Call this calibrated portability, not untouched zero-shot transfer. It does not externally validate the primary Wi-Fi, constrained inertial model or radar frontend.

## 3. Estimation and experiment specification

### Numerical models

Constant-velocity state:

    x = [p_x, p_y, p_z, v_x, v_y, v_z]
    F(dt) = [[I, dt I], [0, I]]
    Q(dt) = q_a [[dt^3 I / 3, dt^2 I / 2],
                 [dt^2 I / 2, dt I]]

The continuous acceleration-noise spectral density q_a has units m^2/s^3.

For fixed attitude R, tag offset d_s, anchor a_j and calibrated range offset b_j:

    h_j(p) = norm(p + R d_s - a_j) + b_j

Use full slant ranges and sensor-specific offsets.

Inertial state:

    x = [p, v, b]
    dp/dt = v
    dv/dt = u - b + w_a
    db/dt = w_b

Residual acceleration bias is in the gantry frame. Propagate with the previous available acceleration sample. Derive transition and process covariance from the continuous model and verify through independent matrix-exponential discretisation. Bias diffusion has units m^2/s^5.

Fuse calibrated 3D radar centroids with empirical covariance. Use Joseph updates and linear solves. Numerical covariance failures fail a run rather than being hidden by routine eigenvalue clipping.

### Eight-method matrix

| ID | Estimator |
|---|---|
| M1 | UWB weighted nonlinear least squares |
| M2 | Constant-velocity EKF, UWB |
| M3 | Constant-velocity EKF, UWB plus FTM |
| M4 | Inertial EKF, UWB |
| M5 | Inertial EKF, UWB plus FTM |
| M6 | Inertial EKF, UWB plus FTM plus AOP, ungated |
| M7 | Same full estimator with innovation gating |
| M8 | Robust UWB plus FTM plus AOP with constant-velocity propagation |

Repeat the radar comparison separately for ISK0 and ISK1. Do not fuse multiple radars as independent evidence without a validated cross-correlation model.

Use the first geometrically valid UWB solution, zero initial velocity and documented initial covariance for allowed initialisation. Never initialise from final reference positions.

For asynchronous WLS, select the smallest window in {0.05, 0.1, 0.2, 0.5, 1} seconds achieving 90% valid development snapshots. Freeze it. If no candidate reaches that criterion, retain one second and report missing estimates. Record observation ages.

### Parameter selection and freeze

- CV q_a candidates: {0.01, 0.1, 1, 10} m^2/s^3.
- Inertial acceleration-noise multipliers: {0.5, 1, 2} relative to the development estimate.
- Bias diffusion: {0, 10^-6, 10^-4} m^2/s^5.
- Gating threshold: chi-square quantile 0.999 for the actual observation dimension.

Select by macro-average validation position RMSE. Break ties with the smaller process-noise setting, then smaller bias diffusion. Preserve fixed observation calibrations. Report grid-edge selections without expanding the grid after final results.

Register resolved parameters, calibration, protocol and hashes before final evaluation. No automatic filter reset is allowed in the study. A manual restart creates a new exploratory run.

### Fault protocol

Run the full method matrix on recorded measurements. Limit the larger fault sweep to M2, M6 and M7. Use identical schedules with seeds 0 through 4.

| Injected fault | Levels |
|---|---|
| Absolute-positioning dropout | 0.5, 2, 5 seconds |
| Positive range bias on one anchor | 0.25, 0.5, 1 m |
| Arrival delay | 50, 100, 250 ms |
| Residual IMU bias on a declared axis | 0.02, 0.05, 0.1 m/s^2 |
| Radar centroid displacement on a declared axis | 0.25, 0.5, 1 m |

Choose episode locations from eligible intervals using fixed seeds. Skip and record episodes that do not fit.

Keep acquisition and arrival time separate. Process delays in arrival order and explicitly reject stale updates. Restoring acquisition order is not online delay robustness.

Injected conditions remain labelled synthetic. They do not establish actual NLOS frequencies or raw RF behaviour.

### Outcomes

Primary endpoint: macro-average final-trajectory 3D position RMSE for M7 versus M2, with both trajectories shown individually.

Evaluate at 20 Hz using causal state predictions and evaluation-only reference interpolation. Apply a common mask for unsupported reference gaps and report its coverage.

Secondary outcomes:

- Median and 95th-percentile position error.
- Availability and numerical failures.
- Outage error and recovery time.
- Observation rejection and association failures.
- Empirical position-uncertainty coverage.
- Processing time, peak memory and real-time factor on recorded hardware.

Preserve failed trajectories. Do not compute a favourable headline from remaining successful cases. Use descriptive paired differences rather than timestamp-level significance claims. Corruption seeds do not create independent environments.

Save all pre-gating innovations. Position references support position-normalised error and ellipsoid coverage, not full-state NEES. Test formal consistency first in matched Monte Carlo experiments with known states.

## 4. Application, interfaces and evidence

Use Python 3.12, NumPy, SciPy, scikit-learn for DBSCAN, Pydantic, h5py, Matplotlib, FastAPI/Uvicorn, React, TypeScript, Vite and Plotly.

One scientific engine serves the CLI, API, notebooks and viewer. The frontend has no independent estimator, scientific defaults or authoritative unit conversions.

Normalised measurements contain session, acquisition time, arrival time, modality, sensor ID, value, frame, validity and source-row identity. Estimator functions receive no reference tables.

Each completed run records data/version/hash provenance, resolved configuration, calibration/protocol hashes, source revision, dependencies/platform, states, full covariances, innovations, rejection reasons, radar associations, metrics and completion status.

Use chunked HDF5 for numerical/radar data, JSON for status/configuration/provenance and CSV for tables. Finalise atomically. Cancelled, interrupted or failed runs remain incomplete.

### User interface

1. Dataset and calibration inventory, clocks, units, frames, gaps, splits and evidence.
2. Experiment builder with presets, modality ablations, physical parameters, fault controls, run/cancel and progress.
3. Synchronous 3D/2D replay with errors, uncertainty, observations, innovations, IMU bias and radar associations.
4. Paired run comparison with mismatched protocols visibly identified.
5. Immutable frozen-study review with results, methods, papers, external checks and exports.

Show stationary radars and the moving gantry. Label rendering decimation. Metrics always use the full eligible data.

### Commands and API

    sensorfusion data fetch
    sensorfusion data audit
    sensorfusion calibrate
    sensorfusion experiment run
    sensorfusion study freeze
    sensorfusion study run
    sensorfusion report
    sensorfusion serve --open-browser

Expose registered catalog/configuration resolution, run creation/status/cancellation, windowed trajectories/diagnostics/radar frames, artefact downloads and frozen-study review.

Use one spawned numerical worker. Return a busy response for concurrent work. Cancellation is cooperative and does not turn partial results into completed results.

Bind to loopback and serve packaged local assets. Use registered IDs rather than arbitrary filesystem paths. After setup and data acquisition, computation and viewing work offline.

## 5. Adapted master-prompt workflow

### Wave 0: coordinator preflight

- Read the shared style, reference-library README, selected simplicity guidance and existing target instructions if present.
- Keep the reference library read-only. Read only references relevant to the current role.
- Check whether the target exists and inspect Git status before modifying existing work.
- For a genuinely new repository, create a local bootstrap commit. Label it as a scaffold baseline, not preserved application behaviour.
- Work on implement/2026-10-29. Do not push or publish.
- Add ignored .refine/ working records and keep scientific methods/protocols tracked.

Use .refine/LEDGER.md, MAP.md, RULES.md, BASELINE.md, PLAN.md, INSTALLS.md and REPORT.md. Track one item per wave and per milestone.

Ledger entries use: Ruling: decision, because reason. Cost if wrong: consequence.

### Wave 1: map and protect invariants

Combine cartography and invariant discovery for the new project. Record interfaces, data flow, dependencies, setup/run/test commands and a Mermaid diagram.

The invariant register uses Given/When/Then acceptance statements for units, dimensions, timing, observation geometry, allowed calibration data, truth isolation, numerical covariance, frozen outputs, lifecycle and provenance.

For an empty repository, historical coverage, behaviour and before/after scanner improvements are not applicable. Establish the first executable baseline after the scaffold and meaningful tests exist.

### Wave 2: acceptance tests and independent design critique

- Treat approved milestones as IMPLEMENT tasks.
- Use separate author and critic passes for estimation, calibration, study design and application lifecycle.
- Define public contracts and meaningful acceptance tests before implementing each behaviour.
- A failing test should expose a missing/wrong observable behaviour, not merely a missing dependency.
- Apply characterisation tests later only when changing already working behaviour.
- Record task IDs, owned files, proving commands, dependencies and review status.

No additional checkpoint is required for the already approved scope. Escalate only a material scope change or an action requiring separate authorisation.

### Wave 3: implementation loop

1. Implement the smallest clear change for one task.
2. Execute its acceptance check.
3. Format and lint touched files.
4. Simplify touched code without obscuring equations or weakening invariants.
5. Obtain fresh review from someone who did not author the change.
6. Reproduce confirmed findings, fix their cause and retain meaningful regression evidence.
7. Integrate, run the relevant suite and commit one logical local change.

Use at most four concurrent agents including the coordinator. Give writers disjoint file ownership and use worktree isolation where useful. Agents receive self-contained briefs, relevant reference paths, owned files and the command that proves completion.

Reports stay concise: files changed, commands and actual outcomes, rulings and unresolved issues. Another agent's assertion is not executable proof.

After repeated failed attempts, stop speculative changes, isolate the cause and review the approach. Do not automatically delete progress, revert scientific findings or erase outputs after an arbitrary retry count.

### Wave 4: verification

Required tooling:

- Python: Ruff formatting/lint, mypy, pytest and coverage.
- TypeScript/React: Biome, TypeScript checks, component tests and browser workflows.
- Dependency locks and a modest Windows/Linux CPU CI matrix.
- Dead-code and complexity reports reviewed manually.

Keep the plan's mypy choice rather than adding a second type checker. Add property tests only for natural properties such as causality or covariance identities.

Use complexity 15 as a review trigger, not an instruction to fragment readable mathematics. Review unused-code reports before deleting public entry points.

Heuristic scanners are optional diagnostic inputs. Do not install every listed tool, optimise for a cosmetic score or require net code shrinkage from new implementation.

### Wave 5: reproducibility and documentation

- Use project-local environments and pinned dependencies.
- Check current documentation before relying on an uncertain library API; record the URL in the ledger.
- Read unfamiliar scripts before executing them.
- Set PYTHONUTF8=1 for Windows Python tooling.
- Record each installation and its reversal command in INSTALLS.md.
- Preserve line endings; introduce a deliberate .gitattributes policy for the new repository.
- Keep GPU/PyTorch/CUDA out of this CPU-based project unless a later authorised task needs them.
- Do not automatically expand the reference library or install system-wide tooling.

Write README, architecture/derivation/calibration documents, a ranked roadmap and project AGENTS.md. CLAUDE.md may forward to AGENTS.md with the single line @AGENTS.md.

The README opens with the problem and an actual screenshot or executed result. State incomplete studies clearly rather than inventing numbers. Include copyable setup/run/test/lint commands, the data-flow diagram, results, limitations and licensing.

Rank five to ten roadmap items by value over effort. Once implementation exists, identify each item's real extension point and proving test.

### Wave 6: fresh verification and report

Install from a fresh local checkout or wheel and follow the README. Verify packaged frontend assets and record which Windows/Linux checks actually ran. Configured CI is not passed CI.

REPORT.md contains actual baseline/current measurements where meaningful, task/module verdicts, commands and outcomes, unresolved findings, deliberate exclusions, installation reversals and next steps.

Preserve data-access failures and negative findings in the report. Do not turn an unavailable dataset into an apparent passed integration.

## 6. Milestones and acceptance gates

| ID | Deliverable and acceptance evidence | Dependencies | Estimated focused hours |
|---|---|---|---:|
| P0 | Target preflight, local branch, ledger, invariant register and owned task map | None | Included below |
| P1 | Primary/external access, actual schema/frame/clock audit, hashes and licensing | P0 | 20-30 |
| P2 | Derivations and independently checked mathematical reference cases | P0 | 16-28 |
| P3 | WLS/CV baselines, causal initialisation and visible geometry failures | P1, P2 | 18-28 |
| P4 | Valid inertial calibration, bias model and ablation | P1-P3 | 20-35 |
| P5 | Three radar calibrations/association paths, AOP fusion and substitutions | P1-P3 | 20-35 |
| P6 | Frozen selection, splits, provenance and eight-method harness | P3-P5 | 20-30 |
| P7 | Original/fault studies and external MILUV shared-subset results | P6 | 18-28 |
| P8 | Local execution, cancellation, replay, comparison and exports | Stable P3/P6 contracts | 30-45 |
| P9 | Scientific report, fresh-install/platform checks and external review | P7, P8 | 18-30 |

Base estimate remains approximately 180-290 focused hours plus prerequisite learning and calibration contingencies. No deadline is assumed. Review/quality work is part of the milestones, not a second complete implementation.

### Required executable checks

- Hand-computed coordinate, offset, range and unit fixtures.
- Analytical linear filtering and known-motion synthetic cases.
- Finite-difference Jacobians and independent continuous/discrete covariance checks.
- Proper rotation recovery for two noncollinear directions and rank-one rejection.
- Covariance symmetry/PSD, irregular timing and prediction through gaps.
- Causal-prefix replay and explicit delayed-arrival handling.
- Reference-isolation and MILUV forbidden-column tests.
- Radar clutter, ambiguity, missing target and duplicated-return tests.
- Gating lockout, valid-measurement rejection and sensor-return cases.
- Frozen-protocol/hash integrity and truthful incomplete-run lifecycle.
- Browser execution, cancellation, replay, comparison, failures and exports.
- Fresh wheel installation with local frontend assets.
- Windows/Linux fixture checks and accurately identified full-study platforms.

Formatting, lint and types must pass. Coverage is used to find missed behaviours; greenfield improvement cannot be expressed as beating a nonexistent historical baseline. Suppressions need specific reasons.

Do not relax numeric checks merely to make a platform pass. Use stated tolerances and explain platform/BLAS differences rather than promising cross-platform byte identity.

### Definition of done

- Valid real-data integration and component ablations for all four sensor classes.
- Immutable source/protocol/run provenance and reproducible principal figures.
- Original-data, fault and external results clearly distinguished.
- Operational local app, installed package and documented commands.
- Derivations, calibration report, scientific report and limitations.
- A five-minute walkthrough explaining one successful case and one failure.
- Recorded feedback from a knowledgeable external reviewer.
- Evidence that another person reproduced the principal comparison.

External review and independent reproduction cannot be self-certified. Prepare their materials and record them as pending until they occur. Do not contact other people without the user's explicit authorisation.

A performance improvement is not required. A reproducible negative result is acceptable. Primary conclusions remain limited to the controlled gantry laboratory; external conclusions remain limited to the shared range subsystem.

## 7. What was adopted and what was adapted

Adopted from the master prompt: local reviewable work, a ledger, explicit task ownership, invariant protection, test-first scientific behaviour, separate author/reviewer passes, selective simplification, project-local tooling, British prose, fresh-install checks, CI configuration, documentation and a measured final report.

Adapted for this project:

- Implement replaces the template's refine default.
- A bootstrap baseline replaces claims about pre-existing behaviour.
- Scientific validation replaces cosmetic scanner-score targets.
- Mathematical readability limits automatic complexity splitting.
- The scoped toolchain replaces blanket installation of all listed scanners.
- CPU execution replaces an unverified GPU/PyTorch assumption.
- Root-cause review replaces automatic deletion/reversion after three attempts.
- Immutable negative findings replace any pressure to make every experiment improve.
- External review is an explicit pending human deliverable, not an agent's self-issued approval.

This revision changes the implementation workflow without reducing the approved scientific or application scope.
