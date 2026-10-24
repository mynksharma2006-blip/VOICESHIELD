package com.voiceshield.callscreening

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.format.DateFormat
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONException
import org.json.JSONObject
import java.util.Date

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var callHistory: LinearLayout
    private lateinit var clearHistoryButton: Button
    private lateinit var serverUrlInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var liveCheckStatus: TextView
    private lateinit var liveCheckButton: Button
    private var speakerphoneMonitor: SpeakerphoneMonitor? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        status = TextView(this).apply {
            textSize = 16f
        }
        val setupButton = Button(this).apply {
            text = "Enable call warnings"
            setOnClickListener { requestSetupPermissions() }
        }
        clearHistoryButton = Button(this).apply {
            text = "Clear call history"
            setOnClickListener { confirmClearCallHistory() }
        }
        val preferences = getSharedPreferences(PREFERENCES_NAME, MODE_PRIVATE)
        serverUrlInput = EditText(this).apply {
            hint = "https://your-server.example"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine(true)
            setText(preferences.getString(SERVER_URL_KEY, ""))
            contentDescription = "Secure VoiceShield server URL"
        }
        tokenInput = EditText(this).apply {
            hint = "Call-stream access token"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
            contentDescription = "Call-stream access token; kept only in memory"
        }
        liveCheckStatus = TextView(this).apply {
            text = "Not connected. This optional experiment sends microphone audio to your configured server only after you start it."
            textSize = 15f
            setPadding(0, 12, 0, 12)
        }
        liveCheckButton = Button(this).apply {
            text = "Start speakerphone check"
            setOnClickListener {
                if (speakerphoneMonitor != null) stopSpeakerphoneCheck("Live check stopped.")
                else requestSpeakerphoneCheck()
            }
        }
        callHistory = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 32)
            addView(status)
            addView(setupButton)
            addView(TextView(this@MainActivity).apply {
                text = "Call warnings only check whether a number is saved in your contacts. They do not listen to calls or decide whether a caller is genuine."
                textSize = 15f
                setPadding(0, 16, 0, 16)
            })
            addView(TextView(this@MainActivity).apply {
                text = "Optional speakerphone live experiment"
                textSize = 20f
                setPadding(0, 28, 0, 8)
            })
            addView(TextView(this@MainActivity).apply {
                text = "Not direct cellular-call audio. During a call, turn on speakerphone, keep this app open in the foreground, then manually start the check. Android or your phone may block or limit microphone capture. The app never starts listening automatically."
                textSize = 15f
                setPadding(0, 0, 0, 12)
            })
            addView(serverUrlInput)
            addView(tokenInput)
            addView(liveCheckButton)
            addView(liveCheckStatus)
            addView(TextView(this@MainActivity).apply {
                text = "Audio is streamed temporarily to the server you configure over WSS and is not saved by this app. The raw model score is not a probability. Until a model is specifically validated for speakerphone call conditions, the result stays Can’t tell; it cannot prove a voice is genuine or fake."
                textSize = 14f
                setPadding(0, 4, 0, 8)
            })
            addView(TextView(this@MainActivity).apply {
                text = "Unknown call history (saved on this device)"
                textSize = 18f
                setPadding(0, 32, 0, 12)
            })
            addView(clearHistoryButton)
            addView(callHistory)
        }
        setContentView(ScrollView(this).apply { addView(layout) })
        updateStatus()
        updateCallHistory()
    }

    override fun onResume() {
        super.onResume()
        if (::status.isInitialized) {
            updateStatus()
            updateCallHistory()
        }
    }

    override fun onStop() {
        stopSpeakerphoneCheck("Live check stopped because VoiceShield left the foreground.")
        super.onStop()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSIONS_REQUEST) {
            if (hasRequiredPermissions()) {
                requestScreeningRole()
            } else {
                status.text = "Contacts and notifications permissions are required for call warnings."
            }
        } else if (requestCode == MICROPHONE_REQUEST) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
                showSpeakerphoneConsent()
            } else {
                liveCheckStatus.text = "Microphone permission was not granted. No audio was captured or sent."
            }
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == SCREENING_ROLE_REQUEST) updateStatus()
    }

    private fun requestSetupPermissions() {
        val missing = buildList {
            if (checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
                add(Manifest.permission.READ_CONTACTS)
            }
            if (
                Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), PERMISSIONS_REQUEST)
        } else {
            requestScreeningRole()
        }
    }

    private fun hasRequiredPermissions(): Boolean =
        checkSelfPermission(Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED &&
            (
                Build.VERSION.SDK_INT < 33 ||
                    checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
                )

    private fun requestScreeningRole() {
        val roleManager = getSystemService(RoleManager::class.java)
        if (!roleManager.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING)) {
            status.text = "This device does not support Android call screening."
            return
        }
        if (roleManager.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)) {
            updateStatus()
            return
        }
        startActivityForResult(
            roleManager.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING),
            SCREENING_ROLE_REQUEST,
        )
    }

    private fun updateStatus() {
        val roleManager = getSystemService(RoleManager::class.java)
        status.text = if (roleManager.isRoleHeld(RoleManager.ROLE_CALL_SCREENING) && hasRequiredPermissions()) {
            "Call warnings are enabled. Numbers not saved in your contacts may be unfamiliar; that does not mean they are fraudulent. Calls and contact checks stay on this device."
        } else {
            "Setup is required. Contact warnings only check whether a number is saved on your phone; they cannot determine whether a caller is genuine or access direct cellular-call audio. The optional speakerphone experiment below is separate."
        }
    }

    private fun updateCallHistory() {
        callHistory.removeAllViews()
        val events = try {
            CallEventStore.read(this)
        } catch (exception: JSONException) {
            status.text = "Could not read local call history: ${exception.localizedMessage}"
            return
        } catch (exception: RuntimeException) {
            status.text = "Could not read local call history: ${exception.localizedMessage}"
            return
        }
        if (events.isEmpty()) {
            clearHistoryButton.isEnabled = false
            callHistory.addView(TextView(this).apply {
                text = "No unknown calls recorded yet."
                textSize = 15f
            })
            return
        }
        clearHistoryButton.isEnabled = true
        events.forEach { event ->
            callHistory.addView(TextView(this).apply {
                val timestamp = DateFormat.getMediumDateFormat(this@MainActivity)
                    .format(Date(event.timestampMillis)) +
                    " " + DateFormat.getTimeFormat(this@MainActivity)
                    .format(Date(event.timestampMillis))
                text = "${event.phoneNumber}\n$timestamp · Not saved in contacts"
                textSize = 15f
                setPadding(0, 12, 0, 12)
            })
        }
    }

    private fun confirmClearCallHistory() {
        AlertDialog.Builder(this)
            .setTitle("Clear call history?")
            .setMessage("This removes the unknown-number history stored on this device. It does not change your contacts or block any numbers.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Clear") { _, _ ->
                try {
                    CallEventStore.clear(this)
                    updateCallHistory()
                } catch (exception: RuntimeException) {
                    status.text = "Could not clear local call history: ${exception.localizedMessage}"
                }
            }
            .show()
    }

    private fun requestSpeakerphoneCheck() {
        val url = serverUrlInput.text.toString().trim()
        val uri = Uri.parse(url)
        if (
            uri.scheme != "https" || uri.host.isNullOrBlank() ||
            uri.userInfo != null || uri.query != null || uri.fragment != null ||
            (uri.path?.isNotBlank() == true && uri.path != "/")
        ) {
            liveCheckStatus.text = "Enter a secure HTTPS server base URL, such as https://your-server.example. Insecure or custom-path URLs are not accepted."
            return
        }
        if (tokenInput.text.isNullOrBlank()) {
            liveCheckStatus.text = "Enter the access token configured on your backend. The token is not saved by this app."
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MICROPHONE_REQUEST)
            return
        }
        showSpeakerphoneConsent()
    }

    private fun showSpeakerphoneConsent() {
        AlertDialog.Builder(this)
            .setTitle("Start speakerphone audio check?")
            .setMessage(
                "VoiceShield will use the phone microphone and stream audio over an encrypted WSS connection to the server URL you entered. " +
                    "This is room audio from your microphone, not direct cellular-call audio. Do not start unless everyone nearby knows you are sharing audio. " +
                    "Audio is not saved by this app. Keep VoiceShield open, use speakerphone, and stop the check when finished. Continue?"
            )
            .setNegativeButton("Cancel") { _, _ ->
                liveCheckStatus.text = "Cancelled. No audio was captured or sent."
            }
            .setPositiveButton("Continue") { _, _ -> connectSpeakerphoneStream() }
            .show()
    }

    private fun connectSpeakerphoneStream() {
        val httpsUrl = Uri.parse(serverUrlInput.text.toString().trim())
        val websocketUrl = Uri.Builder()
            .scheme("wss")
            .encodedAuthority(httpsUrl.encodedAuthority)
            .path("/api/call-stream")
            .appendQueryParameter("sample_rate", SpeakerphoneMonitor.SAMPLE_RATE.toString())
            .build()
            .toString()
        val token = tokenInput.text.toString()
        tokenInput.text.clear()
        getSharedPreferences(PREFERENCES_NAME, MODE_PRIVATE).edit()
            .putString(SERVER_URL_KEY, httpsUrl.toString().trimEnd('/'))
            .apply()
        liveCheckButton.isEnabled = false
        liveCheckStatus.text = "Connecting securely. No microphone audio is sent until the server is ready."

        val monitor = SpeakerphoneMonitor(websocketUrl, token, object : SpeakerphoneMonitor.Listener {
            override fun onConnected() {
                runOnUiThread {
                    liveCheckStatus.text = "Secure connection established. Waiting for the configured model..."
                }
            }

            override fun onMessage(message: String) {
                runOnUiThread { handleSpeakerphoneMessage(message) }
            }

            override fun onError(message: String) {
                runOnUiThread {
                    if (speakerphoneMonitor != null) {
                        stopSpeakerphoneCheck("Live check stopped: $message")
                    }
                }
            }

            override fun onStopped() {
                runOnUiThread {
                    if (speakerphoneMonitor != null) {
                        speakerphoneMonitor = null
                        liveCheckButton.text = "Start speakerphone check"
                        liveCheckButton.isEnabled = true
                        liveCheckStatus.text = "Secure stream closed. Audio capture has stopped."
                    }
                }
            }
        })
        speakerphoneMonitor = monitor
        liveCheckButton.text = "Stop live check"
        liveCheckButton.isEnabled = true
        monitor.start()
    }

    private fun handleSpeakerphoneMessage(message: String) {
        val response = try {
            JSONObject(message)
        } catch (exception: org.json.JSONException) {
            stopSpeakerphoneCheck("The server returned an unreadable response.")
            return
        }
        if (response.has("error")) {
            stopSpeakerphoneCheck(response.optString("error", "The server reported an error."))
            return
        }
        when (response.optString("status")) {
            "ready" -> {
                speakerphoneMonitor?.startCapture()
                liveCheckButton.text = "Stop live check"
                liveCheckButton.isEnabled = true
                liveCheckStatus.text =
                    "Listening to microphone room audio. ${response.optDouble("window_duration_s", 0.0)} seconds are needed before the first score. This is not direct call audio."
            }
            "warming_up" -> {
                liveCheckStatus.text =
                    "Warming up: ${response.optDouble("elapsed_s", 0.0)} of ${response.optDouble("required_s", 0.0)} seconds. No result yet."
            }
            "scored" -> {
                val label = response.optString("label", "inconclusive")
                val rawScore = response.opt("score_raw")?.toString() ?: "unavailable"
                liveCheckStatus.text = if (label == "inconclusive") {
                    "Can’t tell — this speakerphone signal is not validated to label a live caller. Raw model score: $rawScore (not a probability). ${response.optString("reason")}"
                } else {
                    "Model signal: $label. Raw score: $rawScore (not a probability). This remains an experimental speakerphone result, not proof of caller identity. ${response.optString("reason")}"
                }
            }
        }
    }

    private fun stopSpeakerphoneCheck(message: String) {
        val monitor = speakerphoneMonitor
        speakerphoneMonitor = null
        monitor?.stop()
        if (::liveCheckStatus.isInitialized) liveCheckStatus.text = message
        if (::liveCheckButton.isInitialized) {
            liveCheckButton.text = "Start speakerphone check"
            liveCheckButton.isEnabled = true
        }
    }

    private companion object {
        const val PERMISSIONS_REQUEST = 1
        const val SCREENING_ROLE_REQUEST = 2
        const val MICROPHONE_REQUEST = 3
        const val PREFERENCES_NAME = "voiceshield_settings"
        const val SERVER_URL_KEY = "speakerphone_server_url"
    }
}
