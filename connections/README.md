# Connections Puzzle Administration

The admin area is available at `/admin/connections` after a Google-authenticated account has been explicitly granted `User.is_admin`.

## Local setup and first administrator

1. Configure the local `.env` to point `DATABASE_URL` at the intended local SQLite database and set the normal Flask secret/OAuth settings.
2. Start the application once with `python app_with_auth.py`. Startup creates missing tables and applies the additive SQLite schema upgrade, including the default-false `user.is_admin` column. Existing users remain regular users.
3. With that same local environment selected, grant access to the intended existing Google account:

   ```powershell
   flask --app app_with_auth grant-connections-admin EMAIL_OF_EXISTING_ACCOUNT
   ```

   The command resolves the supplied email against an existing account, displays the matched account, and requires an interactive confirmation before changing its role. It does not create accounts or assign administrator rights to anyone else.
4. Sign in through the normal Google flow and open `/admin/connections`.

The command operates on the database selected by `DATABASE_URL`; verify the local environment before running it. The application startup migration only adds the admin field and Connections tables, and does not remove or rebuild existing tables.

## Puzzle readiness

Draft puzzles may have empty category names or word slots. Saving as Review or Approved requires four named categories and 16 nonempty, case-insensitively unique words. Archived puzzles remain in the library for administration and are reserved from future selection by status.
