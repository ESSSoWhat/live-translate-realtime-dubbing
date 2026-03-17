package com.livetranslate.audio_capture_plugin

import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.AudioFormat
import android.media.AudioManager
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AudioCapturePlugin: FlutterPlugin, MethodChannel.MethodCallHandler, EventChannel.StreamHandler {
  private lateinit var methodChannel: MethodChannel
  private lateinit var eventChannel: EventChannel
  private var audioRecord: AudioRecord? = null
  private var isCapturing = false
  private var eventSink: EventChannel.EventSink? = null
  private var captureThread: Thread? = null

  override fun onAttachedToEngine(flutterPluginBinding: FlutterPlugin.FlutterPluginBinding) {
    methodChannel = MethodChannel(flutterPluginBinding.binaryMessenger, "com.livetranslate/audio")
    methodChannel.setMethodCallHandler(this)

    eventChannel = EventChannel(flutterPluginBinding.binaryMessenger, "com.livetranslate/audio_stream")
    eventChannel.setStreamHandler(this)
  }

  override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
    when (call.method) {
      "initializeAudioCapture" -> {
        result.success(initializeAudioCapture())
      }
      "startAudioCapture" -> {
        result.success(startAudioCapture())
      }
      "stopAudioCapture" -> {
        result.success(stopAudioCapture())
      }
      "getAvailableAudioDevices" -> {
        result.success(getAvailableAudioDevices())
      }
      else -> {
        result.notImplemented()
      }
    }
  }

  private fun initializeAudioCapture(): Boolean {
    val sampleRate = 16000
    val channelConfig = AudioFormat.CHANNEL_IN_MONO
    val audioFormat = AudioFormat.ENCODING_PCM_16BIT
    val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)

    if (bufferSize == AudioRecord.ERROR_BAD_VALUE || bufferSize == AudioRecord.ERROR) {
      return false
    }

    return true
  }

  private fun startAudioCapture(): Boolean {
    if (isCapturing) return false

    val sampleRate = 16000
    val channelConfig = AudioFormat.CHANNEL_IN_MONO
    val audioFormat = AudioFormat.ENCODING_PCM_16BIT
    val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat) * 2

    audioRecord = AudioRecord(
      MediaRecorder.AudioSource.MIC,
      sampleRate,
      channelConfig,
      audioFormat,
      bufferSize
    )

    if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
      return false
    }

    audioRecord?.startRecording()
    isCapturing = true

    captureThread = Thread {
      val buffer = ByteArray(bufferSize)
      while (isCapturing && audioRecord != null) {
        val read = audioRecord?.read(buffer, 0, buffer.size) ?: 0
        if (read > 0) {
          val data = buffer.sliceArray(0 until read).toList()
          eventSink?.success(data)
        }
      }
    }
    captureThread?.start()

    return true
  }

  private fun stopAudioCapture(): Boolean {
    if (!isCapturing) return true

    isCapturing = false
    audioRecord?.stop()
    audioRecord?.release()
    audioRecord = null
    captureThread?.join()
    captureThread = null

    return true
  }

  private fun getAvailableAudioDevices(): List<String> {
    return listOf("Default Microphone")
  }

  override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
    eventSink = events
  }

  override fun onCancel(arguments: Any?): EventChannel.EventSink? {
    eventSink = null
    return null
  }

  override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
    methodChannel.setMethodCallHandler(null)
    eventChannel.setStreamHandler(null)
    stopAudioCapture()
  }
}

