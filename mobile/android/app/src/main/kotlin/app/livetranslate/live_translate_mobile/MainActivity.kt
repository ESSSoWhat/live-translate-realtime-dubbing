package app.livetranslate.live_translate_mobile

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val micFgsChannel = "app.livetranslate.live_translate_mobile/mic_fgs"
    private val playbackMethodChannel =
        "app.livetranslate.live_translate_mobile/playback_capture"
    private val playbackEventChannel =
        "app.livetranslate.live_translate_mobile/playback_capture/audio"
    private val framesEventChannel =
        "app.livetranslate.live_translate_mobile/playback_capture/frames"

    private var pendingConsentResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, micFgsChannel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "start" -> {
                        ensureNotificationPermission()
                        val intent = Intent(this, MicTranslateForegroundService::class.java)
                        ContextCompat.startForegroundService(this, intent)
                        result.success(true)
                    }
                    "stop" -> {
                        stopService(Intent(this, MicTranslateForegroundService::class.java))
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, playbackMethodChannel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "isSupported" -> {
                        result.success(Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                    }
                    "requestConsent" -> {
                        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                            result.error(
                                "unsupported",
                                "App audio capture requires Android 10+",
                                null,
                            )
                            return@setMethodCallHandler
                        }
                        if (pendingConsentResult != null) {
                            result.error("busy", "Capture consent already in progress", null)
                            return@setMethodCallHandler
                        }
                        ensureNotificationPermission()
                        pendingConsentResult = result
                        val mgr =
                            getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                        startActivityForResult(
                            mgr.createScreenCaptureIntent(),
                            REQUEST_MEDIA_PROJECTION,
                        )
                    }
                    "start" -> {
                        val data = lastProjectionData
                        val code = lastProjectionResultCode
                        if (data == null || code != Activity.RESULT_OK) {
                            result.error(
                                "no_consent",
                                "Request screen/audio capture permission first",
                                null,
                            )
                            return@setMethodCallHandler
                        }
                        val modeArg = call.argument<String>("mode")
                        val mode = if (modeArg == PlaybackCaptureService.MODE_SCREEN) {
                            PlaybackCaptureService.MODE_SCREEN
                        } else {
                            PlaybackCaptureService.MODE_AUDIO
                        }
                        ensureNotificationPermission()
                        val intent = Intent(this, PlaybackCaptureService::class.java).apply {
                            action = PlaybackCaptureService.ACTION_START
                            putExtra(PlaybackCaptureService.EXTRA_RESULT_CODE, code)
                            putExtra(PlaybackCaptureService.EXTRA_RESULT_DATA, data)
                            putExtra(PlaybackCaptureService.EXTRA_MODE, mode)
                        }
                        ContextCompat.startForegroundService(this, intent)
                        // Consent Intent is single-use after getMediaProjection.
                        lastProjectionData = null
                        lastProjectionResultCode = Activity.RESULT_CANCELED
                        result.success(true)
                    }
                    "stop" -> {
                        val intent = Intent(this, PlaybackCaptureService::class.java).apply {
                            action = PlaybackCaptureService.ACTION_STOP
                        }
                        startService(intent)
                        stopService(Intent(this, PlaybackCaptureService::class.java))
                        lastProjectionData = null
                        lastProjectionResultCode = Activity.RESULT_CANCELED
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }

        EventChannel(flutterEngine.dartExecutor.binaryMessenger, playbackEventChannel)
            .setStreamHandler(object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                    PlaybackCaptureService.audioEventSink = events
                }

                override fun onCancel(arguments: Any?) {
                    PlaybackCaptureService.audioEventSink = null
                }
            })

        EventChannel(flutterEngine.dartExecutor.binaryMessenger, framesEventChannel)
            .setStreamHandler(object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                    PlaybackCaptureService.frameEventSink = events
                }

                override fun onCancel(arguments: Any?) {
                    PlaybackCaptureService.frameEventSink = null
                }
            })
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQUEST_MEDIA_PROJECTION) return
        val pending = pendingConsentResult
        pendingConsentResult = null
        if (resultCode == Activity.RESULT_OK && data != null) {
            lastProjectionResultCode = resultCode
            lastProjectionData = data
            pending?.success(true)
        } else {
            lastProjectionResultCode = Activity.RESULT_CANCELED
            lastProjectionData = null
            pending?.error("denied", "User denied screen/audio capture", null)
        }
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                REQUEST_POST_NOTIFICATIONS,
            )
        }
    }

    companion object {
        private const val REQUEST_POST_NOTIFICATIONS = 1001
        private const val REQUEST_MEDIA_PROJECTION = 1002

        @Volatile
        private var lastProjectionResultCode: Int = Activity.RESULT_CANCELED

        @Volatile
        private var lastProjectionData: Intent? = null
    }
}
