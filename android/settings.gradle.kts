pluginManagement {
    val flutterSdkPath =
        run {
            val properties = java.util.Properties()
            file("local.properties").inputStream().use { properties.load(it) }
            val flutterSdkPath = properties.getProperty("flutter.sdk")
            require(flutterSdkPath != null) { "flutter.sdk not set in local.properties" }
            flutterSdkPath
        }

    includeBuild("$flutterSdkPath/packages/flutter_tools/gradle")

    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}


// Explicitly disable dependency locking for buildscript classpath
// This prevents errors when dependencies are resolved but not in lock state
// This must be done before plugins are applied
buildscript {
    configurations.classpath {
        resolutionStrategy.deactivateDependencyLocking()
    }
}

plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.13.1" apply false
    id("org.jetbrains.kotlin.android") version "2.2.21" apply false
}

// Set root project directory explicitly to prevent Gradle from scanning parent directories
// This must be done early to prevent discovery of Flutter SDK benchmark projects
rootProject.name = "android"
rootProject.projectDir = file(".")

// Only include the app project - don't auto-discover other projects
// This prevents Gradle from evaluating Flutter SDK benchmark projects
// The Flutter plugin loader will automatically include Flutter plugins
include(":app")
project(":app").projectDir = file("app")

