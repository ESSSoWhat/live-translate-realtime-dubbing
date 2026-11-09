// Gradle 8.x does not support running on Java 25. Use JDK 17 for this project (see android/README.md).
val javaMajor = JavaVersion.current().majorVersion.toIntOrNull() ?: 0
if (javaMajor >= 25) {
    logger.warn("WARNING: Android build requires JDK 17 or 21. Current: Java $javaMajor. " +
        "Set JAVA_HOME to JDK 17 (or in IDE: Gradle JDK → 17). See android/README.md.")
}

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

// flutter_overlay_window 0.5.0 crashes on targetSdk 34+ when showing the bubble:
// startForeground() without a type, and startService instead of startForegroundService.
subprojects {
    if (name.contains("flutter_overlay_window")) {
        tasks.configureEach {
            if (name.startsWith("compile") && (name.contains("Java") || name.contains("Kotlin"))) {
                doFirst {
                    val overlayFile =
                        file("src/main/java/flutter/overlay/window/flutter_overlay_window/OverlayService.java")
                    val pluginFile =
                        file("src/main/java/flutter/overlay/window/flutter_overlay_window/FlutterOverlayWindowPlugin.java")
                    if (overlayFile.exists()) {
                        var text = overlayFile.readText()
                        if (!text.contains("FOREGROUND_SERVICE_TYPE_SPECIAL_USE")) {
                            text = text.replace(
                                "startForeground(OverlayConstants.NOTIFICATION_ID, notification);",
                                """
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            startForeground(
                OverlayConstants.NOTIFICATION_ID,
                notification,
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            );
        } else {
            startForeground(OverlayConstants.NOTIFICATION_ID, notification);
        }
                                """.trimIndent(),
                            )
                            text = text.replace(
                                """FlutterEngine engine = FlutterEngineCache.getInstance().get(OverlayConstants.CACHED_TAG);
        engine.getLifecycleChannel().appIsResumed();""",
                                """FlutterEngine engine = FlutterEngineCache.getInstance().get(OverlayConstants.CACHED_TAG);
        if (engine == null) {
            Log.e("OverlayService", "Flutter engine missing; aborting overlay start");
            stopSelf();
            return START_NOT_STICKY;
        }
        engine.getLifecycleChannel().appIsResumed();""",
                            )
                            overlayFile.writeText(text)
                            logger.lifecycle("Patched flutter_overlay_window OverlayService for Android 14+ FGS")
                        }
                        // showOverlay passes dp, but initial LayoutParams used raw px (tiny bubble).
                        text = overlayFile.readText()
                        if (!text.contains("/* LT_DP_SIZE */")) {
                            text = text.replace(
                                """WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                WindowSetup.width == -1999 ? -1 : WindowSetup.width,
                WindowSetup.height != -1999 ? WindowSetup.height : screenHeight(),""",
                                """WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                /* LT_DP_SIZE */ WindowSetup.width == -1999 ? -1 : (WindowSetup.width < 0 ? WindowSetup.width : dpToPx(WindowSetup.width)),
                WindowSetup.height == -1999 ? screenHeight() : (WindowSetup.height < 0 ? WindowSetup.height : dpToPx(WindowSetup.height)),""",
                            )
                            overlayFile.writeText(text)
                            logger.lifecycle("Patched flutter_overlay_window initial size to use dp")
                        }
                        text = overlayFile.readText()
                        if (!text.contains("LT_NO_STOPSELF")) {
                            text = text.replace(
                                """
        if (windowManager != null) {
            windowManager.removeView(flutterView);
            windowManager = null;
            flutterView.detachFromFlutterEngine();
            stopSelf();
        }
        isRunning = true;
                                """.trimIndent(),
                                """
        // LT_NO_STOPSELF: recreating the overlay must not stopSelf() — that races
        // onDestroy with the new FlutterView and crashes / blanks the bubble.
        if (windowManager != null) {
            try {
                windowManager.removeView(flutterView);
            } catch (Exception ignored) {}
            windowManager = null;
            try {
                flutterView.detachFromFlutterEngine();
            } catch (Exception ignored) {}
        }
        isRunning = true;
                                """.trimIndent(),
                            )
                            overlayFile.writeText(text)
                            logger.lifecycle("Patched flutter_overlay_window to avoid stopSelf on recreate")
                        }
                    }
                    if (pluginFile.exists()) {
                        var text = pluginFile.readText()
                        if (!text.contains("startForegroundService(intent)")) {
                            text = text.replace(
                                "context.startService(intent);",
                                """
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent);
            } else {
                context.startService(intent);
            }
                                """.trimIndent(),
                            )
                            pluginFile.writeText(text)
                            logger.lifecycle("Patched flutter_overlay_window to use startForegroundService")
                        }
                    }
                }
            }
        }
    }
}

tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
