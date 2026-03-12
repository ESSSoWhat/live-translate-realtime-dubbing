# Fix Google Sign-In ApiException 10 (Android)

ApiException **10** = **DEVELOPER_ERROR**: Google does not recognize your app. Fix it in **Google Cloud Console**.

## Exact values for this app

| Field | Value |
|-------|--------|
| **Package name** | `app.livetranslate.live_translate_mobile` |
| **SHA-1 (debug)** | `9D:CE:CE:66:A3:E1:5D:46:07:08:75:16:51:20:AB:1C:99:4D:5E:B1` |
| **SHA-1 (no colons)** | `9DCECE66A3E15D46070875165120AB1C994D5EB1` (use if Console rejects colons) |
| **Web client ID** | `683320997088-mi3jnr3lm66ftt0ccurqgnkvmf2fvv9v.apps.googleusercontent.com` |

## Steps in Google Cloud Console

1. Open **[Credentials](https://console.cloud.google.com/apis/credentials)** in the **same project** where your Web client ID was created.

2. **Create or edit the Android OAuth client**
   - Click **+ Create Credentials** → **OAuth client ID** (or edit the existing Android client).
   - Application type: **Android**.
   - **Name:** e.g. "Live Translate Android".
   - **Package name:** paste exactly  
     `app.livetranslate.live_translate_mobile`
   - **SHA-1 certificate fingerprint:** paste exactly  
     `9D:CE:CE:66:A3:E1:5D:46:07:08:75:16:51:20:AB:1C:99:4D:5E:B1`
   - Click **Create** or **Save**.

3. **OAuth consent screen**
   - Go to **OAuth consent screen**.
   - If publishing status is **Testing**, add the **Google account** you use on the device/emulator under **Test users**. Save.

4. **Wait & retry**
   - Wait 5–10 minutes for changes to propagate.
   - Force‑stop the app (or uninstall and reinstall), then run again with:
     ```bash
     flutter run -d <device> --dart-define=GOOGLE_WEB_CLIENT_ID=683320997088-mi3jnr3lm66ftt0ccurqgnkvmf2fvv9v.apps.googleusercontent.com
     ```

## Verify

- You must have an **Android** OAuth client (not only a Web client).
- The **Web** client ID you pass in the app must be from the **same Google Cloud project** as the Android client.
- Package name and SHA-1 must match exactly (no extra spaces; SHA-1 from the **debug** keystore when running debug builds).

## Get your current SHA-1 (if needed)

From project root:

```bash
cd android && ./gradlew signingReport
```

Use the **SHA1** line under **Variant: debug** for **:app**.
