import java.io.File
import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("dev.flutter.flutter-gradle-plugin")
}

// Load keystore properties for release signing
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}

android {
    namespace = "com.livetranslate"
    compileSdk = flutter.compileSdkVersion

    // Flutter's CI installs the NDK at a non-standard path.
    val systemNdkPath: String? = System.getenv("ANDROID_NDK_PATH")
    if (systemNdkPath != null) {
        ndkVersion = flutter.ndkVersion
        ndkPath = systemNdkPath
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.livetranslate"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        // Release signing configuration (only if key.properties exists)
        if (keystorePropertiesFile.exists()) {
            create("release") {
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
                storeFile = file(keystoreProperties["storeFile"] as String)
                storePassword = keystoreProperties["storePassword"] as String
            }
        }
    }

    buildTypes {
        release {
            // Use release signing if available, otherwise use debug (for development)
            if (keystorePropertiesFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            } else {
                signingConfig = signingConfigs.getByName("debug")
            }
        }
    }
}

flutter {
    source = "../.."
}

// Copy release APK to Flutter-expected path (build/app/outputs/flutter-apk) so
// "flutter build apk" can find it when project and Pub cache are on different drives.
afterEvaluate {
    tasks.named("assembleRelease") {
        doLast {
            val apk = file(layout.buildDirectory.get().asFile.resolve("outputs/apk/release/app-release.apk"))
            val destDir = rootProject.file("../build/app/outputs/flutter-apk")
            if (apk.exists()) {
                destDir.mkdirs()
                apk.copyTo(File(destDir, "app-release.apk"), overwrite = true)
            }
        }
    }
}

