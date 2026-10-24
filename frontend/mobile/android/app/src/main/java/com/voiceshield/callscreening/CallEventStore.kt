package com.voiceshield.callscreening

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class UnknownCallEvent(
    val phoneNumber: String,
    val timestampMillis: Long,
)

object CallEventStore {
    private const val PREFERENCES_NAME = "call_history"
    private const val EVENTS_KEY = "unknown_calls"
    private const val MAX_EVENTS = 50

    fun addUnknownCall(context: Context, phoneNumber: String, timestampMillis: Long = System.currentTimeMillis()) {
        val events = read(context).toMutableList()
        events.add(0, UnknownCallEvent(phoneNumber, timestampMillis))
        val json = JSONArray()
        events.take(MAX_EVENTS).forEach { event ->
            json.put(
                JSONObject()
                    .put("phone_number", event.phoneNumber)
                    .put("timestamp", event.timestampMillis),
            )
        }
        val saved = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(EVENTS_KEY, json.toString())
            .commit()
        check(saved) { "Could not save the unknown-call history." }
    }

    fun read(context: Context): List<UnknownCallEvent> {
        val stored = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
            .getString(EVENTS_KEY, null)
            ?: return emptyList()
        val json = JSONArray(stored)
        return List(json.length()) { index ->
            val event = json.getJSONObject(index)
            UnknownCallEvent(
                phoneNumber = event.getString("phone_number"),
                timestampMillis = event.getLong("timestamp"),
            )
        }
    }

    fun clear(context: Context) {
        val saved = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(EVENTS_KEY)
            .commit()
        check(saved) { "Could not clear the unknown-call history." }
    }
}
