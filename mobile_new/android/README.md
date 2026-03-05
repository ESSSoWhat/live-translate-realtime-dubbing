# Android build (Gradle)

**Requires JDK 17.** Using Java 25 (or other unsupported versions) causes:

- `Unsupported class file major version 69`
- `Can't use Java 25.0.2 and Gradle 8.9 to import Gradle project android`

**Fix:**

1. Install JDK 17 if needed (e.g. [Adoptium](https://adoptium.net/) or SDKMAN).
2. Either:
   - **Option A:** Set `JAVA_HOME` to your JDK 17 install (e.g. `C:\Program Files\Eclipse Adoptium\jdk-17.x.x`) and ensure no other Java is first on `PATH`, then re-open the project / re-import Gradle, or  
   - **Option B (IDE):** In **Android Studio** or **IntelliJ**: File → Settings → Build, Execution, Deployment → Build Tools → Gradle → **Gradle JDK** → choose **17**.  
   - **Option C (Cursor / VS Code):** Set Gradle to use JDK 17: add to `.vscode/settings.json` (or workspace settings): `"java.import.gradle.java.home": "C:\\\\path\\\\to\\\\jdk-17"` (use your actual JDK 17 path). Or in `gradle.properties`: `org.gradle.java.home=C:\\path\\to\\jdk-17`.

The root `android/.java-version` file is set to `17` for tools that support it. *Gradle 9.1+ supports Java 25, but Flutter's Android Gradle plugin does not yet support Gradle 9.1, so JDK 17 is required for now.*
