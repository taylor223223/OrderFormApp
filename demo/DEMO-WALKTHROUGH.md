# Order Form App - 10-minute demo

Everything in this folder is **made up** (fake properties, people, phone numbers, emails). It's safe to show anyone.

| File | What it shows off |
|---|---|
| `1 - Demo customers and order history.csv` | 4 properties, contacts, floorplans, 27 units, measurements for 11 product types, 9 past orders |
| `2 - Demo updates (review screen).csv` | Filling in missing customer info, catching a conflicting phone number, adding a new property |
| `3 - Demo customer email.eml` | A maintenance supervisor's order email with a filled-out Window Screen form attached |
| `4 - Demo field photo.jpg` | A handwritten field order, to show Photo Order |

**Before the meeting:** download **OrderFormApp.exe** (link at the top of the GitHub page) and the files in this `demo` folder. Run the exe and create a login.

Tip: if your real data is already in the app on that computer, use a different PC or Windows user for the demo. The demo adds four fake customers.

---

## 1. Load a customer history (1 min)
1. **Import CSV →** choose `1 - Demo customers and order history.csv` → **Upload & map columns**.
   - Every column is recognized automatically. Point out that it reads any spreadsheet layout, and you can re-map columns by hand.
2. **Continue to review →** it lists everything it found: 4 new customers, contacts, floorplans, units, measurements and past orders.
   - **Nothing changes until you approve it.** Click **Apply checked**.

## 2. A customer record (2 min)
**Customers → Saguaro Ridge Apartments (DEMO)**
- **Info & Contacts:** property details, the property manager and maintenance supervisor, and delivery notes.
- **Floorplans & Measurements:** two layouts (A1 - 1BR, B2 - 2BR), each with verified measurements: bedroom doors, bi-pass closet doors, vertical and horizontal blinds, window screens, screen door, pre-hung entry door and baseboards.
  - *Measure a floorplan once and every unit with that layout uses it.*
- **Units:** 16 units mapped to their floorplans. Open **Unit 108**: it has its own wider bi-pass size that overrides its floorplan (the renovated unit).
- **Orders:** 6 past orders with POs and statuses.

**Mesa Verde Villas (DEMO)** shows the yellow **Missing:** bar: no address, phone or account number yet.

## 3. Fill out an order form with predictions (2 min)
**New Order → Door Order Form → customer Saguaro Ridge.**
1. Type unit **104** in "Fill from saved info" → **Load unit**.
   - Width 30, height 80, Colonist, Left Hand, hollow core and the rest fill in, highlighted **yellow** as predictions.
2. Click **+ Add line** and start typing. Dropdowns default to *this customer's usual* choices (Primecoat, 1-3/8", 2-3/8" basket...).
3. Enter a PO and click **Save & create PDF**. That's the real company order form, filled out. Show **View PDF**.

Try **Bi-Pass** with unit **108**, then **104**: different sizes, because of the unit override.
Try **Vertical Blind** with unit **206**: it loads the 96" B2 living-room blind.

## 4. Order from an email (2 min)
**Email →** "Process a saved email" → choose `3 - Demo customer email.eml`.
- The app recognizes the sender as Saguaro Ridge's maintenance supervisor, and finds **PO SR-26140**, units **104** and **206**, a left-hand bedroom door and a 96 x 84 vertical blind.
- It also detects the **filled-out Window Screen form** attached to the email.
- Pick **Door Order Form → Create draft order**. Unit 104's saved door sizes merge with what the email said.
- Back on the email page, pick **Window Screen Order Form → Create draft order**. The attached form's values come straight in (Bronze, Charcoal, 3 screens).

## 5. Photo order from the field (1 min)
**Photo Order →** add `4 - Demo field photo.jpg`, customer **Palo Verde Commons**, PO PV-1210, units A102, B203 → **Save & email**.
- The photo is compressed and attached, either as a single PDF or as separate images.
- On a phone, you'd take the picture right there.

## 6. Keep customer info current (1 min)
**Import CSV →** `2 - Demo updates (review screen).csv` → continue to review.
- **Mesa Verde:** address, phone and management company show as **fill blank**, already checked.
- **Saguaro Ridge:** a different office phone shows as a **conflict**, unchecked so nothing is overwritten by accident.
- **Ironwood Flats:** a brand-new property.

## 7. Tracking + sending (1 min)
- **Order Tracking:** every order with its status (Completed, Shipped, Backordered...). Change a status with one click, or add orders placed outside the app.
- **Email it:** on any order, open the email screen. It goes to orders@ with the PDF and photos attached, either through Outlook on the PC or the phone's Share button.

---

**Talking points**
- **Fewer wrong orders:** sizes come from verified floorplan measurements, not memory.
- **Faster:** a full door order is a unit number plus a PO.
- **Safe:** everything is behind a login, the app only runs on this PC, and nothing changes customer records without approval.
- **Works in the field:** photo orders now, and a phone/tablet version is ready to host online.
