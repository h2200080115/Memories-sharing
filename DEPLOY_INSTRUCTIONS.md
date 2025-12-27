# Deployment Instructions for Render

## Prerequisites
- A GitHub or GitLab account.
- A Render.com account.
- This project pushed to a repository.

## Steps

1. **Push your code**: Ensure all changes (including `Procfile` and `requirements.txt`) are committed and pushed to your git repository.

2. **Create Web Service**:
   - Log in to [Render dashboard](https://dashboard.render.com).
   - Click "New +" and select **Web Service**.
   - Connect your repository.

3. **Configure Service**:
   Render should auto-detect the configuration, but verify these settings:
   - **Name**: `friend-trip` (or your preferred name)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`

4. **Deploy**:
   - Click **Create Web Service**.
   - Wait for the build to finish.

## Important Note on Database
**Warning**: This application currently uses SQLite (`photos.db`). 
On Render's standard Web Services (and most cloud platforms), the filesystem is **ephemeral**. This means:
- Any photos uploaded will be **deleted** when the app restarts or redeploys.
- All user accounts and trips stored in the database will be **reset**.

### Solution for Production
To make data persistent:
1. Create a **PostgreSQL** database on Render.
2. Copy the "Internal Database URL" from the database dashboard.
3. In your Web Service settings, add an Environment Variable:
   - Key: `DATABASE_URL`
   - Value: (paste the URL)
4. Update `app.py` to read this variable:
   ```python
   app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL').replace('postgres://', 'postgresql://') or f'sqlite:///{DB_PATH}'
   ```
   *(Note: The replace fix is needed because SQLAlchemy requires `postgresql://` but Render provides `postgres://`)*
