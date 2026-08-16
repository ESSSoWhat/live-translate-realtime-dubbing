# Fix Google Sign-In ApiException 10 (Android)

ApiException **10** = **DEVELOPER_ERROR**: Google does not recognize your app.
Fix it in **Google Cloud Console** (Firebase is not required).

## Exact values for this app

| Field | Value |
|-------|--------|
| **Package name** | `app.livetranslate.live_translate_mobile` |
| **SHA-1 (debug)** | `9D:CE:CE:66:A3:E1:5D:46:07:08:75:16:51:20:AB:1C:99:4D:5E:B1` |
| **SHA-1 (no colons)** | `9DCECE66A3E15D46070875165120AB1C994D5EB1` |
| **Android client ID** (Cloud Console only — do **not** put in the app) | `683320997088-im2noi5nr7274jciu23l2gp9t4np5jl3.apps.googleusercontent.com` |
| **Web client ID** (used in app as `serverClientId`) | `683320997088-nbqaieaucd9rnpgn1ha2fvsjhqqh47m0.apps.googleusercontent.com` |

## Steps in Google Cloud Console

1. Open **[Credentials](https://console.cloud.google.com/apis/credentials)** in project **livetranslate-488616** (number `683320997088`).

2. **Create or edit an Android OAuth client**
   - **+ Create Credentials** → **OAuth client ID** (or edit existing Android client
     `683320997088-im2noi5nr7274jciu23l2gp9t4np5jl3.apps.googleusercontent.com`).
   - Application type: **Android**
   - **Package name:** `app.livetranslate.live_translate_mobile` (exact, no typos)
   - **SHA-1:** `9D:CE:CE:66:A3:E1:5D:46:07:08:75:16:51:20:AB:1C:99:4D:5E:B1`
     (must be **SHA-1**, not SHA-256)
   - Save — open the client again and confirm the fingerprint is listed

3. Keep a separate **Web application** OAuth client — that ID is what the app uses
   (`GOOGLE_WEB_CLIENT_ID` / `ApiConfig.defaultGoogleWebClientId`). Do **not** put the
   Android client ID in the app.

4. **OAuth consent screen**
   - If status is **Testing**, add the Google account signed into the emulator under **Test users**.

5. Clear Play services cache on the emulator (helps after OAuth changes):
   ```bash
   adb shell pm clear com.google.android.gms
   ```
   Then wait 5–10 minutes, uninstall the app, rebuild/reinstall, and try again:
   ```bash
   flutter run -d <device> --dart-define=GOOGLE_WEB_CLIENT_ID=683320997088-nbqaieaucd9rnpgn1ha2fvsjhqqh47m0.apps.googleusercontent.com
   ```

## Verify

- You need **both** an Android OAuth client (package + SHA-1) and a Web client (in the app).
- Both must be in the **same** Google Cloud project.
- For Play Store builds, also add the **Play App Signing** SHA-1 from Play Console → App integrity.

## Get your current SHA-1

```bash
keytool -keystore "%USERPROFILE%\.android\debug.keystore" -storepass android -list -v
```

Or:

```bash
cd mobile/android && ./gradlew :app:signingReport
```

Use the **SHA1** under **Variant: debug**.
