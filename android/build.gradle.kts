allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

// Note: Removed global project.evaluationDependsOn(":app") to avoid circular evaluation
// If specific subprojects need to depend on :app, add targeted evaluationDependsOn calls
// for those specific modules instead of applying it globally to all subprojects.
subprojects {
    // Individual subprojects can add evaluationDependsOn(":app") if needed
}

tasks.register("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}

