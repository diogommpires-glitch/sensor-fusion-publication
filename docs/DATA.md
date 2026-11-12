# Actual source data and adapter rulings

The five selected source archives were downloaded on 29 October 2026 with normal published HTTPS endpoints. All published byte sizes and MD5 checksums matched; SHA-256 fingerprints were computed locally. Actual CSV payloads were inspected and audited. This replaces the earlier planning-stage HTTP 403 limitation. Initial sandbox requests failed DNS resolution; authorised normal network access succeeded. No access challenge was bypassed.

The runtime workspace is `workspace/` by default. Archives are in `workspace/data/raw/{dataset}/`, access manifests in `workspace/data/access/`, audits in `workspace/data/audits/` and immutable, content-identified calibration records in `workspace/calibrations/`. Archives remain compressed. Readers open only required CSV members, including MILUV `ifo001/uwb_range.csv` and `ifo001/mocap.csv`; images are not extracted. Raw data is excluded from Git. Downloads stream through a temporary file, verify its published size/MD5, compute SHA-256 and publish without replacing an existing archive. Existing corrupt archives fail verification and are preserved.

| Dataset | Pinned source | Selected files | Licence and attribution |
| --- | --- | --- | --- |
| RoboLoc-G | [Zenodo v2, DOI 10.5281/zenodo.15989282](https://zenodo.org/records/15989282) | `SENSORS_MEASUREMENTS_CSV.zip`, `GROUND_TRUTH.zip`, `GANTRY_LINUX_CNC.zip` | CC BY 4.0; Domínguez-Bolaño, Carro Lagoa, Barral Vales, Escudero and García-Naya, Universidade da Coruña (2025). The Zenodo API identifies `cc-by-4.0`; the [university record](https://portalinvestigacion.udc.gal/documentos/688b601017bb6239d2d4811a?lang=en) also supplies attribution. |
| MILUV | [Figshare v1, DOI 10.25452/figshare.plus.28386041.v1](https://doi.org/10.25452/figshare.plus.28386041.v1) | `default_1_random3_0.zip`, `default_1_random3_2.zip` | CC BY 4.0 from the [version-specific metadata](https://api.figshare.com/v2/articles/28386041/versions/1); Shalaby, Ahmed, Dahdah, Cossette, Le Ny and Forbes (2025). |

`evidence/data_access.json` contains retrieval evidence, exact archive fingerprints and the current gate result. These records establish acquisition and actual schema checks, not completion of the full fusion study.

## RoboLoc-G v2: actual payload overrides contradicted prose

The target is the gantry origin, `Grua`, in gantry coordinates. Positions in this laboratory have positive x/y and negative z. Reference tables are kept in `Session.reference_times` and `reference_positions`, separate from estimator observations.

| Field | Actual representation and boundary conversion | Evidence |
| --- | --- | --- |
| Sensor `timestamp` | Integer Unix nanoseconds, divide by 1e9 | Actual CSV values around `1751476324874575521`; associated ROS `header.stamp` seconds/nanoseconds agree within recorded receipt latency. |
| Reference `time` | Floating Unix seconds | Actual GT time around `1751476321.6560955`. |
| GT `Grua.X/Y/Z` | Millimetres, divide by 1000 | Actual still reference `[2500,2500,-800]`, command `still_gantry.txt` and moving commands explicitly using `G21`. |
| UWB `range` | Millimetres, divide by 1000 | Actual still measurements 3080 to 3886, matching metre-scale laboratory geometry after conversion. The [manufacturer's native ranging documentation](https://pozyxlabs.atlassian.net/wiki/spaces/CD/pages/1189118270/Tutorial%2B1%2BReady%2Bto%2Brange%2BPython) also defines millimetres. Zenodo's CSV prose calls this field metres; that statement is contradicted and is not applied. |
| UWB `anchor_id` | Decimal integer converted to the published hexadecimal ID | `26714` becomes `UWB_685A`; the selected moving tag has `tag_id=0`. Unknown IDs fail ingestion. |
| FTM `dist_est` | Metres | Positive finite raw values retained. Range error can be large. |
| Infrastructure positions | Published centimetres, divide by 100 | The version-pinned anchor table is encoded explicitly in the adapter. |
| Tag mounting offsets | Published millimetres, divide by 1000 | UWB `[-0.24019,0.11768,-0.06812]` m; FTM `[-0.24802,-0.10431,-0.07511]` m. |
| IMU acceleration and gyro | m/s² and rad/s | Published schema plus actual acceleration quantisation and stationary behaviour; no additional 9.80665 m/s² subtraction. |
| Radar polar returns | Metres, radians, Doppler m/s | Actual `returns.range/azimuth/elevation/doppler_velocity` fields exist on all three radars. |

FTM repeats one transaction-level `dist_est` on several packet-frame rows. The adapter collapses by `(timestamp, anchor_id)` and rejects contradictory values within a transaction. It does not divide the covariance by the number of repeated frames. Raw receive power remains available in source CSVs for diagnostics; no NLOS classifier is inferred.

The four payload MACs differ from the infrastructure labels. The explicit adapter aliases are `7c:df:a1:05:87:c7 → FTM_87C6`, `7c:df:a1:06:b9:dd → FTM_B9DC`, `7c:df:a1:0e:db:f1 → FTM_DBF0` and `7c:df:a1:0f:74:e9 → FTM_74E8`. This is a version-specific table, not a generic MAC subtraction rule. All 24 assignments were compared on zigzag1/zigzag2 geometry after removing each radio's mean residual. This assignment uniquely minimised the summed mean squared centred residual, 54.24 m² versus 58.13 m² for the runner-up. The suffix pattern corroborates it; firmware-level AP/STA identity was not established. The modest separation and poor FTM noise remain limitations.

Radio acquisition timestamps are unavailable separately. Their CSV timestamps serve as arrival and declared acquisition proxies. IMU and radar use ROS header time as acquisition and CSV timestamp as arrival. An additional timing shift is not inferred from held-out accuracy. Published NTP synchronisation is retained. The published +16 ms correction applies to OptiTrack measurements, not the supplied gantry reference; it is not applied again.

The still recording contains about 122.7 s of sensor data but only 11.24 s of reference support. Calibration uses their overlap and does not extrapolate reference. Moving reference gaps above 0.5 s are excluded from calibration. The still reference's two identical endpoints describe a documented static position, so its 11.24 s interval is supported as constant.

Reference ingestion preserves finite-value checks and source order. Exactly identical duplicate target positions at one timestamp collapse to the first row. If any position in a timestamp group conflicts, every member of that group is excluded. Values are not averaged or selected by agreement with a sensor. Decreasing timestamps still fail ingestion; records are not sorted. The evaluator's existing gap rule determines whether the remaining bracket supports interpolation. The policy, counts and excluded positions/times appear in each session's `reference_normalisation` metadata and in reference audit entries.

The actual validation session `zigzag3` contains a conflicting group at Unix time `1751474938.2291045`, CSV lines 3/4 including the header: `[2.5,2.5,-0.8]` and `[2.399602606831791,2.399714206822863,-0.8]` m. Both rows are quarantined, giving 1196 supported records from 1198 raw records. The surrounding initial bracket is 4.3661494255 s and remains unsupported for the evaluator's 0.105 s gap limit. The conflict occurs 0.73013 s after the first radio timestamp, so no zero-effect claim is made. All other primary sessions are unchanged; the three development arrays remain byte-for-array identical and no calibration is refitted. The source archive is unchanged. Exact before/after counts, excluded positions, hashes and executable checks are in `evidence/reference_normalisation.json`.

## Development calibration and its limits

Only `still`, `zigzag1` and `zigzag2` references enter primary calibration. `zigzag3`, `zigzag4`, `random1` remain validation; `circle` and `random2` remain final. Source archive hashing may read final archive bytes, but calibration does not parse final reference rows or use final estimator errors.

Per-anchor radio calibration records raw-minus-geometric mean bias, sample residual variance and lag-one correlation. Actual UWB biases are approximately -0.061 to +0.044 m, with variances 0.0094 to 0.0444 m². FTM biases are approximately 5.52 to 7.15 m, with variances 10.25 to 18.15 m². Those large values are retained. Subtracting a mean bias may produce a negative Gaussian measurement; such corrected observations are not censored. Nonpositive or nonfinite raw physical ranges are excluded before calibration.

The IMU stationary mean is removed as a combined constant contribution. Its near-zero norm, approximately 0.035 m/s², supports the inference that upstream `linear_acceleration` already excludes gravity. It does not identify physical sensor bias and gravity separately. The apparatus is a translating fixed mount; development quaternion excursions are at most about 1.17°, with near-unit quaternion norms. Gyro spikes and vibration are recorded rather than hidden.

Reference acceleration uses a fixed 10 Hz interpolation, an 11-sample cubic Savitzky-Golay second derivative, and removal of 0.6 s edges and gaps above 0.5 s. The proper zero-translation rotation has rank-two excitation and determinant +1. There are 24 stationary overlap samples and 557 moving calibration pairs. Actual residual acceleration RMSE is about 0.788 m/s². This high residual does not establish benefit from inertial fusion; the measured covariance is preserved.

Acceleration sample covariance has units m²/s⁴. The scalar process density stored as `acceleration_noise_density_m2_s3` is `trace(residual_covariance)/3 * median_sample_interval`, approximately 0.07248 m²/s³ with a 0.34957 s interval. This is an effective white-noise approximation that includes model/differentiation error. Residual temporal correlations are recorded, so formal whiteness is not claimed. Original acquisition times are frozen with zero added timing correction; an exploratory development lag sweep found a flat objective and did not identify a defensible extra shift.

## Radar gate and review artefacts

Native Cartesian conversion is `x=r*sin(az)*cos(el)`, `y=r*cos(az)*cos(el)`, `z=r*sin(el)`. It was checked against the ISK `cloud.csv` export; interpreting raw azimuth with an x-forward convention would swap axes. Native points are not already in the gantry frame.

The implementation selects 30 uniformly spaced, reference-supported development scans per radar, stores exact source rows and native points, and assigns 20 fit and 10 check roles. It evaluates the nine declared DBSCAN combinations: radii {0.1,0.2,0.4} m and minimum distinct returns {3,5,10}. The bounded correspondence search uses seed 123, at most 3000 proper three-pair Kabsch proposals per combination and ten inlier refits. Only the 20 fit references select a transform and clustering setting: maximise fit support within 0.6 m, then minimise trimmed fit squared residual. The ten check references are assessed once after selection. This process creates machine annotations, not human labels, and never accesses final truth.

Every selected scan receives a positive, ambiguous or no-supported-target label. Missing targets are valid negative annotations and remain in the coverage denominator. The engineering calibration gates require a proper rank-two rotation, at least six supported fit correspondences across both moving development sessions, at least three uniquely gated check correspondences, finite positive-definite leave-one-out fit residual covariance and independent annotation review. These are declared integration gates, not accuracy or full-field-of-view guarantees. The fit gate is 0.6 m; check annotation uses a fixed unique 1 m gate. Covariance uses leave-one-out fit errors and is not divided by return count.

| Radar | Selected clustering | Supported fit / 20 | Unique check / 10 | Check scans with any cluster / 10 | RMSE of nearest candidate on all nonempty check scans |
| --- | --- | --- | --- | --- | --- |
| AOP | 0.2 m, 3 distinct returns | 9 | 5 | 8 | 0.687 m |
| ISK0 | 0.4 m, 3 distinct returns | 15 | 6 | 10 | 1.042 m |
| ISK1 | 0.4 m, 3 distinct returns | 17 | 6 | 9 | 0.757 m |

Those errors include ambiguous and outside-gate cases whenever a cluster exists. Conditional accepted-target errors are stored separately and cannot substitute for coverage or unconditional performance. Actual sparse target visibility, particularly at the edges of AOP's field of view, remains a limitation. Independent review of the three 30-panel plots and machine-labelled JSON is recorded by a separate hash-bound review record before enabling radar observations. Review artefacts are under `workspace/data/annotations/roboloc-g-candidates-8ace4c9b1176-*`. Previous failed all-frame correspondence fits are preserved as provisional records, rather than overwritten or relabelled successful.

`accept_radar_review(workspace, calibration_id, review_path)` checks an explicit `agent-scientific-review` record with reviewer identity, review time, checks, an approved verdict, per-radar approvals and SHA-256 values for both inspected calibration and annotation manifest. It creates a new calibration with its parent ID and review-file hash, preserving the old file. It cannot approve a radar whose engineering gates failed. This is internal agent scientific review; external human review remains explicitly pending.

When a radar has a certified effective transform, its full cloud becomes a world-frame observation with empirical 3×3 centroid covariance. The numerical engine clusters and associates from its causal predicted state. Data ingestion never associates a final target from truth. Covariance is not reduced by correlated point count.

## MILUV shared range subset

Development is `default_1_random3_0`; protected external evaluation is `default_1_random3_2`. Select robot `ifo001`, physical tag 10 and anchors 0 through 5. Only `timestamp`, `from_id`, `to_id`, `range_raw` enter observation construction. Other columns, including calibrated `range`, `gt_range`, embedded poses/quaternions, `bias`, `bias_raw` and reported `std`, are ignored. Reversing the tag/anchor transaction direction is supported; other tag pairs are excluded.

Actual timestamps are relative seconds and raw ranges are metres. The official [anchor configuration](https://github.com/decargroup/miluv/blob/399ad46f4e6e88c3c98b40aae33fe8e2c6719cff/config/uwb/anchors.yaml), [experiment assignments](https://github.com/decargroup/miluv/blob/399ad46f4e6e88c3c98b40aae33fe8e2c6719cff/config/experiments.csv) and [tag lever arms](https://github.com/decargroup/miluv/blob/399ad46f4e6e88c3c98b40aae33fe8e2c6719cff/config/uwb/tags.yaml) are pinned at revision `399ad46f4e6e88c3c98b40aae33fe8e2c6719cff`. Session 0 uses anchor constellation 0; session 2 uses constellation 2.

The evaluator constructs tag position as motion-capture body position plus `R(q)*[0.13189,-0.17245,-0.05249]` m. Quaternion order is xyzw. This reference never enters the estimator input. Radio calibration uses session 0 only. The check is calibrated portability of M1/M2 on a shared radio subset; it does not externally validate FTM, the constrained IMU or radar.

## Executed checks

On Windows with project-local Python 3.12, the authored CSV tests verify millimetre/nanosecond conversions, FTM transaction collapse and conflict rejection, strict MILUV truth-column isolation, the rotated evaluation lever arm, gantry-origin reference selection, blocked missing sources, safe calibration IDs and source corruption rejection. They contain authored fixtures and are not real-data study tests.

Run `.venv/Scripts/python.exe -m pytest tests/test_data.py -q --basetemp workspace/pytest-data`, `.venv/Scripts/python.exe -m ruff check src/sensorfusion/data.py src/sensorfusion/sources.py tests/test_data.py` and `.venv/Scripts/python.exe -m mypy src/sensorfusion/data.py src/sensorfusion/sources.py tests/test_data.py`. Real source audit and calibration are separate executed commands recorded in the evidence file. Full study, independent human review and independent reproduction are not self-certified by these checks.
