# 🚀 Fly.io Deployment Completion Guide

## ✅ Current Status
Your Wordle app has been successfully deployed to: **https://wordle-game.fly.dev/**

## 🔧 Required Steps to Complete Setup

### Step 1: Configure Environment Variables
Run these commands in a terminal where Fly CLI is available:

```bash
# Set Google OAuth credentials (replace with your actual values)
fly secrets set GOOGLE_CLIENT_ID="your_google_client_id_here" --app wordle-game
fly secrets set GOOGLE_CLIENT_SECRET="your_google_client_secret_here" --app wordle-game

# Set Flask secret key (generate a secure random string)
fly secrets set SECRET_KEY="your_secure_random_secret_key_here" --app wordle-game

# Set production environment
fly secrets set FLASK_ENV="production" --app wordle-game
```

### Step 2: Update Google Cloud Console
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to APIs & Services > Credentials
3. Edit your OAuth 2.0 Client ID
4. Add these URIs:

**Authorized redirect URIs:**
```
https://wordle-game.fly.dev/auth/callback
```

**Authorized JavaScript origins:**
```
https://wordle-game.fly.dev
```

### Step 3: Generate a Secure Secret Key
You can generate a secure secret key using Python:

```python
import secrets
print(secrets.token_hex(32))
```

### Step 4: Verify Deployment
After setting the environment variables, restart the app:

```bash
fly deploy --app wordle-game
```

### Step 5: Test the Application
1. Visit: https://wordle-game.fly.dev/
2. Try logging in with Google OAuth
3. Play a game to test all functionality

## 🎯 Expected Results
- ✅ App loads without errors
- ✅ Google OAuth login works
- ✅ User stats are tracked
- ✅ Game functions properly
- ✅ Leaderboard displays correctly

## 🔍 Troubleshooting
If you encounter issues:

1. **Check app logs:**
   ```bash
   fly logs --app wordle-game
   ```

2. **Verify secrets are set:**
   ```bash
   fly secrets list --app wordle-game
   ```

3. **Check app status:**
   ```bash
   fly status --app wordle-game
   ```

## 🌟 Success!
Once completed, your Wordle app will be fully functional with:
- Permanent URL (no more changing ngrok URLs)
- Google OAuth authentication
- User progress tracking
- Multi-device access
- Professional cloud hosting

Your app will be accessible from anywhere in the world at:
**https://wordle-game.fly.dev/**

## Persistent SQLite storage

The authenticated app stores users, leaderboard statistics, and completed game
records in SQLite. In Fly production, `fly.toml` sets
`DATABASE_URL=sqlite:////data/wordle_users.db` and mounts the `wordle_data`
volume at `/data`. Locally, the app continues to use the default SQLite
database under Flask's `instance/` directory (or the `DATABASE_URL` from `.env`).

`app_with_auth.py` runs `db.create_all()` at startup. This creates the tables
when the mounted database is empty and leaves an existing schema intact.

Fly Machine count is not set in `fly.toml`. SQLite on a Fly Volume must use one
Machine because Fly Volumes are local to one Machine and do not replicate. Check
the current count first, then use these commands when you are ready to configure
and deploy:

```bash
fly scale show --app wordle-game
fly scale count 1 --region fra --app wordle-game
fly volumes create wordle_data --region fra --size 1 --app wordle-game
fly deploy --ha=false --app wordle-game
fly scale show --app wordle-game
fly volumes list --app wordle-game
```

If the app currently has more than one Machine, run the scale command to reduce
it to one. If it has zero Machines, skip scaling and use `fly deploy --ha=false`
to create one. The volume command creates billable storage; do not repeat it if
a `wordle_data` volume already exists. `--ha=false` prevents Fly from creating
an additional standby Machine on this deployment. Keep the app at one Machine
on later deploys as well. Since this app intentionally accepts losing its old
test records, a fresh empty volume can be used without importing the previous
SQLite file.
