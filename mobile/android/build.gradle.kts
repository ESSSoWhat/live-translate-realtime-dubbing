// Gradle 8.x does not support running on Java 25. Use JDK 17 for this project (see android/README.md).
val javaMajor = JavaVersion.current().majorVersion.toIntOrNull() ?: 0
if (javaMajor >= 25) {
    throw GradleException(
        "Android build requires JDK 17 or 21. Current: Java $javaMajor. " +
        "Set JAVA_HOME to JDK 17 (or in IDE: Gradle JDK → 17). See android/README.md."
    )
}

// Define flutter SDK versions at root level for plugins that need them
val flutterCompileSdkVersion by extra(36)
val flutterTargetSdkVersion by extra(35)
val flutterMinSdkVersion by extra(24)

allprojects {
    repositories {
        google()
        mavenCentral()
    }

    configurations.all {
        resolutionStrategy {
            force("org.jetbrains.kotlin:kotlin-test:2.1.0")
        }
    }
    
    // Make flutter SDK versions available as ext properties for legacy plugins
    afterEvaluate {
        extra["flutter.compileSdkVersion"] = flutterCompileSdkVersion
        extra["flutter.targetSdkVersion"] = flutterTargetSdkVersion
        extra["flutter.minSdkVersion"] = flutterMinSdkVersion
    }
}

subprojects {
    if (project.path != ":app") {
        project.evaluationDependsOn(":app")
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
