# CCOA — Chief Compliance Officer Assistant

AI-powered compliance management for tracking frameworks, evidence, findings, and audit readiness. CCOA runs on your computer using Docker — nothing is hosted in the cloud except the AI chat (which uses your Anthropic API key).

---

## What You Need Before You Start

| Requirement | What it is | Where to get it |
|-------------|------------|-----------------|
| **Docker Desktop** | Runs the app on your computer | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) — download, install, and keep it **running** (whale icon in your taskbar/menu bar) |
| **Anthropic API key** | Lets the AI chat assistant work | [console.anthropic.com](https://console.anthropic.com/) — create an account and generate an API key |
| **This project folder** | The software files | Download from GitHub (see below) or clone with Git |

**Supported systems:** Windows 10/11 and Mac (Apple Silicon or Intel).

---

## Step 1 — Download the Software

### Option A: Download as a ZIP (easiest)

1. Open the project on GitHub in your web browser.
2. Click the green **Code** button.
3. Choose **Download ZIP**.
4. Unzip the file to a folder you will keep — for example:
   - Windows: `C:\Users\YourName\COO Automation`
   - Mac: `~/COO Automation`
5. Remember this folder location — you will use it often.

### Option B: Clone with Git (if you use Git)

```bash
git clone https://github.com/pudme/COOAutomation.git
cd COOAutomation
```

---

## Step 2 — First-Time Setup (do this once)

These steps prepare the app the first time you install it.

### 2a. Create your settings file

1. Open the project folder in File Explorer (Windows) or Finder (Mac).
2. Find the file named `.env.example`.
3. **Copy** it and rename the copy to `.env` (no `.example` at the end).
4. Open `.env` in Notepad (Windows) or TextEdit (Mac).
5. Find the line `ANTHROPIC_API_KEY=` and paste your API key after the `=` sign.
   - It should look like: `ANTHROPIC_API_KEY=sk-ant-...`
6. Save and close the file.

> **Important:** Never share your `.env` file or commit it to GitHub — it contains your private API key.

### 2b. Start the platform

Make sure **Docker Desktop is running**, then:

**Windows:** Double-click `start.bat` in the project folder.

**Mac:** Open Terminal, go to the project folder, and run:
```bash
bash start.sh
```

The first start takes several minutes while Docker downloads and builds everything. Wait until your browser opens automatically, or go to:

**http://localhost:3000/dashboard**

### 2c. Initialize the database (first time only)

After the first successful start, run these two commands **once**:

**Windows** — open Command Prompt or PowerShell in the project folder:
```bat
docker compose exec backend python cli.py init-db
docker compose exec backend python cli.py load-all-frameworks
```

**Mac** — in Terminal, from the project folder:
```bash
docker compose exec backend python cli.py init-db
docker compose exec backend python cli.py load-all-frameworks
```

When both finish without errors, setup is complete.

---

## Step 3 — Using CCOA Every Day

### Open the app

1. Make sure Docker Desktop is running.
2. Open your browser and go to: **http://localhost:3000/dashboard**

If the page does not load, start the platform:
- **Windows:** double-click `start.bat`
- **Mac:** run `bash start.sh` in Terminal

### Main areas of the app

| Menu item | What it does |
|-----------|--------------|
| **Dashboard** | Overview — audit countdown, readiness scores, open gaps |
| **Frameworks** | Browse ISO 27001, CMMC, and other compliance frameworks |
| **Documents** | Upload and search compliance documents |
| **Revision History** | See what changed and when |
| **Findings** | Track audit findings and corrective actions |
| **Auditor** | Auditor checklist and evidence mapping |
| **Obligations** | External obligations and due dates |
| **Personnel** | Staff compliance status (training, MFA, etc.) |
| **Chat** | Ask the AI assistant questions, ingest notes, update records |
| **Settings** | App preferences and configuration |

### Using the AI chat

The **Chat** screen is the fastest way to work with compliance data:

- Ask questions like *"What are my biggest gaps before the audit?"*
- Paste meeting notes or a Notion page URL to ingest new information
- Drag and drop files onto the chat to upload evidence
- The assistant may ask you to **Approve** or **Reject** changes before they are saved

---

## Optional — Start Automatically When You Log In

If you want CCOA to start every time you sign in to your computer:

**Windows** — right-click PowerShell, choose **Run as administrator**, then:
```powershell
powershell -ExecutionPolicy Bypass -File setup_windows.ps1
```

**Mac** — in Terminal, from the project folder:
```bash
bash setup_mac.sh
```

After this, the platform starts in the background when you log in. Open **http://localhost:3000/dashboard** in your browser as usual.

---

## Stop the Platform

When you are done for the day (optional — your data is preserved):

- **Windows:** double-click `stop.bat`
- **Mac:** run `bash stop.sh` in Terminal

---

## Updating to a New Version

When a newer version is available on GitHub:

1. Stop the platform (`stop.bat` or `stop.sh`).
2. Download the latest files (ZIP or `git pull`).
3. Rebuild and restart:
   - **Windows:** double-click `rebuild.bat`
   - **Mac:** run `bash rebuild.sh` in Terminal
4. Open **http://localhost:3000/dashboard**

Your existing data (database, documents) is kept between updates.

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| Page won't load | Confirm Docker Desktop is running, then run `start.bat` or `start.sh` |
| "Docker is not running" | Open Docker Desktop and wait until it says it is ready |
| Chat doesn't respond | Check that `ANTHROPIC_API_KEY` is set correctly in your `.env` file |
| First start is very slow | Normal — Docker is downloading images; allow 5–15 minutes |
| Something still broken | Run `docker compose logs backend` and share the output with your administrator |

---

## For Developers

Technical architecture, API details, and build instructions are in [CLAUDE.md](./CLAUDE.md).

Quick reference:

```bash
docker compose up -d              # Start all services
docker compose up -d --build backend frontend   # Rebuild after code changes
docker compose logs -f backend    # View backend logs
docker compose stop               # Stop all services
```
