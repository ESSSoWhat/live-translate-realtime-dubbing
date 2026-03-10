# Android build (Gradle)

**Requires JDK 17.** Using Java 25 (or other unsupported versions) causes:

- `Unsupported class file major version 69`
- `Can't use Java 25.x and Gradle 8.x to import Gradle project android`

**Fix:**

1. Install JDK 17 if needed (e.g. [Adoptium](https://adoptium.net/) or SDKMAN).
2. Use one of these (cross-platform; no hardcoded paths in the repo):
   - **Option A:** Set `JAVA_HOME` to your JDK 17 install (e.g. `C:\Program Files\Eclipse Adoptium\jdk-17.x.x` on Windows, or `export JAVA_HOME=...` on macOS/Linux). Re-open the project / re-import Gradle after changing.
   - **Option B (IDE):** In **Android Studio** or **IntelliJ**: File → Settings → Build, Execution, Deployment → Build Tools → Gradle → **Gradle JDK** → choose **17**.
   - **Option C (Cursor / VS Code):** In workspace settings: `"java.import.gradle.java.home": "C:\\\\path\\\\to\\\\jdk-17"` (use your actual JDK 17 path).
   - **Option D (local override, gitignored):** Add `org.gradle.java.home=<path-to-jdk17>` to `android/local.properties`. The Gradle wrapper will use it when `JAVA_HOME` is not set. CI should set `JAVA_HOME` instead.

The root `android/.java-version` file is set to `17` for tools that support it. *Gradle 9.1+ supports Java 25, but Flutter's Android Gradle plugin does not yet support Gradle 9.1, so JDK 17 is required for now.*

### Windows: "Different roots" (e.g. `google_sign_in_android:compileDebugUnitTestSources`)

If the project lives on a different drive than the default Pub cache (e.g. project on `S:\`, cache on `C:\`), Gradle can fail with "this and base files have different roots". Use a Pub cache on the **same drive** as the project:

```powershell
# From repo root or mobile/
$env:PUB_CACHE = "S:\Coding project\mobile\.pub-cache"   # or "$(Get-Location)\.pub-cache" when already in mobile/
flutter clean
flutter pub get
```

Then run/build Android again.
