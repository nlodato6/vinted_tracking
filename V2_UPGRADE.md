# 🚀 SpreadThrift V2 Architecture & Mobile App Store Roadmap
> *"From inbox alerts to bottom-line gains."*

---

## 📌 Executive Summary

**SpreadThrift** is currently built as a multi-tenant Streamlit web application backed by Python data engines (`src/html_parser.py`, `src/sheets/`, `src/email_parser.py`, and `src/analytics.py`) that sync directly into user-owned Google Sheets.

This document outlines the strategic transition from the current **V1 Streamlit prototype** to a commercial **V2 production architecture** that can be published to the **Apple App Store**, **Google Play Store**, and deployed on the **Web**.

---

## 🔍 V1 Current State: Streamlit Assessment

### What Works Well Today
* **Rapid Prototyping & Internal Use**: Immediate interactive dashboarding with Plotly and Streamlit widgets.
* **Full Python Ecosystem Access**: Direct in-memory execution of BeautifulSoup, Google Sheets API (`gspread`), and IMAP email fetchers.
* **Low Initial Development Cost**: Unified Python codebase without needing a separate frontend/backend API bridge.
* **Multi-Tenant OAuth**: Users can connect personal Google Drive credentials and work directly with their own spreadsheets.

### Hard Limitations for App Stores & Consumer Scale

| Challenge | Impact on Mobile & App Stores |
| :--- | :--- |
| **Apple App Store Rejection Risk** | Apple strictly enforces [Guideline 4.2 (Minimum Functionality)](https://developer.apple.com/app-store/review/guidelines/#minimum-functionality). An app that is essentially a website or WebView wrapper without deep native functionality is routinely rejected. |
| **Persistent WebSocket Model** | Streamlit maintains an open bidirectional WebSocket connection for every single client session. If hundreds of users open the app simultaneously, server RAM and CPU scale linearly because Python scripts rerun on the server rather than the client. |
| **Mobile UX & Interaction Friction** | File uploading (selecting saved HTML on an iPhone), table scrolling, and dropdown interactions feel desktop-centric rather than tactile and native. |
| **Lack of Native OS Features** | Streamlit cannot natively access the camera for barcode/receipt scanning, background push notifications, the iOS Share Sheet, or Apple In-App Purchases (IAP). |

---

## ⚖️ Framework Evaluation Matrix

To reach both the Web and the App Stores, several architectural patterns were evaluated:

| Framework | Platforms Supported | Pros | Cons | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **Expo / React Native** | **iOS, Android & Web** *(Single Codebase)* | • True native mobile UI & performance<br>• Fully compliant with Apple App Store & Google Play<br>• Native camera, push notifications, iOS Share Sheet, Apple Pay / IAP<br>• One codebase compiles to iOS, Android, and Web (`react-native-web`) | • Requires TypeScript/React for UI components<br>• Need to decouple Python backend via REST/JSON | ⭐ **Top Recommendation for App Stores + Web** |
| **Next.js + Capacitor / PWA** | **Web (SaaS) + Hybrid Mobile** | • Unbeatable web performance, SEO, and desktop usability<br>• Progressive Web App (PWA) installs directly from Safari/Chrome with 0 App Store fees (0% to Apple)<br>• Capacitor allows bundling into native binary wrappers | • Capacitor wrappers still risk Apple Guideline 4.2 review pushback if native UI polish isn't prioritized | **Best if Web SaaS is 90% of your business** |
| **Flutter** | **iOS, Android, Web & Desktop** | • High-performance skia canvas rendering<br>• Beautiful out-of-the-box cross-platform styling | • Dart programming language (new stack apart from Python & JS)<br>• Larger web bundle sizes | **Viable alternative if team prefers Dart** |

---

## 🏗️ Target V2 Architecture

In V2, the application separates into a **stateless REST backend** and a **cross-platform client**. **None of your Python business logic is discarded.**

```
                     ┌────────────────────────────────────────────────────────┐
                     │                  SpreadThrift Clients                  │
                     │                                                        │
                     │   📱 iOS App (App Store)      • Native camera          │
                     │   🤖 Android (Google Play)    • Push notifications     │
                     │   🌐 Web App (SaaS)           • iOS Share Sheet        │
                     │                                                        │
                     │            (React Native / Expo + React Web)           │
                     └───────────────────────────┬────────────────────────────┘
                                                 │ HTTPS / JSON Web Tokens
                                                 ▼
                     ┌────────────────────────────────────────────────────────┐
                     │                SpreadThrift Backend API                │
                     │              FastAPI (`src/server.py`)                 │
                     │                                                        │
                     │  • /api/v1/auth         (Google OAuth2 / JWT Sessions) │
                     │  • /api/v1/lots         (Inventory Lots CRUD)          │
                     │  • /api/v1/supplies     (Shipping Supplies & Cost Avg) │
                     │  • /api/v1/sync/html    (Closet & Orders HTML Parser)  │
                     │  • /api/v1/sync/email   (Gmail IMAP & Webhook Sync)    │
                     │  • /api/v1/analytics    (Plotly/JSON Metrics Engine)   │
                     └───────────────────────────┬────────────────────────────┘
                                                 │
                                                 ▼
                     ┌────────────────────────────────────────────────────────┐
                     │                      Data Layer                        │
                     │                                                        │
                     │  1. Primary User Store: Google Sheets (User's Drive)   │
                     │  2. Fast Cache / Auth: Redis or SQLite / Supabase      │
                     └────────────────────────────────────────────────────────┘
```

---

## 📱 Mobile-First Features Unlocked in V2

1. **iOS Share Sheet Integration**:
   - Resellers can open Vinted, view an order confirmation, tap **Share**, and send the order directly to **SpreadThrift** without ever manually saving or moving HTML files.
2. **Instant Push Notifications**:
   - Receive native push alerts the moment a Vinted sale email or accepted offer arrives, showing realized net profit instantly.
3. **Camera & Receipt Tag Scanning**:
   - Use the phone's native camera with OCR to snap thrift store haul receipts or clothing tags to automatically register new inventory lots.
4. **Offline Mode**:
   - View recent inventory counts, lots, and sales metrics even without an active internet connection, syncing when connectivity resumes.
5. **App Store Subscriptions (StoreKit / Google Play Billing)**:
   - Monetize with recurring monthly/annual subscriptions or tiered tracking limits.

---

## 💎 Monetization & Feature Tiering (Free vs. Paid)

SpreadThrift V2 implements a **Freemium** business model tailored for both the Apple App Store / Google Play and the Web SaaS. 

Resellers can start for free immediately to track their closet performance, while active sellers pay for automated background syncing and direct spreadsheet manipulation.

### Feature Tier Comparison Matrix

| Feature Category | Feature Description | 🆓 Free Tier | ⚡ Paid (SpreadThrift Pro) |
| :--- | :--- | :---: | :---: |
| **HTML Batch Import** | Upload & parse saved Vinted closet HTML files (`active` & `sold`) | ✅ **Unlimited** | ✅ **Unlimited** |
| **Order History Import** | Upload & parse saved Vinted orders HTML files | ✅ **Unlimited** | ✅ **Unlimited** |
| **Metrics & Analytics** | Interactive financial KPIs (Gross Revenue, Net Profit, Margin %) | ✅ **Included** | ✅ **Included** |
| **Sales Timeline Charts** | Historical sales trends, cumulative revenue, and profit timelines | ✅ **Included** | ✅ **Included** |
| **Category & Brand Breakdown** | Category performance leaderboards & top brand rankings | ✅ **Included** | ✅ **Included** |
| **Scatter Analytics** | Price vs. profit margin distribution charts | ✅ **Included** | ✅ **Included** |
| **Direct Email Sync** | Automated IMAP Gmail sync for incoming sale alerts & accepted offers | ❌ Paywalled | ✅ **Included (Automated)** |
| **Email Webhook Ingestion** | Instant real-time sale capture via inbound email webhooks | ❌ Paywalled | ✅ **Included** |
| **In-App Lot Creation** | Define and save new `Inventory_Lots` directly from the app UI | ❌ Paywalled | ✅ **Included** |
| **Live Lot Re-Assignment** | Assign and update inventory hauls to new listings from UI | ❌ Paywalled | ✅ **Included** |
| **Shipping Supplies Manager** | Add/edit packaging materials (mailers, tape, boxes) from UI | ❌ Paywalled | ✅ **Included** |
| **Live Spreadsheets Sync** | Write new listings, sales, and supply updates directly to Google Sheets | ❌ Read-Only / Preview | ✅ **Full 2-Way Sync** |
| **Push / Background Alerts** | Real-time native mobile push notifications on sales & price drops | ❌ Basic | ✅ **Priority Instant Alerts** |

---

## 🧪 Beta Tester & VIP Bypass System (Zero-Pay Access)

During beta testing, pre-launch, and for VIP power sellers, testers **must have unrestricted access to all Paid/Pro features without being charged**.

SpreadThrift V2 implements a **4-tier entitlement bypass system** to ensure seamless zero-cost access across iOS, Android, and Web:

### 1. Server-Side Email Whitelist (`BETA_TESTER_EMAILS`)
* **How it works**: The backend maintains a configuration list of authenticated Google emails granted automatic lifetime Pro status.
* **Implementation Pattern**:
  ```python
  BETA_TESTER_EMAILS = {
      "tsavoie90@gmail.com",
      "nicashlod@gmail.com",
      "beta-tester1@gmail.com",
  }

  def get_user_tier(email: str, subscription_status: dict) -> str:
      # 1. VIP/Beta Whitelist always grants Pro access
      if email.lower() in {e.lower() for e in BETA_TESTER_EMAILS}:
          return "pro_beta_tester"
      
      # 2. Check active Apple StoreKit / Google Play / Stripe subscription
      if subscription_status.get("is_active"):
          return "pro"
          
      return "free"
  ```
* **User Experience**: The user logs in via Google OAuth. The app immediately displays a **"SpreadThrift Pro — Beta Tester VIP"** badge and unlocks Email Sync and direct Spreadsheet updates.

### 2. Apple TestFlight Automatic Zero-Cost Purchases
* **How it works**: When distributing beta builds via **Apple TestFlight**, Apple automatically routes all In-App Purchases through the Apple Sandbox environment.
* **Cost to Tester**: **$0.00**. Testers can tap "Upgrade to Pro" in the app, and iOS displays a confirmation popup showing `[Environment: Sandbox] - You will not be charged`.
* **Benefit**: Allows you to test the actual purchase flow, receipt validation, and paywall UI before public release without anyone spending money.

### 3. Google Play Internal & Closed Testing License Testers
* **How it works**: In the Google Play Console, you add beta testers to **Settings $\rightarrow$ License Testing**.
* **Cost to Tester**: **$0.00**. Google Play Billing presents a test card that approves instantly with no real charge.

### 4. Secret In-App Redemption / Promo Codes
* **How it works**: Under **Settings $\rightarrow$ Redeem Beta Code**, testers can enter a secret promo phrase (e.g. `SPREADTHRIFT-VIP`) to immediately elevate their account to Pro.
* **Benefit**: Allows you to invite testers on the Web SaaS or outside of TestFlight without modifying server configuration files every time.
* **App Store Promo Codes**: Apple also provides developers with up to **1,000 free App Store promotional codes** per app version that redeem subscriptions for free directly through the App Store.

---

## 💳 App Store Monetization Architecture (RevenueCat / StoreKit)

For handling cross-platform subscriptions cleanly between iOS, Android, and Web:

1. **Recommended Subscription Engine: [RevenueCat](https://www.revenuecat.com/)**:
   * Official SDK for React Native (Expo) and Web.
   * Single API handling Apple StoreKit 2, Google Play Billing, and Stripe (Web).
   * Synchronizes user entitlements: if a user subscribes on their iPhone, their SpreadThrift Pro features are automatically unlocked when they log into the Web app.
2. **Pricing Structure**:
   * **Free**: $0/month (unlimited HTML imports, complete analytics & metrics).
   * **SpreadThrift Pro Monthly**: $4.99 – $9.99/month (automated email sync, direct 2-way Google Sheets editing, shipping supplies calculator, push notifications).
   * **SpreadThrift Pro Annual**: $49.99 – $79.99/year (with 7-day free trial).

---

## 🛡️ App Store & Legal Compliance Checklist

To ensure approval by Apple App Review and Google Play without delays or rejections, SpreadThrift must fulfill this compliance checklist prior to store submission:

### 1. Intellectual Property & Third-Party Trademark Compliance
- [x] **Distinct Brand Name**: The app is named **SpreadThrift** rather than *Vinted Assistant* or *Vinted Tracker* (complying with Apple Guideline 5.2.1 and Google Play Impersonation Policy).
- [x] **Descriptive Subtitle Format**: Any store subtitle must use nominative fair use (e.g., `SpreadThrift: Reseller Hub` or `SpreadThrift: Inventory for Vinted`), never implying official affiliation.
- [x] **Prominent Third-Party Disclaimers**: Displayed prominently in:
  - App Store / Google Play long description
  - App Login & Onboarding screens
  - App Settings / Legal & Trademarks expander
  - Public website & repository footer
- [x] **Original App Icon & Graphic Assets**: App icon and marketing screenshots must use original SpreadThrift artwork with no trademarked logos (no Vinted cyan 'V', no Google Sheets logo) without explicit written licensing.
- [x] **Nominative Fair Use Language**: All references to Vinted, Google Sheets, Gmail, and Facebook Marketplace are strictly factual and descriptive.

### 2. Apple App Store Review Guidelines Checklist
- [ ] **Guideline 4.2 (Minimum Functionality)**: Ensure native mobile utility (camera barcode/receipt scanner, iOS Share Extension for orders, interactive charts, and push notifications) so the app is never categorized as a simple repackaged webview.
- [ ] **Guideline 3.1.1 (In-App Purchase)**: Digital Pro subscriptions sold within the iOS app MUST use Apple StoreKit / In-App Purchase. No external links to Stripe, PayPal, or web payment checkouts inside the iOS binary.
- [ ] **Guideline 4.8 (Sign in with Apple)**: Because Google Sign-In is offered as a third-party authentication method, **Sign in with Apple** must also be implemented with equal prominence.
- [ ] **Guideline 5.1.1(v) (Account & Data Deletion)**: Any app that supports account creation must provide an easy, discoverable in-app mechanism for users to delete their account and associated data.
- [ ] **Guideline 5.1.1 (Privacy Policy & Privacy Nutrition Labels)**: Provide a live Privacy Policy URL detailing data collection: Google OAuth tokens (stored securely in iOS Keychain), spreadsheet ID, and parsed email data.
- [ ] **Guideline 2.1 (App Completeness & Review Notes)**: Provide Apple reviewers with a pre-configured demo Google account and sample spreadsheet in App Store Connect review notes so they can test immediately without setup friction.

### 3. Google Play Developer Policy Checklist
- [ ] **Impersonation & Brand Policy**: App title, developer name, and assets do not create confusion with official platforms.
- [ ] **Google Play Billing**: In-app purchases for Pro tier must integrate the Google Play Billing Library.
- [ ] **Google API Services User Data Policy**: IMAP email sync and Google Sheets access comply with Google Limited Use requirements (user data is used solely for the inventory tracker, never sold or shared).
- [ ] **Target API Level**: Built against the latest Android SDK (Android 14 / API level 34+).

### 4. Legal Documents & Policies Checklist
- [ ] **Privacy Policy**: Hosted at a publicly accessible URL (e.g. `https://spreadthrift.com/privacy`).
- [ ] **Terms of Service (ToS) / End User License Agreement (EULA)**: Hosted at `https://spreadthrift.com/terms` and linked on the purchase screen (standard Apple EULA or custom terms).
- [ ] **Support Contact URL**: Working support email or contact portal (`support@spreadthrift.com`).

---

## 📋 Phased Rollout Plan

### Phase 1: Web MVP Launch (Immediate)
* **Goal**: Launch SpreadThrift on the web using the existing Streamlit app to validate product-market fit and onboard real Vinted resellers immediately.
* **Hosting Options**:
  * **Render / Railway / GCP Cloud Run**: Run via Docker container with a custom domain (`app.spreadthrift.com`).
  * **Streamlit Community Cloud**: Free hosting directly from the GitHub repository.
* **Deliverable**: Live web product generating user feedback while V2 mobile is designed.

### Phase 2: Decouple Backend into REST API
* **Goal**: Expand [`src/server.py`](file:///Users/nicol/Desktop/vinted_tracking/src/server.py) into a complete FastAPI REST backend.
* **Endpoints to Expose**:
  - `POST /api/auth/google`: Exchange auth codes for JWT session tokens.
  - `GET /api/sheets/lots` & `POST /api/sheets/lots`: Retrieve and create inventory hauls.
  - `GET /api/sheets/supplies` & `POST /api/sheets/supplies`: Manage shipping supplies and packaging cost deductions.
  - `POST /api/parse/html`: Upload closet/order HTML documents and return parsed listing JSON.
  - `GET /api/analytics/summary`: Return structured KPIs, category breakdowns, and sales timelines.
* **Deliverable**: Fully documented Swagger/OpenAPI documentation at `/docs`.

### Phase 3: Expo Cross-Platform Frontend Build
* **Goal**: Build modern cross-platform UI with React Native (Expo).
* **Key Components**:
  - Bottom Tab Navigation (`Home / Metrics`, `Inventory & Lots`, `Sync Operations`, `Supplies`, `Settings`).
  - Native file picker and iOS Share Extension target.
  - Native charts via Victory Native or React Native Skia.
* **Deliverable**: Single app running simultaneously on iOS Simulator, Android Emulator, and Web browser.

### Phase 4: App Store Preparation & Submission
* **Prerequisites**:
  - Apple Developer Program account ($99/year).
  - Google Play Console developer account ($25 one-time).
  - Privacy Policy URL and Terms of Service (required by both stores).
  - Sign in with Apple implementation (required by Apple if Google Sign-In is offered).
* **Deliverable**: Live listings on the Apple App Store and Google Play Store.

---

## 🎯 Summary Decision Guide

* **If your timeline is NOW**: Deploy the Streamlit app to **Render** or **Railway** today under your custom domain. It is already built, tested (53/53 tests passing), and branded.
* **If your end vision is the App Store**: Follow the roadmap in this document to transition the UI to **Expo (React Native)** backed by your existing Python engine.
