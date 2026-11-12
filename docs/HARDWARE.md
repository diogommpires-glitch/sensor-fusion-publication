# Existing-hardware range study

The recording, calibration and protected-evaluation software is implemented.
Physical verification and H1/H2/H3 acquisition remain pending. No serial port was
opened and no firmware was flashed during implementation. A read-only Windows
serial-device query returned no device identities. Documented support in Dodo
does not establish that a particular device is connected or produces valid range.

## Capabilities and limits

The inspected Dodo bridge targets MR60BHA2 radars on XIAO ESP32C6 boards. Its
Python header describes old stubbed distance behaviour while the sketch calls
`getDistance()` and emits a latched `dist` with `age_dist_ms`. Its version banner
also differs from its header. These contradictions require physical firmware and
freshness verification. Existing source comments are evidence to investigate,
not automatic capability certification.

[Seeed's sensor guide](https://wiki.seeedstudio.com/getting_started_with_mr60bha2_mmwave_kit/)
describes processed distance outputs. [Its reporting documentation](https://wiki.seeedstudio.com/ha_with_mr60bha2/)
describes closest-target selection and held distance outside range. This study
uses processed one-dimensional ranges independently for devices A and B. It does
not claim raw radar returns, a 3D trajectory or independent views of one exact
scattering point. No purchases or firmware changes are part of this workflow.

## Declarations before recording

`register_capability` validates a declaration containing device A/B, model,
firmware, physical-verification confirmation, evidence and freshness semantics.
The application labels this as an operator declaration, not independent human
review or automatic device identification. Current connection state remains
unknown until an operator observes it. Synthetic fixture capabilities remain
explicitly labelled and cannot establish physical-study completion.

Sequence freshness must mean a new **range measurement**, not another serial
message. Device-age freshness requires a measured host/age jitter tolerance and
verified reset-on-new-range behaviour. Equal consecutive distances can be fresh;
repeated receipts do not establish freshness. Unknown freshness is stored for
inspection but never becomes a measurement update. A device-age-derived update
epoch is labelled a host-clock proxy, and acquisition time remains null.

The serial reader uses a finite timeout and configures DTR/RTS before opening.
Some drivers can still toggle control lines on open; confirm the board's reset
behaviour before acquisition. There is no automatic scan, flashing or reconnect
that silently continues an interrupted experimental recording.

## Trial protocol

The [registered design](../protocols/hardware-study-v2.json) specifies 87 trial
conditions per device, with independently shuffled order from seed 1729. H1 is
development, H2 selects parameters and H3 is protected evaluation. Each session
has 24 present-target trials: four nominal distances of 0.5, 1.0, 1.5 and 2.0 m,
clear/passive-clutter surroundings and three independent repositions. Five
target-absent trials complete each 29-trial session.

Each recording lasts 40 s; ten seconds settle and thirty seconds are evaluated.
For two devices these are two separate 87-record channel sets. Record the same
condition on each device without assuming independent scattering-point errors.

The reference template deliberately leaves measurements, instrument and
uncertainty empty. An operator must supply consent, mounting geometry and torso
plane measurements before and after each present-target trial. Start/end
disagreement beyond twice the declared uncertainty bound blocks accuracy
evaluation. Missing references also block it. Report discrepancy from that
defined plane with target extent and instrument limitations, not exact radar
ground-truth accuracy. Absent trials have no invented reference position.

The recorder persists only allowlisted range, presence, sequence, age and clock
fields. HR, breathing, phase signals, faces and unknown fields are discarded
before persistence. Invalid payload contents are not copied into errors or logs.
Consent applies to these new local positioning trials. Dodo recordings are not
imported, copied or used as test fixtures.

## Lifecycle and interfaces

The CLI exposes hardware catalogue, schedule, capability registration, reference
template, recording, calibration, freezing, evaluation and replay commands. The
shared backend interfaces live in `sensorfusion.hardware`; the frontend performs
no estimation or scientific unit conversion.

`record_serial` validates consent, capability identity and protected-session
admission before opening the explicitly selected port. Cancellation, Ctrl+C,
disconnect or a short stream leaves incomplete evidence. A retry receives a new
attempt identifier; the earlier evidence remains intact. A completed trial cannot
be recorded again under a more favourable attempt. Every completed record has a
metadata/observation checksum manifest. Replay refuses incomplete or changed
records.

Calibration requires all 29 scheduled H1 and H2 records for each declared device.
Only present-target fresh measurements fit the constant H1 offset and positive
residual variance. H2 chooses density from 0.01, 0.1 and 1 m²/s³, breaking ties by
the smaller value. D1 uses the offset-corrected fresh measurement without temporal
filtering. D2 uses the causal CV filter and D3 uses the same selected density with
a 0.999 gate and no automatic reset. The filter reuses the scientific core's
discretisation and Joseph update.

Primary RMSE and nominal coverage use common fresh-event support. Receipt-time
prediction RMSE and availability are secondary because D1 has no estimate at held
receipts. Reference uncertainty is an explicit bound, not a fabricated Gaussian
variance. The saved filter diagnostics include full state/covariance, innovations
before gating and rejection reasons. All processing is causal.

Freeze binds source, development observations, calibration and exact capability
content before H3 recording. It rejects changed calibration inputs and reusing
already acquired H3 data as a new protected study. H3 evaluation requires all 29
records and may execute once. Target-absent trials report fresh positive ranges,
not localisation error. Two devices remain independently reported.

## Verification status

Synthetic fixtures exercise the complete H1/H2/freeze/H3 pipeline, packet privacy,
freshness, cancellation, retries, reference gates, capability drift, checksums,
causality and an independently hand-calculated linear update. They validate
software behaviour only. External human review and physical recordings are
separate requirements and remain pending.
