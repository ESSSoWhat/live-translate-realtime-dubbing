# Android host project (Flutter)

This directory is a **root-level Android Gradle wrapper** (`applicationId` **com.livetranslate**). The primary Flutter app lives under [`mobile/`](../mobile/); its Android tree is [`mobile/android/`](../mobile/android/) (**app.livetranslate.live_translate_mobile**). CI builds from `mobile/` — open this project only if you maintain the `com.livetranslate` variant. See the main [README](../README.md).

## JDK requirement

- Use **JDK 17** (matches `compileOptions` / `JavaVersion.VERSION_17` in `app/build.gradle.kts`).
- **Do not** commit IDE-specific JDK paths (e.g. Eclipse Buildship `java.home` under `.settings/`).

### Recommended setup

1. Install a JDK 17 distribution (e.g. [Eclipse Temurin](https://adoptium.net/), Microsoft Build of OpenJDK, or Android Studio’s bundled JBR) into a **normal** location, for example:
   - Windows: `C:\Program Files\Eclipse Adoptium\jdk-17.x.x-hotspot\`
   - macOS: `/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home`
   - Linux: `/usr/lib/jvm/java-17-openjdk` (distro-specific)
2. Point **`JAVA_HOME`** at that installation and ensure `java -version` reports 17.
3. Android Studio / IntelliJ: set **Gradle JDK** to JDK 17 (Settings → Build → Build Tools → Gradle).

### Gradle JVM

- The build uses whatever JVM Gradle runs on—typically **`JAVA_HOME`** when you use the wrapper from a shell.
- Optional: set **`org.gradle.java.home`** in **`local.properties`** (gitignored) if you must pin a path without changing `JAVA_HOME`.
- Do **not** add `org.gradle.java.home` to committed files; keep paths out of the repo.
