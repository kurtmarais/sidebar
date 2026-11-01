# Phone sync setup

Your notes sync through **your own Supabase project**. The phone app is a small web app that you add to your home screen, and it doesn't need the PC to be on.

```
 PC sidebar  <── sync ──>  Supabase (your project)  <── sync ──>  Phone app
                            database + sign-in                 (static files on Cloudflare Pages)
```

**What syncs:** notes, checklists, code and links cards, with their categories, colours, priorities, due dates, reminders and archive state.

**What stays on the PC:** images, file shortcuts and secret notes. Reminder pop-ups also appear on the PC only, although you can set and see reminders on the phone.

**Edits on both devices:** if the same card is edited in two places, the newest edit wins.

## 1. Create the database tables (one time)

1. In Supabase, open your project, then **SQL Editor**, then **New query**.
2. Paste the contents of [`supabase/migrations/20261101120000_sidebar_sync.sql`](../supabase/migrations/20261101120000_sidebar_sync.sql) and click **Run**.

This creates two tables, `boards` and `cards`, with row-level security. A signed-in account can only see or change its own rows. Signed-out requests get nothing. It is safe to run again.

If you've connected the GitHub integration, it applies files in `supabase/migrations` automatically, but only from the branch you set as production in Supabase (usually `main`). Running the file in the SQL Editor works no matter which branch the code is on.

## 2. Create your account and lock sign-ups (one time)

1. **Authentication → Users → Add user → Create new user**. Enter your email and a strong password, and tick **Auto Confirm User**.
2. **Authentication → Sign In / Providers**: make sure **Email** is enabled, then turn **off** "Allow new users to sign up". After that, nobody else can create an account in your project.

## 3. Find your project URL and publishable key

**Project Settings → API Keys** (or **Data API**):
- **Project URL**: `https://<something>.supabase.co`
- **Publishable key**: starts with `sb_publishable_`. On older projects it's called the "anon public" key. It is meant to be public, because the database rules only let it work together with your sign-in.

Never use the **secret** or **service_role** key in the apps.

## 4. Turn on sync in the sidebar

Gear icon → **Phone sync…**. Enter the project URL, publishable key, your email and password, then click **Sign in and sync**.
- The password is used once to sign in and is not stored. The sign-in token is stored encrypted with Windows' own data protection, tied to your Windows account.
- The footer shows **Synced HH:MM**. The sidebar syncs every 30 seconds, a few seconds after you edit, when you open the panel, and when you quit.

## 5. Put the phone app online with Cloudflare Pages (one time)

1. Sign in at https://dash.cloudflare.com (a free account is enough).
2. Go to **Workers & Pages → Create → Pages → Connect to Git**, and choose the `sidebar` repository.
3. Set up the build:
   - **Production branch:** `claude/new-session-wmu4rs`, or `main` once the work is merged.
   - **Framework preset:** None
   - **Build command:** leave empty
   - **Build output directory:** `web`
4. Click **Save and Deploy**. You'll get an address like `https://sidebar-xyz.pages.dev`.

Every push to that branch redeploys the app automatically.

**Optional extra privacy:** in Cloudflare, go to **Zero Trust → Access → Applications → Add → Self-hosted** and add your `pages.dev` address with a policy that allows only your email. Then nobody else can even load the app page.

## 6. Install it on the phone

1. Open the `pages.dev` address in Safari (iPhone) or Chrome (Android).
2. Enter the project URL and publishable key. To skip this step, put them in `web/config.js` and push.
3. Sign in with your email and password.
4. Add it to your home screen: **Share → Add to Home Screen** on iPhone, or **⋮ → Install app / Add to Home screen** on Android.

## Using the phone app

- Tap a card to edit it. **Done** saves it.
- Tick checklist items straight from the list.
- **+** creates a note, checklist, code or links card.
- **Notes**, **Today** and **Archive** work as they do on the desktop.
- Long-press a category to rename or delete it. **+** at the end of the categories adds one.
- Edits made offline are kept on the phone and upload when you're back online.
- **Sign out** removes the notes stored on the phone. They stay in Supabase and on the PC.

## Privacy summary

| Where | What's there |
|---|---|
| Supabase | Your text cards and categories, readable only by your account (row-level security). Hosted in the region you chose for the project. |
| Cloudflare Pages | Only the app's own files: HTML, JavaScript and icons. Your notes never pass through it, because the phone talks to Supabase directly. |
| Phone | A copy of your text cards and the sign-in token, kept in the app's own storage. |
| PC | Everything, as before. The sign-in token is encrypted for your Windows account. |

The phone app loads no third-party scripts, fonts or trackers. Its security headers (`web/_headers`) only allow connections to Supabase.
