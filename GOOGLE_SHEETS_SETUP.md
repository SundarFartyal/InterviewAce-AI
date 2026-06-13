# Google Sheets Storage — Setup Guide

InterviewAce AI now stores **Interviews**, **Answers**, and **Feedback** in a
Google Sheet (one tab each). Questions still come from `data/questions.csv`.
The three tabs are created automatically on first write, so you only need to
create an empty spreadsheet and connect a service account.

---

## 1. Create a Google Cloud project & enable APIs

1. Go to https://console.cloud.google.com/ and create a new project
   (e.g. `interviewace-ai`).
2. Open **APIs & Services → Library** and enable both:
   - **Google Sheets API**
   - **Google Drive API**

## 2. Create a service account

1. Go to **APIs & Services → Credentials → Create credentials → Service account**.
2. Give it a name (e.g. `interviewace-writer`) and click **Done**.
3. Open the service account → **Keys → Add key → Create new key → JSON**.
4. A JSON file downloads. Rename it to `service_account.json` and place it in
   the project root:
   `ai-mock-interview/service_account.json`
   (Keep this file private — never commit it to git.)

## 3. Create the spreadsheet and share it

1. Create a new Google Sheet (e.g. titled **InterviewAce AI**). You do **not**
   need to add any tabs — the app creates `Interviews`, `Answers`, and
   `Feedback` automatically with their headers.
2. Copy the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`<SPREADSHEET_ID>`**`/edit`
3. Open the JSON file and copy the `client_email`
   (looks like `interviewace-writer@your-project.iam.gserviceaccount.com`).
4. In the Sheet, click **Share**, paste that email, and give it **Editor**
   access. This step is required — without it the app cannot read or write.

## 4. Configure `.env`

```bash
cp .env.example .env
```

Then edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
GOOGLE_SHEET_KEY=<SPREADSHEET_ID>
```

## 5. Install packages and run

```bash
cd "ai-mock-interview"
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

New packages added: `gspread`, `google-auth`.

---

## How it maps

| App data    | Stored in            |
|-------------|----------------------|
| Questions   | `data/questions.csv` (unchanged) |
| Interviews  | Google Sheet tab `Interviews` |
| Answers     | Google Sheet tab `Answers` |
| Feedback    | Google Sheet tab `Feedback` |

- Every new interview is written directly to the `Interviews` tab; each answer
  to `Answers`; each feedback entry to `Feedback`.
- The **History** page reads live from the `Interviews` tab.
- The **Export CSV** button still works — it downloads the current interview
  history (pulled from Google Sheets) as a CSV file.

## Troubleshooting

- **`service account file not found`** — check the path in
  `GOOGLE_SERVICE_ACCOUNT_FILE` and that the JSON is in the project root.
- **`PermissionError` / 403** — you forgot to **Share** the Sheet with the
  service account's `client_email` as Editor.
- **`SpreadsheetNotFound`** — `GOOGLE_SHEET_KEY` is wrong, or the sheet isn't
  shared with the service account.
- **APIs disabled** — make sure both Google Sheets API and Google Drive API are
  enabled in the Cloud project.
