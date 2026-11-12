# Models, units and executable checks

The estimator has no reference trajectory argument. Reference positions or
accelerations appear only in development calibration and evaluation. The equations
below derive the implemented models; they are not claims of real-data accuracy.

## Coordinates and observations

Positions and fixed mounting offsets are world-frame metres, times are seconds,
velocities are metres per second and accelerations are metres per second squared.
The fixed-attitude gantry model absorbs the attitude into each supplied world
offset `d`. A calibrated range to anchor `a` observes

\[
h(x)=\|p+d-a\|,\qquad H=[(p+d-a)^T/\|p+d-a\|,0,\ldots,0].
\]

Radio calibration subtracts a development-only measured-minus-geometric offset
before engine input. Bias-corrected values can be negative under the additive
noise model and are retained; raw physical positivity is an ingestion check.
Censoring corrected negative values would change the observation distribution.
Slant range, height and the tag lever arm remain in the
equation. A sensor exactly at an anchor has no unique range derivative; the engine
records `undefined_range` rather than selecting an arbitrary direction.

`test_true_3d_range_jacobian_matches_finite_difference` independently perturbs all
nine state coordinates. The WLS fixture separately tests a nonzero 3D tag offset.

## Constant velocity

For `x=[p,v]`, the continuous dynamics are `dp=v dt`, `dv=sqrt(q_a) dW`.
The acceleration spectral density `q_a` has units m²/s³, distinct from the sample
variance of an acceleration measurement. Integrating the white acceleration gives

\[
F=\begin{bmatrix}I&\Delta t I\\0&I\end{bmatrix},\qquad
Q=q_a\begin{bmatrix}\Delta t^3I/3&\Delta t^2I/2\\
\Delta t^2I/2&\Delta t I\end{bmatrix}.
\]

The block units are m², m²/s and m²/s². The hand-computed two-second fixture uses
`q_a=3`, giving one-axis covariance `[[8,6],[6,6]]`.

## Fixed-attitude inertial model

For `x=[p,v,b]`, the residual bias `b` is in the world frame. The stationary IMU
mean is removed during calibration. This identifies a combined stationary offset;
it does not identify physical gravity and sensor bias separately.

\[
\dot p=v,\quad \dot v=u-b+w_a,\quad \dot b=w_b,\qquad
A=\begin{bmatrix}0&I&0\\0&0&-I\\0&0&0\end{bmatrix}.
\]

For held control `u`, the exact transition and control integral are

\[
F=\begin{bmatrix}I&tI&-t^2I/2\\0&I&-tI\\0&0&I\end{bmatrix},\qquad
B=\begin{bmatrix}t^2I/2\\tI\\0\end{bmatrix}.
\]

Let `q_a` have units m²/s³ and bias diffusion `q_b` have units m²/s⁵. From
`Q=integral_0^t exp(A s) diag(0,q_a I,q_b I) exp(A.T s) ds`,

\[
Q=\begin{bmatrix}
(q_at^3/3+q_bt^5/20)I&(q_at^2/2+q_bt^4/8)I&-q_bt^3I/6\\
(q_at^2/2+q_bt^4/8)I&(q_at+q_bt^3/3)I&-q_bt^2I/2\\
-q_bt^3I/6&-q_bt^2I/2&q_btI
\end{bmatrix}.
\]

The negative position/bias and velocity/bias blocks follow from `dv=-b dt`.
`test_inertial_covariance_matches_independent_van_loan` builds a separate 18-by-18
matrix exponential, extracts its covariance and compares every element. It does
not call the analytical formula to construct its expected result.

The previous available IMU control propagates each interval. A newly arrived
control is installed after propagation to its acquisition epoch. Before the first
control the input is zero and process uncertainty still grows. `acceleration_noise`
is the resolved continuous noise density. Per-sample IMU covariance is validated
and retained in the input provenance; it is not repeatedly injected as independent
white noise, which would incorrectly ignore correlation from holding one sample.
The current model does not explicitly augment held-sample measurement errors.

## Updates and diagnostics

For residual `r=z-h(x)`, `S=H P H.T+R`, `K=P H.T solve(S,I)`, the implemented
posterior covariance is Joseph's expression

\[
P^+=(I-KH)P(I-KH)^T+KRK^T.
\]

Only linear solves are used. Round-off asymmetry is averaged; negative eigenvalues
are never clipped. Material asymmetry, nonfinite entries or an invalid covariance
raise `NumericalError` and fail the run. Numerical PSD checks tolerate relative
round-off of 1e-10, with an absolute floor at that scale. Observation and innovation
covariances must be strictly positive definite.

NIS is `r.T solve(S,r)`. M7 and M8 compare it against the configured chi-square
quantile with the actual observation dimension: one for radio, three for radar.
Every attempted measurement retains a diagnostic including acquisition/arrival,
sensor, dimension, value, prediction, residual, NIS and rejection reason. A missing
or ambiguous radar association has no measurement innovation, so its numeric fields
are null. Initialisation and stale observations likewise have no invented prior
innovation. Accepted-only residuals cannot establish model consistency. NIS needs no
true state under its model assumptions, as explained by
[Piché, Online tests of Kalman filter consistency](https://trepo.tuni.fi/bitstream/handle/10024/212922/KFconsistency2015.pdf?sequence=-1).

The analytical Kalman fixture checks the entire posterior, not merely a finite
result. The lockout fixture deliberately supplies valid measurements after a large
unmodelled displacement: robust gates keep rejecting them and no reset occurs.
This is a retained failure mode, not a promise that gating always helps.

## Initialisation and eight methods

All EKFs start at the first geometrically valid causal UWB WLS fit. Latest ranges
from distinct sensors are retained only inside the configured bounded window.
Four sensors and rank-three effective-anchor geometry are required, avoiding the
unresolved reflection of coplanar range-only initialisation. WLS minimises
`sum((norm(p+d_i-a_i)-r_i)^2/variance_i)` and obtains local covariance from the
inverse weighted Jacobian information. It does not rescale known observation
noise by the fitted residual. Asynchronous WLS approximates the position as
constant inside the window; observation ages are recorded so the approximation
can be assessed.

Initial velocity is zero with covariance `I m²/s²`; inertial residual bias is zero
with covariance `0.1 I m²/s⁴`. Position covariance is the WLS covariance. Ranges used
for initialisation are not immediately reused in an EKF update. Before a valid
solution, states and full covariances are NaN and unavailability is explicit.
M1 also becomes unavailable after ranges expire.

| Method | Propagation | Measurement updates | Innovation gate |
|---|---|---|---|
| M1 | Independent UWB WLS | UWB | None |
| M2 | CV | UWB | None |
| M3 | CV | UWB, FTM | None |
| M4 | Inertial | UWB | None |
| M5 | Inertial | UWB, FTM | None |
| M6 | Inertial | UWB, FTM, selected radar | None |
| M7 | Inertial | UWB, FTM, selected radar | Dimension-specific |
| M8 | CV | UWB, FTM, selected radar | Dimension-specific |

The radar frontend's association gate is required for all radar methods, including
M6. `ungated` refers to the subsequent EKF innovation gate, not to indiscriminate
use of all radar detections.

## Causality and arrival delays

Events are stably ordered by arrival. Acquisition epochs older than the processed
acquisition watermark are explicitly rejected as stale. The engine does not undo
updates or restore acquisition ordering. A late but not stale sample can update
the current posterior, but cannot revise already emitted outputs. The regular
output grid is anchored to first arrival, with causal predictions at 20 Hz by
default. Predictions for display do not mutate the measurement posterior.
Extending a recording therefore cannot move the grid or change earlier states.
Events after the final regular grid point still receive diagnostics.

The prefix fixture compares overlapping output states exactly after extending the
input. A deliberately delayed range arrives after a more recent acquired range;
its diagnostic must say `stale`. This establishes the implemented online policy,
not optimal out-of-sequence filtering or robustness to arbitrary network delay.

## Calibration and radar frontend

Calibration primitives require `split='development'`; callers must additionally
enforce and record the dataset/session split. This label cannot prove provenance
by itself. Protected references never enter estimation.

Kabsch fits `target=R source+t`, with SVD of the cross-covariance and a determinant
correction so `det(R)=+1`. Two adequately excited noncollinear directions determine
a proper 3D rotation, including planar motion; rank-one excitation is rejected.
This follows the proper-rotation problem introduced by
[Kabsch, 1976](https://doi.org/10.1107/S0567739476001873).
The IMU fit removes the stationary mean and fits rotation with zero translation.
Radar fits a full effective centroid-to-origin rigid transformation and empirical
3-by-3 residual covariance. This does not separately identify a physical radar
translation and fixed mannequin scattering offset. In-sample covariance alone is
not a held-out calibration check.

For the inspected RoboLoc-G radar convention, native Cartesian points are
`x=r sin(azimuth) cos(elevation)`, `y=r cos(azimuth) cos(elevation)`,
`z=r sin(elevation)`. Angles are radians and range is slant metres. The native
frame still requires the frozen effective transformation to world coordinates.

DBSCAN runs separately per scan. Exact duplicated returns are removed before
clustering and cannot count as additional independent support. Association uses
only the causal predicted position and covariance. Exactly one cluster must lie
inside the configured three-dimensional gate; no cluster or ambiguity gives no
update. A selected scan supplies at most one centroid, with the calibrated full
covariance. The covariance is never divided by return count. AOP, ISK0 and ISK1
are separate substitutions selected by configuration, never jointly fused as
independent sensors. Doppler and vendor tracks do not update the state.

Clustering fixtures include clutter, two plausible targets, no target and duplicated
returns. An engine fixture verifies one update per raw scan and no truth-based
selection. Frozen development scan annotations and held-out calibration checks
remain data-study requirements; mathematical fixtures cannot certify them.

## Mathematical provenance

The continuous-discrete construction and Gaussian filtering context are described
in [Särkkä and Svensson, Bayesian Filtering and Smoothing, second edition, 2023](https://doi.org/10.1017/9781108917407).
The equations here are independently derived for this small fixed-attitude model.
Publisher metadata and the author-hosted NIS manuscript were accessible during
implementation; the book PDF exceeded the browsing size limit and the Kabsch DOI
full text was inaccessible. No uninspected paper is claimed to validate the code.
The complete project literature motivation and its limitations are recorded in
`IMPLEMENTATION_PLAN.md`.
