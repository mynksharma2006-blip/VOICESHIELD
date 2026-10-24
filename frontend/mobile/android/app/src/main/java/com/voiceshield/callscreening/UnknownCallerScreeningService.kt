package com.voiceshield.callscreening

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract.PhoneLookup
import android.telecom.Call
import android.telecom.CallScreeningService
import android.util.Log
import org.json.JSONException

class UnknownCallerScreeningService : CallScreeningService() {
    override fun onScreenCall(callDetails: Call.Details) {
        val phoneNumber = callDetails.handle?.schemeSpecificPart
        respondToCall(
            callDetails,
            CallResponse.Builder()
                .setDisallowCall(false)
                .setRejectCall(false)
                .setSkipCallLog(false)
                .setSkipNotification(false)
                .build(),
        )
        if (phoneNumber != null && !isKnownContact(phoneNumber)) {
            try {
                CallEventStore.addUnknownCall(this, phoneNumber)
            } catch (exception: JSONException) {
                Log.e(TAG, "Could not encode the unknown call for local history.", exception)
            } catch (exception: IllegalStateException) {
                Log.e(TAG, "Could not save the unknown call in local history.", exception)
            }
            showUnknownCallerWarning()
        }
    }

    private fun isKnownContact(phoneNumber: String): Boolean {
        if (checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "Contacts permission was revoked; allowing the call without a contact lookup.")
            return true
        }

        val lookupUri = Uri.withAppendedPath(PhoneLookup.CONTENT_FILTER_URI, Uri.encode(phoneNumber))
        return contentResolver.query(lookupUri, arrayOf(PhoneLookup._ID), null, null, null)?.use {
            it.moveToFirst()
        } ?: false
    }

    private fun showUnknownCallerWarning() {
        if (
            Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            Log.w(TAG, "Notification permission is not granted; cannot show the unknown-caller warning.")
            return
        }

        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Incoming call warnings",
                NotificationManager.IMPORTANCE_HIGH,
            ),
        )
        val openAppIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val openAppPendingIntent = PendingIntent.getActivity(
            this,
            NOTIFICATION_ID,
            openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = android.app.Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_call_warning)
            .setContentTitle("Number not found in contacts")
            .setContentText("Tap to view this unknown caller in VoiceShield call history.")
            .setCategory(android.app.Notification.CATEGORY_CALL)
            .setContentIntent(openAppPendingIntent)
            .setAutoCancel(true)
            .build()
        manager.notify(System.currentTimeMillis().toInt(), notification)
    }

    private companion object {
        const val TAG = "VoiceShieldCalls"
        const val CHANNEL_ID = "unknown_call_warnings"
        const val NOTIFICATION_ID = 1
    }
}
