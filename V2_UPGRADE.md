# 🚀 InVintory V2 Architecture & Mobile App Store Roadmap
> *"From inbox alerts to bottom-line gains."*

---

## 📌 Executive Summary

**InVintory** is currently built as a multi-tenant Streamlit web application backed by Python data engines (`src/html_parser.py`, `src/sheets/`, `src/email_parser.py`, and `src/analytics.py`) that sync directly into user-owned Google Sheets.

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
                     │                   InVintory Clients                    │
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
                     │                 InVintory Backend API                  │
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
   - Resellers can open Vinted, view an order confirmation, tap **Share**, and send the order directly to **InVintory** without ever manually saving or moving HTML files.
2. **Instant Push Notifications**:
   - Receive native push alerts the moment a Vinted sale email or accepted offer arrives, showing realized net profit instantly.
3. **Camera & Receipt Tag Scanning**:
   - Use the phone's native camera with OCR to snap thrift store haul receipts or clothing tags to automatically register new inventory lots.
4. **Offline Mode**:
   - View recent inventory counts, lots, and sales metrics even without an active internet connection, syncing when connectivity resumes.
5. **App Store Subscriptions (StoreKit / Google Play Billing)**:
   - Monetize with recurring monthly/annual subscriptions or tiered tracking limits.

---

## 📋 Phased Rollout Plan

### Phase 1: Web MVP Launch (Immediate)
* **Goal**: Launch InVintory on the web using the existing Streamlit app to validate product-market fit and onboard real Vinted resellers immediately.
* **Hosting Options**:
  * **Render / Railway / GCP Cloud Run**: Run via Docker container with a custom domain (`app.invintory.com`).
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
