# Workbench design

The application is a local scientific tool for inspecting estimators and their evidence. Its primary object is a saved experiment linked to a shared time cursor, trajectory views and pre-gating diagnostics.

The five views are datasets and calibration, experiment builder, replay and diagnostics, run comparison, and frozen study and methods. The shell uses a top bar and horizontal navigation. Scientific controls remain inline or in labelled disclosures. Draft configuration is separate from each saved run.

The palette follows Hallmark Vantage: blue-black surfaces, pale steel text and a restrained lime signal for primary actions, selected states and estimated trajectories. The system sans stack is bundled by the operating system and requires no font download. All colour and typography roles live in `frontend/src/styles.css` under `:root`. Error and warning roles include explicit text.

Plots use locally packaged Plotly. The backend supplies full nominal 95% covariance ellipsoid axes and separate 95% XY/XZ marginal ellipse axes. The frontend renders that geometry without estimating eigenvalues or statistical scales. Nominal confidence and measured reference coverage remain distinct. Axis labels retain physical units.

The engine supplies configuration defaults, data readiness, metrics, provenance and study status. Missing metrics read “Unavailable”. Synthetic validation fixtures and injected faults remain labelled. No fixture can satisfy a real-data study gate.

Tabs, selected runs, comparison run, time cursor and diagnostic filters live in URL query parameters. Requests abort on selection changes and stale responses are discarded. Loading, empty and error regions remain distinct. Keyboard focus is visible, native controls retain keyboard behaviour, and reduced motion stops cosmetic motion.

The page container caps at 1568 px. Scientific tables and plots receive the available width; the experiment ledger moves below the form on narrow screens. The interface uses no external images, decorative charts, testimonials or claimed study numbers.

Verification commands: `npm run typecheck`, `npm run lint`, `npm test` and `npm run build` from `frontend`. Browser workflows use the actual local API when running the integration suite. Test fixtures, where used to exercise unavailable-data errors, are explicitly test-only responses.

On 30 October 2026, Windows 11 and Node 26.2.0 passed TypeScript, Biome and all eight Chromium 156 integration tests. They exercise actual local execution, cancellation, replay, comparison, exports, protected sessions, missing numeric rows, authoritative errors, calibration settings and covariance geometry. Screenshots wait for Plotly to settle and assert actual 1920 and 390 px PNG widths. A fresh finish review found clipped mobile 3D labels; larger scene margins and camera framing were verified by recapture. The local lazy Plotly bundle produces a documented Vite size warning.

The Hallmark static sweep reported zero failures and one explicitly waived semantic warning stripe. Its optional render tier could not run because puppeteer-core is unavailable. Executed Playwright screenshots and an independent finish-review recapture supply the visual evidence.
