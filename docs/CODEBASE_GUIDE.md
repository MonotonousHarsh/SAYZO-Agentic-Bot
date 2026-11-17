# SAYZO code guide, in plain language

This guide explains what every file in the project does and why it exists. It is written for a
reader who has not seen the code before.

## The big picture first

SAYZO answers questions about a company using only what the company has written down: chat
messages, documents and meeting notes. It works in four steps.

1. **Get the data in.** Read the exports, cut them into small pieces, and store them.
2. **Find the right pieces.** When someone asks a question, look up the pieces that match.
3. **Write an answer.** Give those pieces to an AI model and check that it only used them.
4. **Do one action.** Turn action items from meetings into tasks, after a person approves.

Two rules apply everywhere: people only see what their role and groups allow, and one company
can never see another company's data.

The code is split into small folders, one per job, so you can read one job without reading the
rest. Folders never reach into each other's insides.

```
backend/
  app/
    modules/   <- the jobs (ingest, search, answer, tasks...)
    shared/    <- tools every job uses (database, AI model, embeddings)
    api/       <- the web server and the screen people use
    config.py, bootstrap.py   <- settings, and where everything is wired together
  scripts/     <- commands you run in the terminal
  migrations/  <- the database tables, created step by step
  tests/       <- checks that the code still works
datasets/      <- the made-up company data
docs/          <- README extras, diagrams, this guide
```

---

## 1. Settings and wiring (`app/`)

| File | What it does | Why it exists |
|---|---|---|
| `config.py` | Reads settings from the `.env` file: database address, Groq key, Supabase keys, time zone. | One place for settings, so no secret is ever typed into code. |
| `bootstrap.py` | Builds the whole system: creates the database connection, loads the embedding model, creates the AI client, and hands each job what it needs. | Every other file asks for what it needs instead of creating it. That makes each file easy to test and easy to swap. |

## 2. Shared tools (`app/shared/`)

| File | What it does | Why it exists |
|---|---|---|
| `database/engine.py` | Opens the connection to Postgres (Supabase) and keeps all our tables in their own `sayzo` area. | Keeps our tables away from the part of Supabase that is open to the internet. |
| `database/unit_of_work.py` | Wraps one job in one all-or-nothing database transaction. | If anything fails halfway, nothing is saved, so data is never left half-written. |
| `database/ids.py` | Makes the same id from the same name every time (e.g. `nex_rohan` always gives the same id). | Running a load twice updates rows instead of creating copies. |
| `embeddings/interfaces.py` | Describes what an "embedding provider" must do. | Lets us swap the model without touching other code. |
| `embeddings/sentence_transformer_provider.py` | Runs the BAAI/bge model to turn text into a list of numbers that captures its meaning. | This is how "find text that means the same thing" works. |
| `llm/interfaces.py` | Describes what an AI-model provider must do. | Same idea: Groq can be replaced by another model later. |
| `llm/groq_provider.py` | Calls Groq's model, forces its reply into a fixed JSON shape, and retries when Groq is busy (rate limits). | Gets reliable, machine-readable answers instead of free text. |
| `text.py` | Cleans text: removes invisible characters and odd spaces, collapses whitespace. | The AI sometimes writes look-alike characters that break exact matching. |

## 3. Company directory and access (`app/modules/identity/` and `sources/directory.py`)

| File | What it does | Why it exists |
|---|---|---|
| `sources/directory.py` | Describes the people file: companies, people, roles, access groups, projects. | The starting point for "who is allowed to see what". |
| `identity/ids.py` | Gives each person and group a steady database id. | So seeding is safe to run again. |
| `identity/seeding.py` | Loads the people, groups and projects into the database. | The database must know who exists before anyone can ask anything. |
| `identity/repository.py` | The database queries for people and groups. | Keeps SQL out of the logic files. |
| `identity/access.py` | Works out, for each request, who the person is, their role, and which groups they are in. It reads this fresh from the database every time. | If someone is removed or changes group, it takes effect immediately. Everything else asks this file "what may this person see?" |

## 4. Reading the raw data (`app/modules/sources/`)

Each tool (Slack, Notion, meetings) exports data in its own shape. These files convert them all
into one common shape so the rest of the code does not care where text came from.

| File | What it does |
|---|---|
| `common.py` | The one shared shape: where it came from, who wrote it, when, which project, which access group, the text, and its version. |
| `slack.py` | Reads chat exports, including "message edited" and "message deleted" events, and spots the same message exported twice. |
| `notion.py` | Reads documents, where each entry is one saved version of a page. |
| `meetings.py` | Reads meeting transcripts: who spoke, when, and what they said. |

## 5. The made-up data (`app/modules/synthetic_data/` and `datasets/`)

| File | What it does | Why it exists |
|---|---|---|
| `loader.py` | Reads a dataset folder into one object. | One door into the data files. |
| `manifest.py` | Reads the list of deliberate tricky cases (edited, deleted, duplicate, conflicting, restricted). | The data is built to test the hard parts, and this file says where they are. |
| `validation.py` | Checks the dataset is sound: references exist, every promised tricky case is really there, nothing crosses between companies. | A bad dataset gives misleading test results. |
| `index.py` | Quick lookups over the loaded data. | Lets the checks run fast. |
| `evaluation.py` | Reads the list of test questions and what a correct answer looks like. | Used to score the system. |
| `generation/generator.py`, `prompts.py`, `schemas.py` | Ask the AI model to write a larger made-up dataset, then check the result's shape. | Gives a bigger dataset than anyone wants to write by hand. |

## 6. Ingestion: turning data into searchable pieces (`app/modules/ingestion/`)

| File | What it does | Why it exists |
|---|---|---|
| `planning.py` | For each source, decides: **create** it, **update** it, **delete** it, leave it **unchanged**, or **ignore** it. | This is how edits, deletions and duplicates are handled correctly instead of piling up copies. |
| `chunking.py` | Cuts long text into pieces of a useful size. | A whole document is too big to match precisely. Small pieces give exact answers. |
| `service.py` | Runs the steps for each source: plan, cut, embed, store, extract facts. | The conductor of ingestion. |
| `repository.py` | The database queries for sources, pieces and the knowledge graph. | Keeps SQL in one place. |
| `graph/extractor.py` | Asks the AI to pull out who and what is mentioned, and how they relate (e.g. "Rohan works on the API migration"). | Lets us answer questions about people and projects, not just matching words. |
| `graph/schemas.py` | The allowed kinds of things and links. | The AI can only pick from a fixed list, so it cannot invent categories. |
| `graph/validation.py` | Checks the AI's output with plain code and drops anything that does not hold up. | We do not trust the AI blindly. |

## 7. Retrieval: finding the right pieces (`app/modules/retrieval/`)

| File | What it does | Why it exists |
|---|---|---|
| `service.py` | Runs a search three ways at once: by meaning, by exact words, and by the people/project links. Then merges the results. | Each way misses things the others catch. |
| `fusion.py` | Combines the ranked lists into one by rewarding items that rank well in several. | A fair way to merge without comparing unlike scores. |
| `entity_matching.py` | Spots names of people and projects inside the question. | Lets "What is Rohan working on?" use the people links. |
| `repository.py` | The search queries. **Every query only returns what this person is allowed to see**, so the permission check cannot be forgotten. | Access control is part of the search itself, not an afterthought. |
| `models.py` | The shape of a search result. | Shared language between search and answering. |
| `evaluation.py` | Scores search on the test questions and also checks nothing restricted leaked. | Proves search works and stays safe. |

## 8. Answering with proof (`app/modules/answering/`)

| File | What it does | Why it exists |
|---|---|---|
| `service.py` | Runs the answer: pick evidence, ask the model, check its work, retry once if needed. | The conductor of answering. |
| `evidence.py` | Decides which found pieces are good enough to use. If nothing is, the system says it does not know. | Prevents confident guessing. |
| `prompt.py` | The exact instructions sent to the model, and the shape of the reply it must give. | Tells the model to use only the given text, ignore any instructions hidden inside documents, and flag disagreements. |
| `citations.py` | Checks every source number the model cited really exists, then renumbers them neatly. | An answer is only trusted if its sources are real. |
| `models.py` | The shape of an answer: status (answered / not enough evidence / sources disagree), text, citations. | Shared language for the screen and the tests. |
| `evaluation.py` | Scores answers against the test questions. | Proves behaviour, not just hopes. |

## 9. Conversations (`app/modules/conversations/`)

| File | What it does | Why it exists |
|---|---|---|
| `service.py` | Handles a question: decides whether it is a normal question or a task request, then runs the right job and saves the exchange. | One entry point for the chat. |
| `repository.py` | Saves and reads conversations. | Keeps SQL in one place. |
| `models.py` | The shape of saved messages. A saved answer keeps a copy of its citations. | The history still reads correctly even if the source is later edited or deleted. |

## 10. Tasks, the one action (`app/modules/tasks/`)

| File | What it does | Why it exists |
|---|---|---|
| `intent.py` | A simple check: does this message ask for tasks to be created? | Fast, predictable, and no AI guesswork for this decision. |
| `proposals.py` | Asks the model to propose tasks from the evidence, then fills in assignees and dates with plain code. | The AI suggests; code verifies. |
| `policy.py` | Who may do what: viewers cannot create tasks, members change their own, admins change any. | Role rules in one readable file. |
| `service.py` | Creates the task after the person approves, re-checks everything, blocks duplicates, changes status. | Nothing is created until a person confirms. A double-click cannot create two. |
| `repository.py` | The task queries, including the history log. | Keeps SQL in one place. |
| `models.py` | The shape of a task and a proposal. | Shared language. |
| `evaluation.py` | Scores task proposals on the test questions. | Proves it. |

## 11. The web server and screen (`app/api/`)

| File | What it does | Why it exists |
|---|---|---|
| `main.py` | The web server: every address the screen calls (ask, tasks, conversations, who am I). Turns errors into the right messages. | The doorway to everything above. |
| `auth.py` | Checks the sign-in token from Supabase, finds the matching person, and offers a "pick a demo person" mode for local use only. | Only real, known people get in. |
| `container.py` | Holds the built services for the server. | Lets tests swap in fakes. |
| `schemas.py` | The exact shapes of requests and replies. | Bad input is rejected at the door. |
| `static/index.html` | The page layout: sign-in, chat, conversation list, task panel. | The screen people see. |
| `static/styles.css` | Colours, spacing, and how the page changes on small screens. | Look and feel. |
| `static/app.js` | The screen's behaviour: sign in, ask, show sources, review and create tasks. It writes server text as plain text, never as page code. | Text from documents can never run as code in the browser. |

## 12. Commands you run (`scripts/`)

| Script | What it does |
|---|---|
| `validate_dataset.py` | Checks a dataset is sound. |
| `generate_dataset.py` | Makes a larger dataset with the AI model. |
| `seed_directory.py` | Loads people and groups. |
| `ingest_dataset.py` | Loads all the data (the main loading command). |
| `db_status.py` | Shows what is in the database. |
| `search.py` | Try the search by hand. |
| `ask.py` | Ask a question from the terminal. |
| `tasks.py` | Propose, confirm and list tasks from the terminal. |
| `evaluate_retrieval.py`, `evaluate_answers.py`, `evaluate_tasks.py` | Score each part against the test questions. |
| `create_demo_users.py` | Creates demo sign-in accounts in Supabase. |
| `serve.py` | Starts the web server. |

## 13. Database setup (`migrations/`)

| File | What it does |
|---|---|
| `sql/0001_initial_schema.up.sql` | Creates every table: people, sources, pieces, graph, conversations, tasks. Also the rules that stop one company seeing another's rows. |
| `sql/0002_source_duplicates.up.sql` | Adds tracking for exact duplicate sources. |
| `sql/*.down.sql` | Undo each step. |
| `versions/*.py`, `sql_runner.py`, `env.py` | Small helpers that run those SQL files in order. |

## 14. Tests (`tests/`)

Fast tests (no database) cover splitting, merging, validation, answer checking and task rules.
`tests/integration/` runs the real flow against a real Postgres: loading data, searching,
asking, creating tasks, and the web API. `fakes.py` gives a pretend AI model and pretend
embeddings so tests are quick and repeatable.

---

## Follow one question through the code

"Who is working on the API migration?"

1. `app.js` sends it to `main.py` with the person's sign-in.
2. `auth.py` finds who they are. `access.py` loads their role and groups.
3. `conversations/service.py` sees it is a normal question and calls answering.
4. `retrieval/service.py` finds matching pieces, but only ones this person may see.
5. `evidence.py` keeps the good ones. If none are good, the answer is "not enough evidence".
6. `prompt.py` builds the instructions and Groq replies.
7. `citations.py` checks every source it cited is real.
8. The reply is saved with its citations and shown with its sources.
