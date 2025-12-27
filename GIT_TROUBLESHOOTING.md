# Git Push Troubleshooting

The push failed with a **403 Permission Denied** error. This usually happens with Fine-grained Personal Access Tokens if they don't have the write permissions.

## How to Fix the Token

1. Go to [GitHub Developer Settings > Personal access tokens > Fine-grained tokens](https://github.com/settings/tokens?type=beta).
2. Click on the token you created.
3. Under **"Repository access"**, ensure "Only select repositories" includes `trip-friends` (or "All repositories").
4. Under **"Permissions"**:
   - Click **"Repository permissions"**.
   - Find **"Contents"**.
   - Change explicit access from "Read-only" or "No access" to **"Read and write"**.
5. Save the changes.
6. Try running the push command again in your terminal:
   ```powershell
   git push -u origin main
   ```
   (You will need to paste the token again when prompted, or configured via credential manager).
