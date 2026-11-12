# Sensor Fusion

Read IMPLEMENTATION_PLAN.md. Follow CODE_STYLE_v1.md when available in your local
reference library.
For the research upgrade also read RESEARCH_UPGRADE_PLAN.md. All v2 investigations
on previously inspected recordings are exploratory. Execute historical studies
through the pinned v1 runtime; never bypass source hash checks.

Keep equations, units and provenance explicit. Scientific invariants outrank line
count and scanner scores. Use British prose without em dashes.

The estimator receives observations and configuration, never reference tables.
Calibration uses development sessions only. Validation selects fixed candidates.
Final results and source archives are immutable. Synthetic fixtures are labelled
and cannot establish real-data study completion.

Work on the local implementation branch. Do not push or publish. Writers own
disjoint files; obtain independent review for substantial changes.

Use Python 3.12 in .venv. Set PYTHONUTF8=1 on Windows. Run pytest, Ruff and mypy
for the Python change; frontend work also runs TypeScript, Biome and browser tests.
Report exact commands, platforms, outcomes and unresolved limitations.
