# JobFinder (jobreach)

Finds IT companies near you and remote openings worldwide, finds a public contact
e-mail, drafts a personal message for each, and — after you approve — sends it
from your own mailbox with your resume attached. Python 3.11+, no installs needed.

## Setup (once)

1. Copy `config.example.toml` to `config.toml` and fill in the `[me]`, `[local]` and `[global]` sections.
2. Put your resume next to it as `resume.pdf`.
3. Gmail: turn on 2-Step Verification, create an **App password**
   (myaccount.google.com/apppasswords) and set it as an environment variable:
   - Windows (PowerShell): `$env:JOBREACH_SMTP_PASSWORD = "xxxx xxxx xxxx xxxx"`
   - macOS / Linux: `export JOBREACH_SMTP_PASSWORD="xxxx xxxx xxxx xxxx"`
4. Optional, for many more local companies: set `GOOGLE_MAPS_API_KEY` (Google Cloud, "Places API (New)").

## Routine

| Step | Command | What happens |
|---|---|---|
| 1 | `python jobreach.py find` | Collects companies into `leads.csv` (`--scope local` / `global` for one side only) |
| 2 | `python jobreach.py enrich` | Reads each company's contact/careers page for a published e-mail |
| 3 | `python jobreach.py draft` | Writes one message per company to `drafts/L0001.txt` … |
| 4 | *you* | Open `leads.csv`, read/edit the drafts, type `yes` in `approved` |
| 5 | `python jobreach.py send --test` | Sends the first approved draft to yourself |
| 6 | `python jobreach.py send --live` | Sends approved drafts (daily cap, random pauses). Without `--live` it only previews |
| 7 | `python jobreach.py replies` | Marks who replied / bounced by checking your inbox |
| 8 | `python jobreach.py followup --live` | One follow-up to companies silent for 7+ days |

`python jobreach.py status` shows where everything stands. Run steps 1–3 weekly; new companies are added, known ones are never contacted twice.

Rows with status `manual` have no public e-mail: use the `job_url` / website form and paste the text from their draft file.

## Tests

`python tests/test_offline.py` — runs the whole pipeline against fake servers (no internet, no real e-mail).
