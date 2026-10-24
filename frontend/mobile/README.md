# Native call-screening scaffolds

The Android APK is a native call-screening app; the existing VoiceShield website
cannot receive cellular-call events or inspect call audio. Install the APK on an
Android device and grant its system call-screening role to activate the Android
feature.

## Android

Open `frontend/mobile/android` in Android Studio with JDK 17, Gradle 8.9, and Android SDK 35 installed.
Build and install the app, open it, grant Contacts and Notifications permissions,
and approve the system's Call screening role request. The app uses Android's
`CallScreeningService` to compare incoming numbers against the device's contacts.
It allows calls and posts a local warning for numbers not found in contacts.

To build the installable debug APK from Windows:

```powershell
cd frontend\mobile\android
.\gradlew.bat assembleDebug
```

The APK is written to `app\build\outputs\apk\debug\app-debug.apk`. Transfer it to
the Android phone, install it (allowing the file manager to install this APK if
Android prompts), open **VoiceShield Calls**, tap **Enable call warnings**, grant
the requested permissions, and choose VoiceShield as the call screening app
when the Android system prompt appears.

When the local web server is running, its Call detection section also provides
the built APK at `/api/mobile/android.apk`.

The warning means only "not saved in contacts." It is not a spam verdict, caller
identity check, or voice-deepfake detection. Contact lookups and notifications
are processed on-device; phone numbers are not uploaded.

Unknown numbers are also recorded in the app's on-device call history (up to
the 50 most recent screening events). Tap the incoming-call warning
notification to open VoiceShield Calls and view the caller number and time.
The call-screening service records screening metadata only; it does not capture
direct call audio. The separate optional speakerphone experiment below streams
microphone room audio when manually started. The app includes a **Clear call history** action with confirmation;
clearing it removes only the locally stored unknown-call events. Uninstalling
the app also clears this local history.

### Optional speakerphone live experiment

The Android app includes an opt-in microphone experiment, not cellular-call
audio access. It must be started manually while VoiceShield is visible in the
foreground. The user must enable speakerphone during the call; Android or a
device manufacturer may still prevent microphone capture. The experiment does
not start automatically, stop/start a call, block a caller, or save audio.

To configure it:

1. Run the backend behind HTTPS with WebSocket upgrades enabled, so the app can
   connect using WSS. Provide a reachable DNS name and a certificate trusted by
   the Android device. Do not use a public or shared network without suitable
   access controls.
2. Set a long, random `VOICESHIELD_CALL_STREAM_TOKEN` in the backend's private
   environment. Keep it out of source control and logs. Restart the backend.
   The dedicated `/api/call-stream` endpoint rejects connections without this
   token and rejects non-secure WebSockets. The ordinary `/api/stream` browser
   microphone demo remains a separate endpoint.
3. Build and install the updated APK. In **Optional speakerphone live
   experiment**, enter the server's HTTPS base URL and the token. The URL is
   saved on device; the token is kept only in memory and cleared from the input
   after starting a connection.
4. During a call, enable speakerphone, open VoiceShield, tap **Start
   speakerphone check**, grant microphone permission, and confirm the per-session
   audio-sharing notice. Keep the app in the foreground. Tap **Stop live check**
   when finished. Leaving the foreground also stops capture and closes the
   stream.

Only microphone PCM audio is sent, as temporary chunks over WSS, to the server
you configured. It is not written to a local audio file by the app. The server
uses its configured AASIST model in memory; live streaming is unavailable if
that model is not present. Android mic capture can include room noise and
speakerphone playback, not a clean or direct copy of the other caller.
`VOICESHIELD_CALL_AUDIO_LABELS_VALIDATED` defaults to `false`; this setting is
separate from general recording validation. With the default, scores are
explicitly **Can’t tell**. Do not enable call-audio labels until the deployed
model and thresholds have been evaluated on representative, labeled
speakerphone-call recordings. Even then, results are model signals—not proof
of caller identity or fraud. A raw score is not a probability.

For a TLS-terminating reverse proxy, configure the ASGI server/proxy chain so
the application receives the WebSocket scope scheme as `wss`; the backend
deliberately rejects connections whose scope is not secure. Never solve proxy
issues by weakening that check or trusting arbitrary client-supplied forwarded
headers.

#### Run one automated connection test

This test runs the authorized streaming endpoint with a simulated microphone
chunk and a dummy detector. It checks that the secure endpoint accepts the
configured token and returns **Can’t tell** when call-audio validation is off.
It does not use a real phone, model, network, or cellular call.

From the project root in PowerShell:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_call_stream.py -k authorized_call_stream -v
Set-Location ..
```

The passing test should end with `OK`. To try the real phone flow instead,
first complete the HTTPS/WSS setup above, install the APK, enter that server
URL and its matching token in the app, then manually start a speakerphone
check. The app must remain in the foreground; with unvalidated call-audio
settings, the displayed result remains **Can’t tell**.

The web app's **Call detection** panel also has a **Run backend stream test**
button. Open the app from the FastAPI server (normally `http://127.0.0.1:8000`)
and click it to send a simulated PCM chunk through the backend streaming
pipeline. This confirms that this page can reach that backend and that the
stream handler returns **Can’t tell** for unvalidated call audio. The test
uses a dummy detector; it does not use a real microphone, the configured
model, token, HTTPS/WSS connection, phone, or live call. A separate static
preview on localhost can reach the backend on port 8000; the backend allows
non-credentialed POST requests from localhost/127.0.0.1 origins for this
development workflow. If a browser reports a missing endpoint, close any old
VoiceShield server window before restarting with `start.bat`; the startup
script now checks that the expected test endpoint responds before opening the
app.

### Test an incoming Android call

Use a real Android phone with an active SIM/eSIM and a second phone to place the
test call. This development PC cannot place or receive cellular calls, and no
Android device is currently connected for testing.

1. Build the APK using the command above, or download the debug APK from the
   running website's `/api/mobile/android.apk` route.
2. On the Android phone, install the APK. For USB installation, enable Developer
   options and USB debugging, connect and authorize the phone, then run from
   `frontend\mobile\android`:

   ```powershell
   $env:ANDROID_HOME = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
   & "$env:ANDROID_HOME\platform-tools\adb.exe" devices
   & "$env:ANDROID_HOME\platform-tools\adb.exe" install -r app\build\outputs\apk\debug\app-debug.apk
   ```

   Alternatively, transfer the APK to the phone and open it there; allow
   installation from that source if Android asks.
3. Open **VoiceShield Calls**, tap **Enable call warnings**, grant Contacts and
   Notifications permissions, and accept Android's system prompt to set
   **VoiceShield Calls** as the call-screening app.
4. Verify the app says call warnings are enabled. From a second phone, call the
   Android phone once using a number saved in Contacts, then once from a number
   not saved in Contacts. The unsaved-number call should be allowed and should
   trigger a local "Number not found in contacts" notification. Dismiss or
   clear the first notification before the second test if needed.
5. To test revocation/recovery, remove Contacts or Notifications permission in
   Android Settings and confirm the app no longer claims warnings are enabled.
   Restore the permission before relying on warnings.

Android manufacturers and carrier dialers may apply their own call-screening
behavior. This test checks only contact lookup and a local notification. The
app does not listen to call audio, classify a caller as real/fake, or block
calls.

## iOS

The Swift files under `frontend/mobile/ios` are source scaffolding for a host app and a
Call Directory extension; **there is no Xcode project in this repository yet**,
so these files are not currently an installable iOS app. You need a Mac with
Xcode and an Apple Developer signing setup to create, build, and install the
required host-app and extension targets. On that Mac:

1. Create an iOS app target and a Call Directory Extension target.
2. Set the host app deployment target to iOS 16 or newer. Add
   `HostApp/SpamNumberListView.swift` and `HostApp/VoiceShieldCallsApp.swift` to
   the host app target.
3. Add `CallDirectoryExtension/CallDirectoryHandler.swift` to the extension
   target. Set its extension point to
   `com.apple.callkit.call-directory`, and set the extension bundle identifier
   to `com.voiceshield.calls.CallDirectoryExtension` or update the identifier in
   `SpamNumberListView.swift`.
4. Enable the same App Group, `group.com.voiceshield.calls`, for both targets.
5. Build and install from Xcode. Enable VoiceShield under Settings > Phone >
   Call Blocking & Identification.

6. In the installed app, add a test number with its country code. Enable the
   VoiceShield Call Directory under Settings > Phone > Call Blocking &
   Identification.
7. From a second phone, call the iPhone using that listed number and confirm
   iOS displays the extension's "VoiceShield user-reported spam" identification.
   Remove the number from the app and confirm the extension reloads.

The extension identifies only numbers manually added to the local list. iOS
does not let this extension inspect every unknown caller, display an app-run
warning for arbitrary incoming calls, or access cellular-call audio. The label
is user-entered list metadata, not a verified spam finding.

## Limitations

Neither mobile app can access direct cellular-call audio. Android has an
optional, manual speakerphone microphone experiment, but it does not yet
classify a live caller unless a separate call-audio model validation is
completed and explicitly configured. A number not found in contacts, or listed
as spam, does not prove the caller is fraudulent. The Android debug APK was
built and its package/signature verified, but no Android phone was connected
for incoming-call or speakerphone experiment testing.
The iOS source has not been built; iOS requires macOS and Xcode.

The website's **Try 500 ms live mic demo** is a separate, local microphone
streaming demo after a 4.04-second warmup. It does not receive cellular-call
audio and is not the Phase 2 live-phone-call requirement.
