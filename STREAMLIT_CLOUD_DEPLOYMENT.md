# ☁️ Deploying Thread Count to Streamlit Community Cloud

This guide provides step-by-step instructions for deploying and running **Thread Count** on [Streamlit Community Cloud](https://share.streamlit.io/).

---

## 🚀 Step 1: Deploy from GitHub

1. Push all latest changes to your GitHub repository:
   ```bash
   git push origin master
   ```
2. Navigate to [share.streamlit.io](https://share.streamlit.io/) and log in with your GitHub account.
3. Click **"New app"** (or **"Create app"**).
4. Configure the deployment settings:
   * **Repository**: `nlodato6/vinted_tracking`
   * **Branch**: `master`
   * **Main file path**: `app.py`
   * **App URL** (optional): Choose a custom subdomain (e.g. `thread-count.streamlit.app`).
5. Click **"Deploy!"**.

---

## 🔑 Step 2: Configure Streamlit Cloud Secrets

Once your app is created in Streamlit Cloud:
1. In the Streamlit Cloud dashboard, click the **Settings** icon (gear icon) in the bottom-right corner $\rightarrow$ click **"Secrets"**.
2. Copy and paste the contents from [`.streamlit/secrets.toml.template`](./.streamlit/secrets.toml.template):

```toml
# Google OAuth 2.0 Web Client Credentials
GOOGLE_CLIENT_ID = "162785904429-j2pd34jjgb00n50au5j0uiuds9nc1od3.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_REDIRECT_URI = "https://your-app-name.streamlit.app"

# Administrator Access & Manual Fallback Login
ADMIN_EMAILS = "nicashlod@gmail.com,admin@spreadthrift.com"
ADMIN_PASSWORD = "PassWord123!@#"

# Default Google Spreadsheet Tracker
GOOGLE_SHEET_ID = "1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg"
GOOGLE_SHEET_TITLE = "Vinted Sales & Inventory Tracker"
```

3. Update `GOOGLE_REDIRECT_URI` with the exact URL of your deployed Streamlit app (e.g., `https://thread-count.streamlit.app`).
4. Click **"Save"**.

---

## 🌐 Step 3: Add Authorized Redirect URI in Google Cloud Console

To allow users (and your admin account) to sign in with Google OAuth on Streamlit Cloud:
1. Open [Google Cloud Console Credentials](https://console.cloud.google.com/apis/credentials).
2. Click on your OAuth 2.0 Client ID (**"Web client 1"**).
3. Under **Authorized redirect URIs**, click **"+ ADD URI"**.
4. Enter your Streamlit Cloud app URL:
   ```text
   https://your-app-name.streamlit.app
   ```
   *(Note: Do NOT include trailing slashes or subpaths like `/oauth2callback`).*
5. Under **Authorized JavaScript origins**, click **"+ ADD URI"**:
   ```text
   https://your-app-name.streamlit.app
   ```
6. Click **"Save"** at the bottom of the page.

---

## 🔐 Step 4: Login & Testing

When opening your app on Streamlit Cloud:

### Method A: Google Sign-In (Admin: `nicashlod@gmail.com`)
* Click **"🔑 Sign in with Google"**.
* Log in with **`nicashlod@gmail.com`**.
* The app recognizes your email as a designated administrator (`👑 Administrator Session`), granting full access to user telemetry, error logs, and inventory tools.

### Method B: Manual Fallback Sign-In
* Use the **"🔐 Manual Login (Fallback)"** container:
  * **Email**: `nicashlod@gmail.com` (or `nicashlod@gmail`)
  * **Password**: `PassWord123!@#`
* Click **"Sign In with Email & Password"**.
* This logs you in immediately with full Administrator access—even if Google OAuth has not yet been configured on the cloud domain!
