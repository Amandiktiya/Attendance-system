# Supabase Storage Setup

This app can save profile photos and application files to Supabase Storage.

## 1. Create Supabase Bucket

In Supabase:

1. Open your project.
2. Go to Storage.
3. Create a bucket, for example:

```text
attendance-files
```

The app stores files under:

```text
profiles/
applications/
```

## 2. Add Render Environment Variables

In Render > your web service > Environment, add:

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=attendance-files
```

Use the `service_role` key on the server only. Do not put it in frontend JavaScript.

## 3. Deploy

After setting the variables, redeploy the Render service.

When these variables are present:

- Profile photos upload to Supabase Storage.
- Application files upload to Supabase Storage.
- View/download routes read files from Supabase Storage.

When these variables are missing, the app falls back to local `uploads/`.

## Database Note

This change moves files to Supabase Storage. Student/faculty/attendance records still use the configured database.

For full Supabase database storage, migrate from SQLite (`attendance.db`) to Supabase PostgreSQL and update the app database layer.
