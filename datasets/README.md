# SAYZO synthetic datasets

All people, companies and content here are fictional.

## `seed/` — hand-written seed dataset

Two companies, so tenant isolation can be tested:

- **Nexora Labs** (6 people): Project Atlas, a customer portal whose launch moves from
  Fri 20 Nov to Mon 23 Nov 2026 because the API migration runs late; and Project Beacon.
- **Vertex Retail** (2 people): a different "Project Atlas" (a store rollout), so a
  cross-tenant leak would be obvious.

Three source systems, each in its own raw format:

| File | Source | Format |
|---|---|---|
| `slack_export.json` | chat messages | Slack-style event stream: `message`, `message_changed`, `message_deleted` |
| `notion_pages.json` | project notes and docs | one entry per saved version of a page |
| `meeting_transcripts.json` | meetings | speaker, offset and text per utterance |

`directory.json` holds organisations, people (role + access groups) and projects.

Deliberate edge cases (declared in `manifest.json`, checked by the validator):

| Case | Where |
|---|---|
| Edited document | `nex_doc_launch_plan` v1 → v2 (launch date changes) |
| Edited message | `nex_msg_002` (load test date corrected) |
| Deleted message | `nex_msg_004` (a wrong launch date, then deleted) |
| Exact duplicates | `nex_msg_001` re-exported twice; `nex_mtg_002` is a re-upload of `nex_mtg_001` |
| Conflict | launch plan says 3 pilot customers, `nex_msg_006` says 5 |
| Restricted | `nex_doc_hiring_plan` (HR + leadership), `nex_msg_007` (leadership) |
| Prompt injection | `nex_doc_vendor_notes` contains an instruction aimed at the assistant |

`evaluation_questions.json` holds 13 ground-truth questions: who asks, how SAYZO must behave
(`ANSWER`, `ABSTAIN`, `CONFLICT`, `TASK_PROPOSAL`, `DENIED`), which sources must and must
not be used, and what the answer must and must not contain.

Validate with `python scripts/validate_dataset.py` from the `backend` folder.

## `generated/` — LLM-augmented datasets

`python scripts/generate_dataset.py` (from `backend`) copies the seed dataset and adds
LLM-written everyday work topics (messages, a page and a meeting per topic) to each project.
The LLM writes only the text. Code assigns ids, authors' channels, timestamps and access
groups, drops anything that touches subjects the evaluation questions depend on (launch dates,
pilots, salaries, ownership and so on), and the result must pass the same validator before it
is written. `generation_log.json` records the model, prompt version, seed, every raw reply and
everything that was dropped, so a run can be inspected and reproduced.
