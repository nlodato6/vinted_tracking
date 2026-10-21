# 🔑 How to Generate a Google App Password for Gmail Email Sync

This guide walks you through generating a 16-character **Google App Password** for **`tsavoie90@gmail.com`**. 

This password allows our Python CLI (`sync-emails`) and the Streamlit frontend to securely read incoming Vinted sales, offers, and favorite emails via IMAP without exposing your main Google password.

---

## 📌 Prerequisites: Enable 2-Step Verification
Google requires **2-Step Verification (2FA)** to be enabled on your account before it allows creating App Passwords.

1. Open your browser and make sure you are logged into **`tsavoie90@gmail.com`**.
2. Go to: [https://myaccount.google.com/security](https://myaccount.google.com/security)
3. Under **"How you sign in to Google"**, verify that **2-Step Verification** is turned **On**.
   - If it is Off, click on it and follow the prompts to add your phone number.

---

## 🛠️ Step-by-Step: Generate the 16-Character App Password

1. Go directly to the App Passwords page:   👉 **[https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
**
   *(Or type **"App passwords"** into the search bar at the very top of your Google Account page).*

2. Enter your account password if prompted to verify your identity.

3. Under **"App name"**, enter a recognizable name, for example:
   ```text
   Vinted Tracker
   ```

4. Click **Create**.

5. A modal window will appear displaying a **16-character code** in a yellow box (e.g. `abcd efgh ijkl mnop`).

6. **Copy this 16-character code**. (You can keep the spaces or remove them; Python handles both).

7. Click **Done**.

---

## 📂 Where to Add the App Password

### 1. In Your Local Project (`.env` file)
Open the [`.env`](./.env) file in your project directory and set:

```bash
GMAIL_USER=tsavoie90@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
```

> [!NOTE]
> The `.env` file is in `.gitignore`, meaning this password will **never** be pushed to GitHub.

---

### 2. In Streamlit Community Cloud (When Deployed)
When you deploy your Streamlit frontend to [share.streamlit.io](https://share.streamlit.io):

1. Open your app dashboard and click **Settings** (gear icon) $\rightarrow$ **Secrets**.
2. Add your credentials into the encrypted secrets editor:
   ```toml
   GMAIL_USER = "tsavoie90@gmail.com"
   GMAIL_APP_PASSWORD = "abcd efgh ijkl mnop"
   ```
3. Click **Save**.

---

## 🚀 How to Test Your Email Sync

Once saved in `.env`, run the sync command from your terminal:

```bash
PYTHONPATH=. venv/bin/python -m src.cli sync-emails
```

### What happens when you run this:
* Connects securely to `imap.gmail.com:993` via SSL.
* Searches for recent transactional emails from `no-reply@vinted.com`.
* Automatically processes:
  - 💰 **Sales**: Transitions garment from `Item_Listings` to `Sold_Items` and calculates profit.
  - 🏷️ **Offers**: Logs buyer offer amounts into `Price_History`.
  - ❤️ **Favorites**: Increments favorite counters.
* Stamps the live status banner in [`Analytics_Dashboard`](https://docs.google.com/spreadsheets/d/1osw6Osf37GMeCEf3dXA6_TqaUPCCfsUg4VS5HIKLGWg/edit) with the latest timestamp.

---

## 🔒 Security & Revocation
* You can revoke this App Password at any time with a single click at [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) by clicking the trash can icon next to `Vinted Tracker`.
* This password only grants IMAP/email access and cannot be used to modify your Google account settings.
