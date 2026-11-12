# Research portfolio upgrade

Approved scope: refine and implement, with the coding master prompt and shared
CODE_STYLE rules. Preserve v1 evidence. No equipment purchases or publication.
The equipment is not locally available, so physical verification and recordings
cannot be completed in this implementation session.

## Scientific work

Register eight exploratory conditions: CV or constrained inertial propagation,
AOP radar enabled or disabled, and innovation gating enabled or disabled. Every
condition uses UWB plus FTM. Fix all CV parameters to the v1 M3 selection and all
inertial parameters to the v1 M6 selection, without retuning. Reuse calibration,
initialisation, observation ordering and reference masks. All eight existing
RoboLoc-G sessions are exploratory because their references have been inspected.
Report all failures, whole-recording contrasts, interactions and the predeclared
0–20 s circle diagnostic window.

Run known-state ensembles with 1,000 seeds, 50 steps of 0.1 s, matched noise,
underestimated observation noise and correlated held-acceleration errors.
Independently verify the augmented covariance. Preserve undetected mismatch
results and distinguish simulations from real-data consistency.

## Existing hardware

Verify model, firmware, freshness and clocks before opening a physical recorder.
MR60BHA2 processed range is a separate one-dimensional observation, not a 3D
point cloud. Keep both devices independent. Store positioning fields only.

H1 development, H2 selection and H3 protected evaluation each contain 24 target
trials: four distances (0.5, 1.0, 1.5, 2.0 m), two surroundings conditions and
three repositioned repetitions, plus five absent-target trials. There are 87
trial conditions per device, ordered with seed 1729. Record 40 s, excluding the
first 10 s from scoring. Require consent and defensible before/after torso-plane
reference measurements with an uncertainty bound. If unavailable, accuracy is
blocked rather than inferred. Preserve raw discrepancies; compare calibrated
unfiltered range, 1D CV and gated 1D CV with no reset. Fit H1 and select density
from {0.01, 0.1, 1} m²/s³ on H2 before freezing H3.

## Implementation and acceptance

Use an archived, hash-verified v1 wheel and separate interpreter for historical
studies. Add registered investigation, hardware capture and replay commands,
read-only research views, explicit result kinds and incompatible-target labels.
Never overwrite old reports or weaken source/calibration/protocol checks.

Pin meaningful behaviours before changes, assign disjoint writers and obtain
independent review. Run formatter, lint, types, Windows/Linux fixtures, frontend
and browser checks, fresh setup and wheel checks. Record real commands and
limitations. Scanner metrics are evidence for review, not scientific acceptance.

Provide a scientific report, reproduction packet, five-minute demonstration and
ownership exercises. External human scientific feedback, another person's
reproduction, physical trials and the user's ownership exercises remain human
acceptance gates. Preparing software or agent reviews does not satisfy them.
