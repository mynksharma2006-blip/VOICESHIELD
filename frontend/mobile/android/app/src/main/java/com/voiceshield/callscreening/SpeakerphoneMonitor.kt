package com.voiceshield.callscreening

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class SpeakerphoneMonitor(
    private val websocketUrl: String,
    token: String,
    private val listener: Listener,
) {
    interface Listener {
        fun onConnected()
        fun onMessage(message: String)
        fun onError(message: String)
        fun onStopped()
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val running = AtomicBoolean(false)
    private val authorization = "Bearer $token"
    @Volatile private var webSocket: WebSocket? = null
    @Volatile private var recorder: AudioRecord? = null
    @Volatile private var captureThread: Thread? = null

    fun start() {
        val request = Request.Builder()
            .url(websocketUrl)
            .header("Authorization", authorization)
            .build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                listener.onConnected()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                listener.onMessage(text)
            }

            override fun onFailure(webSocket: WebSocket, error: Throwable, response: Response?) {
                stopCapture()
                listener.onError(response?.message ?: error.localizedMessage ?: "Secure streaming connection failed.")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                stopCapture()
                listener.onStopped()
            }
        })
    }

    fun startCapture() {
        if (!running.compareAndSet(false, true)) return
        val minimumBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minimumBuffer <= 0) {
            running.set(false)
            listener.onError("This device cannot capture microphone audio at 16 kHz.")
            return
        }

        try {
            val audioRecord = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minimumBuffer, PACKET_BYTES * 2),
            )
            if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
                audioRecord.release()
                running.set(false)
                listener.onError("The microphone could not be started. Your phone may reserve it for the call.")
                return
            }
            recorder = audioRecord
            audioRecord.startRecording()
            captureThread = Thread({ captureLoop(audioRecord) }, "VoiceShieldSpeakerphoneCapture").apply {
                isDaemon = true
                start()
            }
        } catch (error: SecurityException) {
            running.set(false)
            recorder?.let { releaseRecorder(it) }
            listener.onError("Microphone permission is required to start this check.")
        } catch (error: IllegalStateException) {
            running.set(false)
            recorder?.let { releaseRecorder(it) }
            listener.onError(error.localizedMessage ?: "The microphone could not be started.")
        }
    }

    fun stop() {
        stopCapture()
        webSocket?.close(1000, "Speakerphone check stopped")
        webSocket = null
        client.dispatcher.cancelAll()
        client.connectionPool.evictAll()
    }

    private fun captureLoop(audioRecord: AudioRecord) {
        val packet = ShortArray(PACKET_SAMPLES)
        try {
            while (running.get()) {
                var offset = 0
                while (offset < packet.size && running.get()) {
                    val count = audioRecord.read(packet, offset, packet.size - offset)
                    if (count < 0) {
                        listener.onError("Microphone capture stopped unexpectedly (code $count).")
                        running.set(false)
                        break
                    }
                    if (count == 0) {
                        Thread.yield()
                        continue
                    }
                    offset += count
                }
                if (offset != packet.size || !running.get()) continue

                val bytes = ByteArray(PACKET_BYTES)
                for (index in packet.indices) {
                    val value = packet[index].toInt()
                    bytes[index * 2] = value.toByte()
                    bytes[index * 2 + 1] = (value shr 8).toByte()
                }
                val sent = webSocket?.send(ByteString.of(*bytes)) ?: false
                if (!sent) {
                    listener.onError("The secure audio connection is no longer available.")
                    running.set(false)
                }
            }
        } finally {
            releaseRecorder(audioRecord)
        }
    }

    private fun stopCapture() {
        running.set(false)
        val audioRecord = recorder
        if (audioRecord != null) {
            try {
                if (audioRecord.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    audioRecord.stop()
                }
            } catch (_: IllegalStateException) {
                listener.onError("The microphone stopped unexpectedly.")
            }
        }
        captureThread?.interrupt()
        captureThread = null
    }

    private fun releaseRecorder(audioRecord: AudioRecord) {
        if (recorder === audioRecord) {
            recorder = null
            try {
                audioRecord.release()
            } catch (error: IllegalStateException) {
                listener.onError(error.localizedMessage ?: "Could not release microphone resources.")
            }
        }
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        private const val PACKET_SAMPLES = SAMPLE_RATE / 2
        private const val PACKET_BYTES = PACKET_SAMPLES * 2
    }
}
