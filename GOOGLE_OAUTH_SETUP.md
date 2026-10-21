# 🔑 Google OAuth 2.0 Setup Guide for SpreadThrift
> Step-by-step instructions to enable **"Sign in with Google"** for multi-tenant Google Sheets access.

---

## 🎯 What This Does
With Google OAuth enabled:
1. Users visit your SpreadThrift web app and click **"🔑 Sign in with Google"**.
2. They authenticate with their own Google account and grant permission for SpreadThrift to access their Google Sheets.
3. SpreadThrift reads and updates their personal spreadsheet directly. **You incur $0 in Google API bills** because each user uses their own free Google API quota.

---

## 🛠️ Step 1: Open Google Cloud Console & Enable APIs

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Select an existing project or click the project dropdown at the top $\rightarrow$ **New Project** (e.g. name it `SpreadThrift`).
3. In the top search bar, search for **Google Sheets API** $\rightarrow$ click it $\rightarrow$ click **Enable**.
4. In the top search bar, search for **Google Drive API** $\rightarrow$ click it $\rightarrow$ click **Enable**.

---

## 🛡️ Step 2: Configure OAuth Consent Screen

1. In the left navigation menu, go to **APIs & Services** $\rightarrow$ [**OAuth consent screen**](https://console.cloud.google.com/apis/credentials/consent).
2. Under **User Type**, select **External** $\rightarrow$ click **Create**.
3. **App Information**:
   * **App name**: `SpreadThrift`
   * **User support email**: Select your email address.
   * **Developer contact information**: Enter your email address.
   * Click **Save and Continue**.
4. **Scopes**:
   * Click **Add or Remove Scopes**.
   * In the filter box, search and select these scopes:
     * `.../auth/spreadsheets` (Google Sheets API - see, edit, create, and delete spreadsheets)
     * `.../auth/drive.file` (Google Drive API - see, create, and delete its own configuration files)
     * `.../auth/userinfo.email` (See primary Google Account email)
     * `.../auth/userinfo.profile` (See personal info including photo)
     * `openid`
   * Click **Update** $\rightarrow$ click **Save and Continue**.
5. **Test Users** *(Crucial while app is in testing mode)*:
   * Click **+ Add Users**.
   * Type your personal Google email (and any tester emails you want to allow to log in).
   * Click **Add** $\rightarrow$ click **Save and Continue**.
6. Review the summary $\rightarrow$ click **Back to Dashboard**.

---

## 🔑 Step 3: Create OAuth Client ID Credentials

1. In the left navigation menu, go to [**Credentials**](https://console.cloud.google.com/apis/credentials).
2. Click **+ Create Credentials** (at the top) $\rightarrow$ select **OAuth client ID**.
3. **Application type**: Select **Web application**.
4. **Name**: `SpreadThrift Web Client`.
5. **Authorized redirect URIs** *(Most important step)*:
   * Click **+ Add URI**.
   * Enter:
     ```
     http://localhost:8501
     ```
   * *(Optional)* If you plan to deploy to production (e.g., Streamlit Cloud or Render), also add your production URL:
     ```
     https://your-app-name.streamlit.app
     ```
   * > [!IMPORTANT]
     > Do **NOT** add a trailing slash to `http://localhost:8501`. It must match the redirect URI sent by the app exactly.
6. Click **Create**.
7. A dialog will appear with:
   * **Your Client ID** (e.g., `123456789012-abcdefghijklmnopqrstuvwxyz.apps.googleusercontent.com`)
   * **Your Client Secret** (e.g., `GOCSPX-abcdefghijklmnopqrstuvwxyz123456`)

---

## ⚙️ Step 4: Add Your Keys to SpreadThrift

You can configure your keys in either **Option A** (`.env`) or **Option B** (`.streamlit/secrets.toml`).

### Option A: Via `.env` (Recommended for Local Dev)
Open the `.env` file in the root of the project and add:

```env
GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your_client_secret_here
```

### Option B: Via `.streamlit/secrets.toml` (Recommended for Streamlit Cloud)
Create `.streamlit/secrets.toml` (this file is ignored by git):

```toml
GOOGLE_CLIENT_ID = "your_client_id_here.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-your_client_secret_here"
```

---

## 🚀 Step 5: Test the Flow

1. Open your browser to [http://localhost:8501](http://localhost:8501).
2. If you are already logged into Local Mode, click **Log Out** in the sidebar.
3. You will now see the blue button:
   ```
   🔑 Sign in with Google
   ```
4. Click **Sign in with Google**.
5. Select your Google Account (the one you added to Test Users in Step 2).
6. Click **Continue** on the Google permission screen.
7. Google will redirect back to `http://localhost:8501`, log you in, and display your Google profile name and photo in the sidebar!

---

## ❓ Troubleshooting

| Error | Cause | Solution |
| :--- | :--- | :--- |
| **"Error 400: redirect_uri_mismatch"** | The redirect URI in Google Cloud Console doesn't match the current browser URL. | Go to Credentials $\rightarrow$ your OAuth Client ID $\rightarrow$ ensure `http://localhost:8501` is listed under **Authorized redirect URIs** exactly (no trailing slash). |
| **"Access blocked: SpreadThrift has not completed the Google verification process"** | Your Google Cloud project is in "Testing" mode and your email isn't in Test Users. | Go to **OAuth consent screen** $\rightarrow$ **Test users** $\rightarrow$ click **+ Add Users** and add your Google email address. |
| **"API [sheets.googleapis.com] not enabled"** | The Google Sheets API is not activated on your Google Cloud project. | Search for "Google Sheets API" in Google Cloud Console and click **Enable**. |
| **"InsecureTransportError"** | Trying to use OAuth over `http://` instead of `https://`. | Handled automatically: SpreadThrift automatically enables `OAUTHLIB_INSECURE_TRANSPORT=1` on localhost. |
