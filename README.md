# Order Form App

## >>> [Download the app: OrderFormApp.exe](https://github.com/taylor223223/OrderFormApp/releases/latest/download/OrderFormApp.exe) <<<

Double-click the downloaded **OrderFormApp.exe**. Nothing else to install (no Python). If Windows shows "Windows protected your PC", click **More info -> Run anyway**. Everything below this line is for developers.

A private Windows desktop app for filling out Apartment Interior Supply order forms. It keeps track of customers, their unit layouts and measurements, predicts form values from past orders, reads order requests from Outlook, and emails the finished PDFs.

Everything runs on your own computer. The app opens in its own window (Edge/Chrome app mode) and only answers to `127.0.0.1`. The data is kept in one local database file.

## What it does

| Area | What you get |
|---|---|
| **Login** | Username + password (hashed). Locks for 5 minutes after 5 wrong tries. Logs you out automatically after inactivity. |
| **Customers** | Contact info, multiple contacts, notes. Shows which fields are missing and fills them from previous orders. |
| **Floorplans & units** | Measure a floorplan once and every unit with that layout uses it. Units can override it with their own measurements. Add units in bulk (`101-124, 201-224`). Copy a layout's measurements to a mirrored floorplan. |
| **Products** | Interior, entry, pre-hung, bi-pass, closet, storage and garage doors, screen doors, window screens, horizontal and vertical blinds, baseboards, cabinets, cabinet doors, and Other. Any custom size, color or style is allowed. |
| **Order forms** | All 9 supplied PDF forms are mapped field by field: Door, New Door, Pre-Hung, Bi-Pass, Screen Door, Window Screen, Horizontal Blind, and both Vertical Blind forms. |
| **Smart fill** | Type a unit # or pick a floorplan and the lines fill from saved measurements. Dropdowns suggest this customer's usual values first. Predicted values are highlighted in yellow. Values that don't match a checkbox go into Comments automatically. More lines than the form holds become extra pages. |
| **PDF** | Fills the real fillable form and saves it to `Documents\Order Forms Output\<Customer>\`. |
| **Email (Outlook)** | Reads your inbox, picks out the customer, PO, units, sizes, colors and swing, and builds a draft order. Missing info is filled from the unit's saved floorplan. It can also read a filled-out copy of one of the forms that a customer sends back. Sends the PDF from Outlook (as a draft to review, or sent immediately). |
| **CSV / Excel import** | Upload old orders or customer and unit lists. Columns are auto-detected and you can change them. A review screen fills blank fields (pre-checked) and flags conflicts (unchecked). Nothing is changed until you click Apply. |
| **Order tracking** | Status board (Draft → Ready → Sent → Confirmed → Shipped → Installed …), due dates, vendor #, history. Orders placed outside the app can be added manually. |
| **Backup** | One-click database backup, plus CSV exports of customers, units and measurements. |

## CRM and route planner (v1.2)
- **CRM tab:** log calls, texts, emails and site visits (who, where, about what, topics, outcome). You can also set follow-up tasks, track deals in a pipeline, and see which accounts have gone 30+ days without contact. Every property has its own **CRM** tab with a timeline.
- **Weekly report:** CRM → Weekly report shows everyone you interacted with that week. It emails your manager a PDF and a spreadsheet. Set the manager's address in Settings.
- **Routes tab:** plan a week at a time. When a property calls or texts, add it to a day and the stop is placed where it adds the least driving. **Optimize route** puts the stops in the best order. Each day shows drive times, arrival times, total miles, a map, and an **Open in Google Maps** button for turn-by-turn directions. Marking a stop **Done** logs a site visit.
- **Drive times** are estimated out of the box. For real road times, get a free OpenRouteService key (openrouteservice.org → sign up → Dashboard → copy key) and paste it in Settings → Route planner.

## Run it

**Option A – from the code (needs Python 3.10+):** double-click `run_from_source.bat`. The first run installs what it needs.

**Option B – build the .exe yourself:** double-click `build.bat`. The app will be `dist\OrderFormApp.exe`.

**Option C – let GitHub build it:** see below.

On the first launch you create your login, then go to **Settings** to check the sales rep name and email setup.

> Windows SmartScreen will warn because the exe isn't code-signed. Click **More info → Run anyway**.

## Put it on GitHub

1. Create a repository on github.com. Make it **Private** (see the note below).
2. Upload this whole folder. The `.gitignore` keeps the database, keys and tokens out. They never live in this folder anyway.
   - **Workflow file:** on GitHub, click **Add file → Create new file** and name it `.github/workflows/build.yml`. Paste in the contents of `github-workflow-build.yml`, then commit. (If this folder already has `.github/workflows/build.yml`, uploading it is enough.)
3. **Actions** tab → *Build Windows exe* → **Run workflow**. Download `OrderFormApp.exe` from the run's Artifacts.
4. To publish a Release that others can download, create a tag: **Releases → Draft a new release → tag `v1.0.0` → Publish**. The workflow attaches the exe to the release.

**Keep the repo private unless you've cleared it with the company.** The bundled PDFs are Apartment Interior Supply's branded forms, and they print your name and the company's address. Also, PyMuPDF (the PDF library) is licensed under AGPL-3.0. A public repo should therefore be released under AGPL-3.0, or the PDF code would need to switch libraries.

## Phone / tablet version (online)

The same app can run online so it works on your phone and tablet anywhere. It installs to the home screen like an app.

**Hosting:** [Render](https://render.com), about $7.25/month ($7 Starter server plus $0.25 for 1 GB of disk). `render.yaml` in this repo sets everything up.

1. Sign up at render.com with **Sign in with GitHub**, and allow Render to see the `OrderFormApp` repo.
2. **New → Blueprint →** choose `OrderFormApp` → **Apply**. Render builds it and gives you a web address like `https://order-form-app-xxxx.onrender.com`.
3. In Render, open the service → **Environment** and copy **ORDERAPP_SETUP_KEY**.
4. Open the web address, enter the setup key, and create your login (10+ character password).
5. **Move your PC data:** in the PC app, go to Settings → **Download backup**. In the online app, go to Settings → **Restore** and choose that file. Then log in with your PC username and password.
6. **Install on the phone:**
   - iPhone/iPad: open the address in Safari → Share → **Add to Home Screen**.
   - Android: open it in Chrome → ⋮ → **Install app**.

**Email from the phone:** on the email screen, tap **Share from this phone/tablet** and pick Outlook. The PDF and photos are attached for you. If you want the app to send by itself, use Microsoft 365 sign-in (see Email setup).

**Security in online mode:**
- HTTPS only.
- Creating the first login requires the setup key.
- 10+ character passwords.
- The account locks after repeated failed logins, and logins are throttled per device.
- Secure cookies, and no caching of pages.
- Code updates you push to GitHub redeploy automatically. Your data stays on the Render disk.

## Email setup

In **Settings → Email**, pick one:

- **Outlook on this PC** (default): works with *classic* Outlook and needs no setup. It reads and sends as whatever account Outlook is signed in to.
- **Microsoft 365 / new Outlook**: use this if you use the "new Outlook" toggle, or if classic Outlook isn't installed. One-time setup:
  1. Go to https://entra.microsoft.com → **App registrations → New registration**.
  2. Name it "Order Form App". For supported accounts, choose *Accounts in any organizational directory and personal Microsoft accounts*. Under Redirect URI, choose **Public client/native** with `http://localhost`.
  3. **Authentication** → turn on **Allow public client flows** → Save.
  4. **API permissions → Add → Microsoft Graph → Delegated**: `Mail.ReadWrite`, `Mail.Send`, `User.Read`.
  5. Copy the **Application (client) ID** into Settings, save, and click **Sign in to Microsoft**.

  If your company blocks user consent, IT has to approve the app once.
- **No connection**: creates a `.eml` draft with the PDF attached and opens it in your mail app.

You can also drag an email out of Outlook to your desktop and drop the `.msg` file on the Email page, or paste the text of an email or text message.

## CSV columns it recognises

`Property/Customer, Acct #, Address, City, State, Zip, Mgmt Co, Phone, Email, Contact, Contact Email, Contact Phone, Unit, Building, Floorplan, Product, Room/Location, Qty, Item #, Width, Height, Length, Color, Style, Finish, Core, Swing, Thickness, PO, Date, Status, Notes, Verified`. Any other column can be mapped by hand on the mapping screen.

Rows that have a PO or date become *previous orders*, which feed the smart dropdowns. Rows with a product and sizes become saved measurements on the unit's floorplan.

## Where your data is

`%LOCALAPPDATA%\OrderFormApp\` holds:

- `orderapp.db`: everything
- `secret.key`: session signing key
- `graph_token_cache.json`: only if you use Microsoft sign-in

Back up from **Settings → Download backup**. To move to a new PC, copy that folder.

## Developer notes

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
python run.py            # http://127.0.0.1:8765
python -m pytest -q      # 18 tests: every form round-trips, full app flow
```

- `orderapp/catalog.py`: field map for every form (logical keys → PDF field names, plus page coordinates for the Pre-Hung rows that have no fillable fields).
- `orderapp/pdf_fill.py`: fills the forms, reads back filled forms, validates the map.
- `orderapp/suggest.py`: predictions; `importer.py`: CSV; `email_parse.py` + `emailer.py`: Outlook.
- To add a new order form, drop the PDF in `orderapp/forms/`, add an entry in `FORMS`, and run the tests. `validate_catalog()` flags any field name that doesn't exist in the PDF.
- Environment: `ORDERAPP_DATA` (data folder), `ORDERAPP_PORT` (default 8765).
