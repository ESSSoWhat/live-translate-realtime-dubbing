// Gradle 8.x does not support running on Java 25. Use JDK 17 for this project (see android/README.md).
val javaMajor = JavaVersion.current().majorVersion.toIntOrNull() ?: 0
if (javaMajor >= 25) {
    throw GradleException(
        "Android build requires JDK 17 or 21. Current: Java $javaMajor. " +
        "Set JAVA_HOME to JDK 17 (or in IDE: Gradle JDK → 17). See android/README.md."
    )
}

// Set SDK versions for plugins that need them (e.g. record_android)
rootProject.extra.set("compileSdkVersion", 36)
rootProject.extra.set("targetSdkVersion", 35)
rootProject.extra.set("minSdkVersion", 24)

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
    if (project.path != ":app") {
        project.evaluationDependsOn(":app")
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}

