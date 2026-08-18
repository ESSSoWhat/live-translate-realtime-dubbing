package app.livetranslate.live_translate_mobile

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import io.flutter.plugin.common.EventChannel
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread

/**
 * MediaProjection FGS: either app/system playback (WAV) or screen frames (JPEG)
 * for Live Translate.
 */
class PlaybackCaptureService : Service() {
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var audioRecord: AudioRecord? = null
    private var captureThread: Thread? = null
    @Volatile private var capturing = false
    private var captureMode: String = MODE_AUDIO

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopCapture(removeForeground = true)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_START -> {
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
                val data = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(EXTRA_RESULT_DATA)
                }
                val mode = intent.getStringExtra(EXTRA_MODE) ?: MODE_AUDIO
                if (data == null || resultCode != Activity.RESULT_OK) {
                    emitError("Screen/audio capture permission was denied")
                    stopSelf()
                    return START_NOT_STICKY
                }
                captureMode = if (mode == MODE_SCREEN) MODE_SCREEN else MODE_AUDIO
                createChannel()
                // Must be foreground with mediaProjection type BEFORE getMediaProjection (API 34+).
                try {
                    ServiceCompat.startForeground(
                        this,
                        NOTIFICATION_ID,
                        buildNotification(captureMode),
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
                    )
                } catch (e: Exception) {
                    Log.e(TAG, "startForeground failed", e)
                    emitError("Could not start capture service: ${e.message}")
                    stopSelf()
                    return START_NOT_STICKY
                }
                try {
                    startCapture(resultCode, data, captureMode)
                } catch (e: Exception) {
                    Log.e(TAG, "startCapture failed", e)
                    emitError("Could not start capture: ${e.message}")
                    stopCapture(removeForeground = true)
                    stopSelf()
                    return START_NOT_STICKY
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopCapture(removeForeground = true)
        super.onDestroy()
    }

    private fun startCapture(resultCode: Int, data: Intent, mode: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            emitError("Live capture requires Android 10+")
            stopSelf()
            return
        }
        releaseCaptureResources()

        val mgr = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val projection = mgr.getMediaProjection(resultCode, data)
            ?: throw IllegalStateException("Could not start MediaProjection")

        mediaProjection = projection
        projection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                Handler(Looper.getMainLooper()).post {
                    stopCapture(removeForeground = true)
                    stopSelf()
                }
            }
        }, Handler(Looper.getMainLooper()))

        if (mode == MODE_SCREEN) {
            startScreenCapture(projection)
        } else {
            startAudioCapture(projection)
        }
    }

    private fun startAudioCapture(projection: MediaProjection) {
        val reader = ImageReader.newInstance(2, 2, PixelFormat.RGBA_8888, 2)
        imageReader = reader
        virtualDisplay = projection.createVirtualDisplay(
            "lt-audio-capture",
            2,
            2,
            resources.displayMetrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY,
            reader.surface,
            null,
            Handler(Looper.getMainLooper()),
        )

        val config = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()

        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()

        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = (minBuf * 2).coerceAtLeast(SAMPLE_RATE * 2)

        val record = AudioRecord.Builder()
            .setAudioFormat(format)
            .setBufferSizeInBytes(bufferSize)
            .setAudioPlaybackCaptureConfig(config)
            .build()

        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IllegalStateException("AudioRecord failed to initialize for playback capture")
        }

        audioRecord = record
        capturing = true
        record.startRecording()
        captureThread = thread(name = "PlaybackCapture", isDaemon = true) {
            // ~20 ms frames for energy VAD; flush on short silence or max length.
            val frameBytes = (SAMPLE_RATE / 50) * BYTES_PER_SAMPLE
            val readBuf = ByteArray(frameBytes.coerceAtMost(bufferSize))
            val chunk = ByteArrayOutputStream(SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MAX_SECONDS + 64)
            val silenceFramesNeeded = SILENCE_FLUSH_MS / 20
            val minSpeechBytes = (SAMPLE_RATE * BYTES_PER_SAMPLE * MIN_SPEECH_MS) / 1000
            val maxChunkBytes = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MAX_SECONDS
            while (capturing) {
                chunk.reset()
                var collected = 0
                var heardSpeech = false
                var silenceFrames = 0
                while (capturing && collected < maxChunkBytes) {
                    val n = record.read(readBuf, 0, minOf(readBuf.size, frameBytes))
                    when {
                        n > 0 -> {
                            chunk.write(readBuf, 0, n)
                            collected += n
                            val frame = if (n == readBuf.size) readBuf else readBuf.copyOf(n)
                            if (isSilent(frame)) {
                                if (heardSpeech) {
                                    silenceFrames++
                                    if (silenceFrames >= silenceFramesNeeded &&
                                        collected >= minSpeechBytes
                                    ) {
                                        break
                                    }
                                }
                            } else {
                                heardSpeech = true
                                silenceFrames = 0
                            }
                        }
                        n < 0 -> {
                            emitError("AudioRecord read error: $n")
                            capturing = false
                            break
                        }
                    }
                }
                if (!capturing) break
                if (!heardSpeech) continue
                val pcm = chunk.toByteArray()
                if (pcm.isEmpty() || isSilent(pcm)) continue
                emitWav(pcmToWav(pcm, SAMPLE_RATE))
            }
        }
    }

    private fun startScreenCapture(projection: MediaProjection) {
        val metrics = resources.displayMetrics
        val fullW = metrics.widthPixels.coerceAtLeast(1)
        val fullH = metrics.heightPixels.coerceAtLeast(1)
        val longest = maxOf(fullW, fullH)
        val scale = if (longest > SCREEN_MAX_EDGE) {
            SCREEN_MAX_EDGE.toFloat() / longest
        } else {
            1f
        }
        val width = (fullW * scale).toInt().coerceAtLeast(2)
        val height = (fullH * scale).toInt().coerceAtLeast(2)
        val density = metrics.densityDpi

        val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        imageReader = reader
        virtualDisplay = projection.createVirtualDisplay(
            "lt-screen-ocr",
            width,
            height,
            density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.surface,
            null,
            Handler(Looper.getMainLooper()),
        )

        capturing = true
        captureThread = thread(name = "ScreenCapture", isDaemon = true) {
            while (capturing) {
                try {
                    val image = reader.acquireLatestImage()
                    if (image != null) {
                        try {
                            val jpeg = imageToJpeg(image)
                            if (jpeg != null && jpeg.isNotEmpty()) {
                                emitFrame(jpeg)
                            }
                        } finally {
                            image.close()
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "frame capture failed", e)
                }
                try {
                    Thread.sleep(FRAME_INTERVAL_MS)
                } catch (_: InterruptedException) {
                    break
                }
            }
        }
    }

    private fun imageToJpeg(image: Image): ByteArray? {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        val width = image.width
        val height = image.height
        val rowPadding = rowStride - pixelStride * width

        val bitmap = if (rowPadding == 0 && pixelStride == 4) {
            val bmp = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            buffer.rewind()
            bmp.copyPixelsFromBuffer(buffer)
            bmp
        } else {
            val bmp = Bitmap.createBitmap(
                width + rowPadding / pixelStride,
                height,
                Bitmap.Config.ARGB_8888,
            )
            buffer.rewind()
            bmp.copyPixelsFromBuffer(buffer)
            Bitmap.createBitmap(bmp, 0, 0, width, height).also {
                if (it !== bmp) bmp.recycle()
            }
        }

        return try {
            val out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
            out.toByteArray()
        } finally {
            bitmap.recycle()
        }
    }

    private fun stopCapture(removeForeground: Boolean) {
        releaseCaptureResources()
        if (removeForeground) {
            try {
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            } catch (_: Exception) {
            }
        }
    }

    private fun releaseCaptureResources() {
        capturing = false
        try {
            captureThread?.join(1500)
        } catch (_: InterruptedException) {
        }
        captureThread = null
        try {
            audioRecord?.stop()
        } catch (_: Exception) {
        }
        try {
            audioRecord?.release()
        } catch (_: Exception) {
        }
        audioRecord = null
        try {
            virtualDisplay?.release()
        } catch (_: Exception) {
        }
        virtualDisplay = null
        try {
            imageReader?.close()
        } catch (_: Exception) {
        }
        imageReader = null
        try {
            mediaProjection?.stop()
        } catch (_: Exception) {
        }
        mediaProjection = null
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Live Translate capture",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Capturing app audio or screen for translation"
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(mode: String): Notification {
        val launch = packageManager.getLaunchIntentForPackage(packageName)
        val pending = PendingIntent.getActivity(
            this,
            0,
            launch,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val text = if (mode == MODE_SCREEN) {
            "Reading on-screen text for translation"
        } else {
            "Capturing app audio for translation"
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Live Translate")
            .setContentText(text)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentIntent(pending)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    companion object {
        private const val TAG = "PlaybackCapture"
        const val ACTION_START = "app.livetranslate.START_PLAYBACK_CAPTURE"
        const val ACTION_STOP = "app.livetranslate.STOP_PLAYBACK_CAPTURE"
        const val EXTRA_RESULT_CODE = "resultCode"
        const val EXTRA_RESULT_DATA = "resultData"
        const val EXTRA_MODE = "captureMode"
        const val MODE_AUDIO = "audio"
        const val MODE_SCREEN = "screen"
        const val CHANNEL_ID = "playback_capture_fgs"
        const val NOTIFICATION_ID = 4202
        const val SAMPLE_RATE = 16000
        const val BYTES_PER_SAMPLE = 2
        /** Hard cap so phrases are not held forever without a pause. */
        const val CHUNK_MAX_SECONDS = 2
        /** Flush after this much trailing silence once speech was heard (~desktop VAD). */
        const val SILENCE_FLUSH_MS = 350
        /** Do not flush on silence until at least this much audio was collected. */
        const val MIN_SPEECH_MS = 450
        const val SCREEN_MAX_EDGE = 720
        const val FRAME_INTERVAL_MS = 900L
        const val JPEG_QUALITY = 70

        @Volatile
        var audioEventSink: EventChannel.EventSink? = null

        @Volatile
        var frameEventSink: EventChannel.EventSink? = null

        /** @deprecated use [audioEventSink] */
        @Deprecated("Use audioEventSink", ReplaceWith("audioEventSink"))
        var eventSink: EventChannel.EventSink?
            get() = audioEventSink
            set(value) {
                audioEventSink = value
            }

        private fun emitWav(wav: ByteArray) {
            Handler(Looper.getMainLooper()).post {
                try {
                    audioEventSink?.success(wav)
                } catch (_: Exception) {
                }
            }
        }

        private fun emitFrame(jpeg: ByteArray) {
            Handler(Looper.getMainLooper()).post {
                try {
                    frameEventSink?.success(jpeg)
                } catch (_: Exception) {
                }
            }
        }

        private fun emitError(message: String) {
            Handler(Looper.getMainLooper()).post {
                try {
                    audioEventSink?.error("playback_capture", message, null)
                } catch (_: Exception) {
                }
                try {
                    frameEventSink?.error("playback_capture", message, null)
                } catch (_: Exception) {
                }
            }
        }

        private fun isSilent(pcm: ByteArray): Boolean {
            var sum = 0L
            var count = 0
            var i = 0
            while (i + 1 < pcm.size) {
                val sample = (pcm[i].toInt() and 0xff) or (pcm[i + 1].toInt() shl 8)
                sum += kotlin.math.abs(sample.toShort().toInt())
                count++
                i += 2
            }
            if (count == 0) return true
            return (sum / count) < 200
        }

        private fun pcmToWav(pcm: ByteArray, sampleRate: Int): ByteArray {
            val channels = 1
            val byteRate = sampleRate * channels * 2
            val out = ByteArrayOutputStream(44 + pcm.size)
            out.write("RIFF".toByteArray())
            out.write(intLE(36 + pcm.size))
            out.write("WAVE".toByteArray())
            out.write("fmt ".toByteArray())
            out.write(intLE(16))
            out.write(shortLE(1))
            out.write(shortLE(channels))
            out.write(intLE(sampleRate))
            out.write(intLE(byteRate))
            out.write(shortLE(channels * 2))
            out.write(shortLE(16))
            out.write("data".toByteArray())
            out.write(intLE(pcm.size))
            out.write(pcm)
            return out.toByteArray()
        }

        private fun intLE(v: Int): ByteArray =
            ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()

        private fun shortLE(v: Int): ByteArray =
            ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort(v.toShort()).array()
    }
}
