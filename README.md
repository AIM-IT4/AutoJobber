# Job Apply Agent

Automates job discovery, ranking, and application form filling for Workday, Greenhouse, and Lever style career portals.

This project is designed for high automation, but real-world job portals can still block full autopilot because of CAPTCHA, MFA, custom flows, and anti-bot checks.

## Features
- Discover jobs by company via provider adapters:
  - Workday (`myworkdayjobs.com`)
  - Greenhouse (`boards-api.greenhouse.io`)
  - Lever (`api.lever.co`)
  - Eightfold (`/api/pcsx/search`)
- Fallback provider discovery from web search when explicit targets are not configured.
- Rank jobs using your tags and profile preferences.
- Auto-fill common application fields with Playwright.
- Optional auto-submit mode.
- Persistent browser session support (reuse login between runs).
- Writes screenshots and run results to `outputs/`.

## Quickstart
1. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   python -m playwright install chromium
   ```
2. Create your runtime files:
   ```powershell
   Copy-Item profile.example.yaml profile.yaml
   Copy-Item targets.example.yaml targets.yaml
   ```
3. Update `profile.yaml` with your details, resume path, and answers.
4. Run a dry run:
   ```powershell
   python run.py --company "Boeing" --tags "quant,python,stochastic" --dry-run
   ```
5. Run apply mode:
   ```powershell
   python run.py --company "Boeing" --tags "quant,python,stochastic" --max-jobs 20 --auto-submit
   ```
6. Minimal involvement mode (recommended):
   ```powershell
   python run.py --company "Morgan Stanley" --tags "quant strategist,senior associate" --max-jobs 10 --auto-submit --headful --session-dir .session/chromium
   ```

## CLI
```text
python run.py \
  --company "Boeing,Lockheed Martin" \
  --tags "quant,python,machine learning" \
  --profile profile.yaml \
  --targets targets.yaml \
  --session-dir .session/chromium \
  --max-role-seconds 210 \
  --max-jobs 20 \
  --max-per-company 60 \
  --auto-submit
```

## Notes
- Use only where allowed by each site's terms.
- Keep `auto_submit` off until your field mappings are validated.
- Workday and other portals can change selectors at any time; adjust adapters if needed.
