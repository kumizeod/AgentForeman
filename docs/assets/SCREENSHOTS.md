# README screenshots

These images are browser captures of the AgentForeman v0.14.2 interface, taken on 2026-10-31 from source commit `04140c8`. They are not UI mockups. The README update does not translate the application; its current labels remain Chinese.

| Image | Page | Demonstrated state |
| --- | --- | --- |
| `workspace.webp` | Tasks | Chairman contract, cooperative execution, independent acceptance and reported task usage |
| `connections.webp` | Subscriptions & Models | Four local demonstration API connections, test status, deletion controls and allocation preferences |
| `usage.webp` | Usage | Reported input/output totals, connection shares and task-level accounting |

## Data and capture method

The data comes from the existing browser workflow in `tests/browser/ui.cjs`, using `tests.fixture_connection_api` as a loopback-only, scripted provider. No paid subscription, real account password or external API key was used. Connection labels were seeded as `Demo model 1` through `Demo model 4`, and the task was named `Demo: validate the sales total`, instead of using the suite's injection-test strings. The model ID remains explicitly `test/model`, and some replies retain `TEST ONLY` labels.

The capture follows real connection creation/tests, target-share persistence, chairman contract confirmation, adversarial and cooperative execution, independent JSON acceptance checks, and usage aggregation. Provider replies and token counts are scripted. They illustrate application behavior, not live-provider quality, subscription compatibility or measured cost.

Captures use Chromium with a 1440-pixel desktop viewport and Noto CJK fonts. Each page is captured at full height after its relevant UI has settled; the optional historical diagnostics panel is collapsed on Usage. Images are compressed to lossless WebP without changing the page contents, layout or reported values.

The temporary capture variant stops after these workflows; it is not a full-suite validation run. Authentication, official-client login, deletion and cancellation validation are documented separately in the repository's test evidence. The unmodified browser suite remains the authoritative test implementation.
