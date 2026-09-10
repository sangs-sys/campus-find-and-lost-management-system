# Campus Lost & Found System

A Flask + SQLite prototype for a campus lost-and-found workflow.

## Updated UI

- Landing page opens first.
- Clicking anywhere on the landing page opens Login.
- Login heading is **Welcome to**.
- Admin demo credentials are removed from the login screen.
- Login/Register and all authenticated pages use a soft light-lavender background.
- The same visual theme is applied to:
  - User Dashboard
  - Report Lost
  - Report Found
  - AI Matching
  - Claim
  - Admin Dashboard
  - Admin Claim Review
- Admin can also report Lost and Found items.

## Main workflow

1. Open `http://127.0.0.1:5000/`.
2. Click the landing page.
3. Login or create an account.
4. Report a lost/found item.
5. View AI-generated possible matches.
6. Submit a claim with:
   - ownership proof photo (required)
   - purchase bill/invoice (optional)
   - additional details
7. Admin reviews the claim.
8. Admin approves/rejects the claim.
9. Approved claims can be marked as returned.

## AI matching

The prototype calculates a weighted score:

- Photo similarity: 35%
- Description/name/colour-brand similarity: 30%
- Location similarity: 20%
- Category match: 15%

AI only suggests possible matches. It does not automatically approve ownership.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Open:

`http://127.0.0.1:5000/`

## Admin

The application automatically creates the admin account:

- Email: `admin@campus.in`
- Password: `Admin#987`

These credentials are intentionally **not shown on the login page**.

## Project structure

```text
campus_lost_found_project/
├── app.py
├── requirements.txt
├── README.md
├── lostfound.db              # created automatically on first run
├── landing_phone.png
├── static/
│   ├── style.css
│   └── uploads/
└── templates/
    ├── base.html
    ├── landing.html
    ├── login.html
    ├── register.html
    ├── dashboard.html
    ├── report_item.html
    ├── view_matches.html
    ├── claim.html
    ├── admin_dashboard.html
    └── admin_claims.html
```

## Note about the landing artwork

`landing_phone.png` is included as the available project artwork. If you want the exact full composite artwork you showed in the chat, replace `landing_phone.png` with that image while keeping the same filename.


## Landing page
When the website is opened at `/`, the supplied **Campus Lost & Found System** image is displayed full-screen. Clicking anywhere on the image opens the login page.
