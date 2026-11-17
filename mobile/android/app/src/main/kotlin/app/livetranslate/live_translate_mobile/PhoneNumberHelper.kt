package app.livetranslate.live_translate_mobile

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Bundle
import com.google.android.gms.auth.api.identity.GetPhoneNumberHintIntentRequest
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.auth.api.phone.SmsRetriever
import com.google.android.gms.common.api.CommonStatusCodes
import com.google.android.gms.common.api.Status
import io.flutter.plugin.common.MethodChannel

/**
 * The phone's own number via Google's Phone Number Hint picker (no phone-state
 * permissions), and the SMS verification code via SMS User Consent.
 * Both answer `null` when unavailable or dismissed so Dart falls back to typing.
 */
class PhoneNumberHelper(private val activity: Activity) {
    private var pendingHint: MethodChannel.Result? = null
    private var pendingCode: MethodChannel.Result? = null
    private var smsReceiver: BroadcastReceiver? = null

    fun handle(method: String, result: MethodChannel.Result) {
        when (method) {
            "requestHint" -> requestHint(result)
            "listenForCode" -> listenForCode(result)
            "cancelCode" -> {
                finishCode(null)
                result.success(null)
            }
            else -> result.notImplemented()
        }
    }

    private fun requestHint(result: MethodChannel.Result) {
        if (pendingHint != null) {
            result.success(null)
            return
        }
        pendingHint = result
        val request = GetPhoneNumberHintIntentRequest.builder().build()
        Identity.getSignInClient(activity)
            .getPhoneNumberHintIntent(request)
            .addOnSuccessListener { pending ->
                try {
                    activity.startIntentSenderForResult(
                        pending.intentSender, REQUEST_PHONE_HINT, null, 0, 0, 0,
                    )
                } catch (e: Exception) {
                    finishHint(null)
                }
            }
            .addOnFailureListener { finishHint(null) }
    }

    private fun listenForCode(result: MethodChannel.Result) {
        finishCode(null) // only one listener at a time
        pendingCode = result
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent?.action != SmsRetriever.SMS_RETRIEVED_ACTION) return
                val extras = intent.extras ?: return finishCode(null)
                val status = extras.statusExtra()
                if (status?.statusCode != CommonStatusCodes.SUCCESS) return finishCode(null)
                val consent = extras.consentIntent() ?: return finishCode(null)
                // Only launch Google Play services' consent screen (intent-redirection guard).
                val target = consent.resolveActivity(activity.packageManager)
                if (target == null || target.packageName != "com.google.android.gms") {
                    return finishCode(null)
                }
                consent.flags = consent.flags and
                    (Intent.FLAG_GRANT_READ_URI_PERMISSION or
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION).inv()
                try {
                    activity.startActivityForResult(consent, REQUEST_SMS_CONSENT)
                } catch (e: Exception) {
                    finishCode(null)
                }
            }
        }
        smsReceiver = receiver
        val filter = IntentFilter(SmsRetriever.SMS_RETRIEVED_ACTION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            activity.registerReceiver(
                receiver, filter, SmsRetriever.SEND_PERMISSION, null, Context.RECEIVER_EXPORTED,
            )
        } else {
            activity.registerReceiver(receiver, filter, SmsRetriever.SEND_PERMISSION, null)
        }
        SmsRetriever.getClient(activity).startSmsUserConsent(null)
            .addOnFailureListener { finishCode(null) }
    }

    /** Returns true when the result belonged to this helper. */
    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?): Boolean {
        when (requestCode) {
            REQUEST_PHONE_HINT -> {
                val number = if (resultCode == Activity.RESULT_OK && data != null) {
                    try {
                        Identity.getSignInClient(activity).getPhoneNumberFromIntent(data)
                    } catch (e: Exception) {
                        null
                    }
                } else {
                    null
                }
                finishHint(number)
                return true
            }
            REQUEST_SMS_CONSENT -> {
                val message = if (resultCode == Activity.RESULT_OK) {
                    data?.getStringExtra(SmsRetriever.EXTRA_SMS_MESSAGE)
                } else {
                    null
                }
                finishCode(message?.let { Regex("\\b(\\d{6})\\b").find(it)?.groupValues?.get(1) })
                return true
            }
        }
        return false
    }

    fun dispose() {
        finishHint(null)
        finishCode(null)
    }

    private fun finishHint(number: String?) {
        val r = pendingHint
        pendingHint = null
        r?.success(number)
    }

    private fun finishCode(code: String?) {
        smsReceiver?.let {
            try {
                activity.unregisterReceiver(it)
            } catch (e: IllegalArgumentException) {
            }
        }
        smsReceiver = null
        val r = pendingCode
        pendingCode = null
        r?.success(code)
    }

    @Suppress("DEPRECATION")
    private fun Bundle.statusExtra(): Status? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getParcelable(SmsRetriever.EXTRA_STATUS, Status::class.java)
        } else {
            get(SmsRetriever.EXTRA_STATUS) as? Status
        }

    @Suppress("DEPRECATION")
    private fun Bundle.consentIntent(): Intent? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getParcelable(SmsRetriever.EXTRA_CONSENT_INTENT, Intent::class.java)
        } else {
            getParcelable(SmsRetriever.EXTRA_CONSENT_INTENT)
        }

    companion object {
        const val CHANNEL = "app.livetranslate.live_translate_mobile/phone_number"
        private const val REQUEST_PHONE_HINT = 1003
        private const val REQUEST_SMS_CONSENT = 1004
    }
}
